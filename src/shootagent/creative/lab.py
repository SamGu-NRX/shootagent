"""The creative lab's producer: runs every phase end to end on a worker thread and keeps state.json current.

    lab_id = start_lab(store, "64170ca0a0", LabOptions())   # returns at once; the lab runs in the background
    state = load_lab(store, "64170ca0a0", lab_id)            # the live state while it runs, state.json after

Order: the brief; the writers' room and the single-agent control in parallel; the template concept; the renders
(template "A", finalists "B", "C", ..., the single agent's pick "S"; at most settings.render_workers at once); the
audience and judge network; one simulated launch per reel. Everything lives in
data/shoots/<shoot>/creative/<lab id>/: state.json and reels/. state.json is rewritten atomically at most twice a
second while the lab runs, at least every couple of seconds as a heartbeat (so another process can tell a live lab
from one whose process died), and once more at the end.

A failed phase is marked failed and ends the lab, keeping everything produced so far; `start_lab(...,
resume=lab_id)` continues from there without redoing the brief, the concepts, any reel whose files exist, or a
finished evaluation. The phase modules (room, baseline, render, evaluate) are imported on first use through
PHASES, so this module imports on its own and tests replace them with fakes.
"""
from __future__ import annotations

import importlib
import itertools
import json
import os
import re
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..store import ShootStore, now_iso
from .context import Cancelled, LabContext, UsageMeter, default_client
from .schema import (AgentCard, Critique, Evaluation, LabOptions, LabPhase, LabState, Reel, ReelConcept,
                     ReelSummary, RoomMessage, Simulation)
from .simulate import simulate

LABELS = {
    "brief": "Brief", "pitch": "Pitches", "critique": "Critique", "remix": "Remix", "greenlight": "Greenlight",
    "single_agent": "Single-agent control", "render": "Render", "watch": "Audience", "judge": "Judges",
    "novelty": "Novelty", "verdict": "Verdict", "simulate": "Launch sim",
}
ROOM = ("pitch", "critique", "remix", "greenlight")  # reported by run_room
EVAL = ("watch", "judge", "novelty", "verdict")  # reported by evaluate
# Reporting a phase finishes the phases it lists: the room's phases run one after another, and so do the
# evaluation's (the audience, then the judges, the novelty scores and the verdict).
CLOSES = {"critique": ("pitch",), "remix": ("pitch", "critique"), "greenlight": ("pitch", "critique", "remix"),
          "judge": ("watch",), "novelty": ("watch", "judge"), "verdict": ("watch", "judge", "novelty")}
SIDE = {"single_agent"}  # runs beside the room; state.phase shows the room while both run
REEL_IDS = "BCDEFG"  # finalists, best first
SINGLE_AGENT = "single_agent"  # the control's agent id (baseline.single_agent_card)
SAVE_INTERVAL = 0.5  # seconds between state.json writes while running
HEARTBEAT = 2.0  # write at least this often while running
STALE_AFTER = 60.0  # a "running" state.json untouched this long belongs to a dead process
CANCEL_FILE = "cancel.request"  # lets another process (the API, the CLI) cancel a lab
_ID_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,79}$")

PRODUCER = AgentCard(id="producer", name="Producer", role="system", color="#64748b", initials="PR",
                     lens="Runs the lab: briefs the room, renders the reels, screens them for the audience and the "
                          "judges, and simulates the launch.")
TEMPLATE = AgentCard(id="template", name="Template", role="system", color="#94a3b8", initials="TP",
                     lens="The conventional listing reel: a walkthrough of the rooms with the price and address.")


class LabConflict(Exception):
    """The lab is in the wrong state for the request: still running, already finished, or not evaluated yet."""


class _PhaseFailed(Exception):
    def __init__(self, key: str, cause: BaseException):
        super().__init__(_describe(cause))
        self.key = key


def _describe(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:600]


def _lazy(module: str, name: str) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        return getattr(importlib.import_module(f"{__package__}.{module}"), name)(*args, **kwargs)

    call.__name__ = name
    return call


@dataclass
class Phases:
    """The functions the lab calls, imported when first used. Tests swap in fakes (monkeypatch lab.PHASES)."""

    room_agents: Callable[..., Any] = field(default_factory=lambda: _lazy("room", "room_agents"))
    write_brief: Callable[..., Any] = field(default_factory=lambda: _lazy("room", "write_brief"))
    run_room: Callable[..., Any] = field(default_factory=lambda: _lazy("room", "run_room"))
    template_concept: Callable[..., Any] = field(default_factory=lambda: _lazy("baseline", "template_concept"))
    single_agent_card: Callable[..., Any] = field(default_factory=lambda: _lazy("baseline", "single_agent_card"))
    single_agent_concepts: Callable[..., Any] = field(
        default_factory=lambda: _lazy("baseline", "single_agent_concepts"))
    render_concept: Callable[..., Any] = field(default_factory=lambda: _lazy("render", "render_concept"))
    evaluation_agents: Callable[..., Any] = field(default_factory=lambda: _lazy("evaluate", "evaluation_agents"))
    evaluate: Callable[..., Any] = field(default_factory=lambda: _lazy("evaluate", "evaluate"))


PHASES = Phases()


@dataclass
class _Planned:
    id: str
    concept: ReelConcept
    label: str
    kind: str


# ---- files ----------------------------------------------------------------------------------------------------


def labs_dir(store: ShootStore, shoot_id: str) -> Path:
    return store.shoot_dir(shoot_id) / "creative"


def lab_dir(store: ShootStore, shoot_id: str, lab_id: str) -> Path:
    if not _ID_RE.match(lab_id or ""):
        raise KeyError(f"no lab {lab_id!r}")
    return labs_dir(store, shoot_id) / lab_id


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read(store: ShootStore, shoot_id: str, lab_id: str) -> LabState:
    path = lab_dir(store, shoot_id, lab_id) / "state.json"
    if not path.is_file():
        raise KeyError(f"no lab {lab_id!r} on shoot {shoot_id}")
    return LabState.model_validate_json(path.read_text(encoding="utf-8"))


def _stale(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime > STALE_AFTER
    except FileNotFoundError:
        return True


def _running_elsewhere(store: ShootStore, shoot_id: str) -> str | None:
    """Id of a lab on this shoot that another process is running (its state.json is still being rewritten)."""
    root = labs_dir(store, shoot_id)
    for path in sorted(root.glob("*/state.json")) if root.is_dir() else []:
        if _live(shoot_id, path.parent.name) is not None or _stale(path):
            continue
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("status") == "running":
                return path.parent.name
        except (OSError, ValueError):
            continue
    return None


# ---- the run --------------------------------------------------------------------------------------------------


def _upsert(items: list[Any], item: Any, key: Callable[[Any], Any]) -> list[Any]:
    """`items` with `item` in place of the one sharing its key, or appended."""
    k = key(item)
    for i, x in enumerate(items):
        if key(x) == k:
            return items[:i] + [item] + items[i + 1:]
    return items + [item]


def _next_message(feed: list[RoomMessage]) -> int:
    numbers = [int(m.id[1:]) for m in feed if m.id[1:].isdigit()]
    return max(numbers, default=0) + 1


def _empty_summary(reel_id: str) -> ReelSummary:
    return ReelSummary(reel=reel_id, creativity=0, creativity_sd=0, pairwise_win_rate=0, novelty=0, stop_rate=0,
                       avg_watch_fraction=0, completion_rate=0, like_rate=0, comment_rate=0, share_rate=0,
                       save_rate=0, inquire_rate=0)


def simulate_reels(reels: list[Reel], evaluation: Evaluation, followers: int, hours: int, seed: int) -> list[Simulation]:
    """One launch per reel from the evaluation's reactions and summary for it (matched by reel or concept id)."""
    out = []
    for reel in reels:
        keys = {reel.id, reel.concept}
        summary = next((s for s in evaluation.summaries if s.reel in keys), None) or _empty_summary(reel.id)
        reactions = [r for r in evaluation.reactions if r.reel in keys]
        out.append(simulate(reel, summary, reactions, evaluation.viewers, followers=followers, hours=hours, seed=seed))
    return out


class LabRun:
    """One lab executing in this process: the state, its lock, the context the phases report through, the saver."""

    def __init__(self, store: ShootStore, state: LabState, settings: Settings, client: Any, phases: Phases):
        self.store, self.state, self.settings, self.phases = store, state, settings, phases
        self.dir = lab_dir(store, state.shoot_id, state.id)
        (self.dir / "reels").mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.meter = UsageMeter(state.usage.model_copy(deep=True))
        self.ctx = LabContext(
            store=store, shoot_id=state.shoot_id, settings=settings, lab_dir=self.dir, options=state.options,
            client=client, emit_fn=self._on_emit, progress_fn=self._on_progress, publish_fn=self._on_publish,
            usage=self.meter, t0=time.monotonic() - state.elapsed, _ids=itertools.count(_next_message(state.feed)),
        )
        self.finished = threading.Event()
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._fresh: set[str] = set()  # reels rendered by this run
        single = set(state.single_agent)
        self._room = [c for c in state.concepts if c.id not in single and c.agent != "template"]
        self._single = [c for c in state.concepts if c.id in single]
        self._template = next((c for c in state.concepts if c.agent == "template"), None)
        self._worker = threading.Thread(target=self._work, name=f"lab-{state.id}", daemon=True)
        self._saver = threading.Thread(target=self._save_loop, name=f"lab-{state.id}-save", daemon=True)

    # ---- lifecycle -------------------------------------------------------------------------------------------
    def start(self) -> None:
        (self.dir / CANCEL_FILE).unlink(missing_ok=True)
        self._write()
        self._saver.start()
        self._worker.start()

    def cancel(self) -> None:
        self.ctx.cancel.set()

    def snapshot(self) -> LabState:
        with self.lock:
            self._sync()
            return self.state.model_copy(deep=True)

    def _work(self) -> None:
        status, error = "failed", None
        try:
            self._pipeline()
            status = "done"
        except Cancelled:
            status = "cancelled"
        except _PhaseFailed as e:
            error = f"{LABELS.get(e.key, e.key)} failed: {e}"
            self._log(e.__cause__ or e)
        except Exception as e:  # a bug in the producer itself: still end the lab cleanly
            error = _describe(e)
            self._log(e)
        if status == "failed" and self.ctx.cancel.is_set():  # whatever a phase raised while stopping, it was asked to
            status, error = "cancelled", None
        try:
            self._finish(status, error)
        finally:
            self.finished.set()

    def _finish(self, status: str, error: str | None) -> None:
        with self.lock:
            for p in self.state.phases:
                if p.status == "running":
                    p.status, p.ended = "failed", self.ctx.elapsed
                    p.detail = "Cancelled." if status == "cancelled" else (p.detail or "Stopped.")
            self.state.status, self.state.error = status, error
        if status == "cancelled":
            self._say("Cancelled. Resume the lab to continue from here.")
        elif status == "failed":
            self._say(f"Stopped: {error}")
        self._stop.set()
        if self._saver.is_alive():
            self._saver.join()
        self._write()
        (self.dir / CANCEL_FILE).unlink(missing_ok=True)

    def _log(self, e: BaseException) -> None:
        try:
            with open(self.dir / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"--- {now_iso()}\n" + "".join(traceback.format_exception(e)))
        except OSError:
            pass

    # ---- persistence -----------------------------------------------------------------------------------------
    def _sync(self) -> None:
        self.state.elapsed = self.ctx.elapsed
        with self.meter._lock:  # the meter is updated from model-call threads
            self.state.usage = self.meter.usage.model_copy(deep=True)

    def _write(self) -> None:
        with self._write_lock:
            with self.lock:
                self._sync()
                text = self.state.model_dump_json()
            _atomic_write(self.dir / "state.json", text)

    def _save_loop(self) -> None:
        last_write, last_calls = time.monotonic(), self.meter.usage.calls
        while not self._stop.wait(SAVE_INTERVAL):
            if (self.dir / CANCEL_FILE).exists():
                self.cancel()
            calls = self.meter.usage.calls
            if self._dirty.is_set() or calls != last_calls or time.monotonic() - last_write >= HEARTBEAT:
                self._dirty.clear()
                try:
                    self._write()
                except OSError as e:  # a full disk must not kill the lab; the next write tries again
                    self._log(e)
                last_write, last_calls = time.monotonic(), calls

    # ---- callbacks from the phases ---------------------------------------------------------------------------
    def _on_emit(self, msg: RoomMessage) -> None:
        if '\\"' in msg.text:  # some answers arrive with their quotes still JSON-escaped; show plain quotes
            msg = msg.model_copy(update={"text": msg.text.replace('\\"', '"')})
        with self.lock:
            self.state.feed.append(msg)
        self._dirty.set()

    def _on_progress(self, key: str, detail: str) -> None:
        with self.lock:
            phase = self._phase(key)
            if phase.status in ("pending", "failed"):
                for earlier in CLOSES.get(key, ()):
                    if self._phase(earlier).status == "running":
                        self._set(self._phase(earlier), "done")
                self._set(phase, "running")
            phase.detail = detail
            self._headline()
        self._dirty.set()

    def _on_publish(self, kind: str, items: list[Any]) -> None:
        """Work handed over before its phase returns (ctx.publish), so the dashboard shows concept cards while the
        room talks and a cancelled lab keeps them: concepts upserted by id (the single agent's by its agent id go
        to the control), critiques by critic and concept, finalists replaced. The phase's result wins when it
        returns."""
        with self.lock:
            if kind == "concepts":
                for c in items:
                    c = c if isinstance(c, ReelConcept) else ReelConcept.model_validate(c)
                    if c.agent == SINGLE_AGENT:
                        self._single = _upsert(self._single, c, lambda x: x.id)
                    elif c.agent == TEMPLATE.id:
                        self._template = c
                    else:
                        self._room = _upsert(self._room, c, lambda x: x.id)
                self.state.single_agent = [c.id for c in self._single]
                self._rebuild_concepts()
            elif kind == "critiques":
                for k in items:
                    k = k if isinstance(k, Critique) else Critique.model_validate(k)
                    self.state.critiques = _upsert(self.state.critiques, k, lambda x: (x.critic, x.concept))
            elif kind == "finalists":
                self.state.finalists = [str(i) for i in items]
            else:
                return
        self._dirty.set()

    def _say(self, text: str, concept: str | None = None) -> None:
        self.ctx.emit(PRODUCER.id, "system", text, concept=concept)

    # ---- phase bookkeeping (call with the lock held) ---------------------------------------------------------
    def _phase(self, key: str) -> LabPhase:
        for p in self.state.phases:
            if p.key == key:
                return p
        p = LabPhase(key=key, label=LABELS.get(key, key.replace("_", " ").capitalize()))
        self.state.phases.append(p)
        return p

    def _set(self, phase: LabPhase, status: str, detail: str | None = None) -> None:
        if status == "running":
            phase.started, phase.ended = self.ctx.elapsed, None
        elif status in ("done", "failed"):
            phase.ended = self.ctx.elapsed
        phase.status = status
        if detail is not None:
            phase.detail = detail

    def _headline(self) -> None:
        running = [p for p in self.state.phases if p.status == "running"]
        main = [p for p in running if p.key not in SIDE] or running
        if main:
            self.state.phase = max(main, key=lambda p: p.started or 0.0).key

    def _mark(self, key: str, status: str, detail: str | None = None) -> None:
        with self.lock:
            self._set(self._phase(key), status, detail)
            self._headline()
        self._dirty.set()

    def _settled(self, keys: tuple[str, ...]) -> bool:
        with self.lock:
            statuses = [self._phase(k).status for k in keys]
        return all(s in ("done", "skipped") for s in statuses) and "done" in statuses

    def _close(self, keys: tuple[str, ...], failed: str | None = None) -> str:
        """End a group of phases the module reports itself. On success running ones are done and the ones it never
        reported are skipped; on failure the running ones (or the first unfinished one) fail. Returns the key
        that failed, or ''."""
        with self.lock:
            phases = [self._phase(k) for k in keys]
            if failed is None:
                for p in phases:
                    if p.status in ("running", "pending"):
                        self._set(p, "done" if p.status == "running" else "skipped")
                key = ""
            else:
                hit = [p for p in phases if p.status == "running"] or \
                      [next((p for p in phases if p.status not in ("done", "skipped")), phases[0])]
                for p in hit:
                    self._set(p, "failed", failed)
                key = hit[0].key
            self._headline()
        self._dirty.set()
        return key

    def _run_phase(self, key: str, fn: Callable[[], Any], keep: Callable[[Any], None] | None = None) -> Any:
        """A phase this module bounds: running, then done (after `keep` stores the result) or failed."""
        self._mark(key, "running")
        try:
            out = fn()
        except Cancelled:
            self._mark(key, "failed", "Cancelled.")
            raise
        except Exception as e:
            self._mark(key, "failed", _describe(e))
            raise _PhaseFailed(key, e) from e
        with self.lock:
            if keep is not None:
                keep(out)
            self._set(self._phase(key), "done")
            self._headline()
        self._dirty.set()
        return out

    def _run_group(self, keys: tuple[str, ...], fn: Callable[[], Any], keep: Callable[[Any], None]) -> Any:
        """A call that reports its own phases (the room, the evaluation)."""
        try:
            out = fn()
        except Cancelled:
            self._close(keys, failed="Cancelled.")
            raise
        except Exception as e:
            raise _PhaseFailed(self._close(keys, failed=_describe(e)), e) from e
        with self.lock:
            keep(out)
        self._close(keys)
        return out

    # ---- the pipeline ----------------------------------------------------------------------------------------
    def _add_agents(self, cards: list[AgentCard]) -> None:
        with self.lock:
            index = {a.id: i for i, a in enumerate(self.state.agents)}
            for card in cards:
                if card.id in index:
                    self.state.agents[index[card.id]] = card
                else:
                    index[card.id] = len(self.state.agents)
                    self.state.agents.append(card)
        self._dirty.set()

    def _rebuild_concepts(self) -> None:
        self.state.concepts = self._room + self._single + ([self._template] if self._template else [])

    def _pipeline(self) -> None:
        st, ctx, p = self.state, self.ctx, self.phases
        if st.brief is None:
            def brief() -> Any:
                cards = list(p.room_agents(ctx))
                if st.options.single_agent:
                    cards.append(p.single_agent_card())
                self._add_agents(cards + [PRODUCER, TEMPLATE])
                return p.write_brief(ctx)

            self._run_phase("brief", brief, keep=lambda b: setattr(st, "brief", b))
        ctx.check_cancel()
        self._concepts(st.brief)
        ctx.check_cancel()
        reels = self._render()
        ctx.check_cancel()
        evaluation = self._evaluate(reels)
        ctx.check_cancel()
        self._simulate(reels, evaluation)

    def _concepts(self, brief: Any) -> None:
        jobs = []
        if not self._settled(ROOM):
            jobs.append(self._writers_room)
        if self.state.options.single_agent and not self._settled(("single_agent",)):
            jobs.append(self._single_agent)
        errors: list[Exception] = []
        with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
            for future in [pool.submit(job, brief) for job in jobs]:
                try:
                    future.result()
                except Exception as e:  # both finish, so a failure on one side keeps the other's work
                    errors.append(e)
        if errors:
            raise next((e for e in errors if not isinstance(e, Cancelled)), errors[0])

    def _writers_room(self, brief: Any) -> None:
        def keep(result: Any) -> None:
            self._add_agents(list(getattr(result, "agents", None) or []))
            self._room = list(result.concepts)
            self.state.critiques = list(result.critiques)
            self.state.finalists = list(result.finalists)
            self._rebuild_concepts()

        self._run_group(ROOM, lambda: self.phases.run_room(self.ctx, brief), keep)

    def _single_agent(self, brief: Any) -> None:
        def keep(out: Any) -> None:
            concepts, pick = out
            ids = [c.id for c in concepts]
            first = pick if pick in ids else (ids[0] if ids else None)
            order = ([first] if first else []) + [i for i in ids if i != first]
            self._single = sorted(concepts, key=lambda c: order.index(c.id))  # the agent's own pick first
            self.state.single_agent = order
            self._rebuild_concepts()

        self._run_phase("single_agent", lambda: self.phases.single_agent_concepts(self.ctx, brief), keep)

    def _plan(self) -> list[_Planned]:
        opts, st = self.state.options, self.state
        plan = [_Planned("A", self._template, "Template", "baseline")]
        by_id = {c.id: c for c in self._room}
        finalists = [by_id[f] for f in st.finalists if f in by_id][: max(0, opts.finalists)]
        for i, concept in enumerate(finalists[: len(REEL_IDS)]):
            label = ("Showrunner's pick", "Wildcard")[i] if i < 2 else f"Finalist {i + 1}"
            plan.append(_Planned(REEL_IDS[i], concept, label, "creative"))
        if opts.render_single_agent and self._single:
            plan.append(_Planned("S", self._single[0], "Single agent", "single_agent"))
        return plan

    def _ready(self, reel: Reel) -> bool:
        return bool(reel.video and reel.poster) and (self.dir / reel.video).is_file() and \
            (self.dir / reel.poster).is_file()

    def _render(self) -> list[Reel]:
        def run() -> list[Reel]:
            template = self.phases.template_concept(self.ctx)
            with self.lock:
                self._template = template
                self._rebuild_concepts()
            plan = self._plan()
            order = {p.id: i for i, p in enumerate(plan)}
            with self.lock:
                kept = {r.id: r for r in self.state.reels if r.id in order and self._ready(r)}
                self.state.reels = [kept[p.id] for p in plan if p.id in kept and kept[p.id].concept == p.concept.id]
                done_ids = {r.id for r in self.state.reels}
            todo = [p for p in plan if p.id not in done_ids]
            if todo:
                self._say("Rendering " + ", ".join(f"{p.id} ({p.label}: {p.concept.title})" for p in todo) + ".")
            self._progress_render(len(plan) - len(todo), len(plan))
            errors: list[str] = []
            with ThreadPoolExecutor(max_workers=max(1, self.settings.render_workers)) as pool:
                futures = {pool.submit(self._render_one, p): p for p in todo}
                for future in as_completed(futures):
                    planned = futures[future]
                    try:
                        reel = future.result()
                    except Cancelled:
                        continue
                    except Exception as e:  # the other renders finish; a resume redoes only this one
                        errors.append(f"{planned.id}: {_describe(e)}")
                        self._log(e)
                        continue
                    self._keep_reel(reel, planned, order)
            self.ctx.check_cancel()
            if errors:
                raise RuntimeError("; ".join(errors))
            with self.lock:
                return [r.model_copy(deep=True) for r in self.state.reels]

        return self._run_phase("render", run)

    def _render_one(self, p: _Planned) -> Reel:
        self.ctx.check_cancel()
        reel = self.phases.render_concept(self.ctx, p.concept.model_copy(deep=True), p.id, p.label, p.kind,
                                          voice=self.state.options.voice)
        self._fresh.add(p.id)
        return reel

    def _keep_reel(self, reel: Reel, planned: _Planned, order: dict[str, int]) -> None:
        with self.lock:
            self.state.reels = sorted([r for r in self.state.reels if r.id != reel.id] + [reel],
                                      key=lambda r: order.get(r.id, len(order)))
            planned.concept.status = "rendered"
            self._phase("render").detail = f"{len(self.state.reels)} of {len(order)} reels rendered"
        bits = [f"{reel.seconds:.1f} s", "voice" if reel.has_voice else "no voice"]
        if reel.cards:
            bits.append(f"{len(reel.cards)} card{'s' if len(reel.cards) > 1 else ''}")
        self._say(f"Reel {reel.id} rendered: {reel.title} ({', '.join(bits)}).", concept=reel.concept)

    def _progress_render(self, done: int, total: int) -> None:
        with self.lock:
            self._phase("render").detail = f"{done} of {total} reels rendered"
        self._dirty.set()

    def _evaluate(self, reels: list[Reel]) -> Evaluation:
        st = self.state
        if st.evaluation is not None and not self._fresh and self._settled(EVAL):
            return st.evaluation
        with self.lock:
            for k in EVAL:  # a re-run after new renders starts the group over
                self._set(self._phase(k), "pending", "")
                self._phase(k).started = self._phase(k).ended = None

        def run() -> Evaluation:
            self._add_agents(list(self.phases.evaluation_agents()))
            with self.lock:  # every concept in the lab (room, single agent, template), and the control's own
                concepts = [c.model_copy(deep=True) for c in self.state.concepts]
                single = [c.model_copy(deep=True) for c in self._single]
            return self.phases.evaluate(self.ctx, reels, concepts, single)

        return self._run_group(EVAL, run, keep=lambda ev: setattr(st, "evaluation", ev))

    def _simulate(self, reels: list[Reel], evaluation: Evaluation) -> None:
        opts = self.state.options

        def keep(sims: list[Simulation]) -> None:
            self.state.simulations = sims
            if sims:
                best = max(sims, key=lambda s: s.totals.views)
                self._phase("simulate").detail = f"{best.reel} travels furthest: {best.totals.views:,} views"

        sims = self._run_phase(
            "simulate", lambda: simulate_reels(reels, evaluation, opts.followers, opts.hours, opts.seed), keep)
        if sims:
            ranked = sorted(sims, key=lambda s: s.totals.views, reverse=True)
            views = ", ".join(f"{s.reel} {s.totals.views:,}" for s in ranked)
            self._say(f"Launch sim, {opts.followers:,} followers, {opts.hours} h: {views} views. "
                      f"Reel {ranked[0].reel} travels furthest.")


# ---- public API -----------------------------------------------------------------------------------------------

_RUNS: dict[str, LabRun] = {}
_RUNS_LOCK = threading.Lock()
_FILE_LOCKS: dict[str, threading.Lock] = {}


def _live(shoot_id: str, lab_id: str) -> LabRun | None:
    run = _RUNS.get(lab_id)
    return run if run is not None and run.state.shoot_id == shoot_id and not run.finished.is_set() else None


def _busy(store: ShootStore, shoot_id: str) -> str | None:
    """Id of a lab still running on this shoot of this store, in this process or another."""
    here = next((r.state.id for r in _RUNS.values() if r.state.shoot_id == shoot_id and not r.finished.is_set()
                 and r.store.root.resolve() == store.root.resolve()), None)
    return here or _running_elsewhere(store, shoot_id)


def _file_lock(shoot_id: str, lab_id: str) -> threading.Lock:
    with _RUNS_LOCK:
        return _FILE_LOCKS.setdefault(f"{shoot_id}/{lab_id}", threading.Lock())


def _validate(options: LabOptions) -> None:
    if not 0 <= options.finalists <= len(REEL_IDS):
        raise ValueError(f"finalists must be between 0 and {len(REEL_IDS)}.")
    if not 1 <= options.rounds <= 3:
        raise ValueError("rounds must be 1 (pitch only), 2 (pitch, critique, remix) or 3.")
    if options.creators is not None:
        try:
            from .personas import CREATORS, creators
        except ImportError:  # the room's module is not there yet; it reports bad ids itself
            CREATORS, creators = None, None
        if not options.creators or (creators is not None and not creators(options.creators)):
            known = ", ".join(p.id for p in CREATORS) if CREATORS else "see creative/personas.py"
            raise ValueError(f"creators must name at least one known creator ({known}), or be null for all of them.")
    _validate_sim(options.followers, options.hours)


def _validate_sim(followers: int, hours: int) -> None:
    if not 0 <= followers <= 10_000_000:
        raise ValueError("followers must be between 0 and 10,000,000.")
    if not 1 <= hours <= 720:
        raise ValueError("hours must be between 1 and 720.")


def _new_state(store: ShootStore, shoot_id: str, options: LabOptions) -> LabState:
    shoot = store.load(shoot_id)
    phases = [LabPhase(key=k, label=v) for k, v in LABELS.items()]
    if not options.single_agent:
        phases[list(LABELS).index("single_agent")].status = "skipped"
    return LabState(id=time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], shoot_id=shoot_id,
                    title=shoot.listing.address or shoot.title, created=now_iso(), phases=phases,
                    options=options.model_copy(deep=True))


def _resumable(store: ShootStore, shoot_id: str, lab_id: str) -> LabState:
    state = _read(store, shoot_id, lab_id)
    path = lab_dir(store, shoot_id, lab_id) / "state.json"
    if state.status == "done":
        raise LabConflict(f"Lab {lab_id} already finished; start a new lab instead.")
    if state.status == "running" and not _stale(path):
        raise LabConflict(f"Lab {lab_id} is still running.")
    for p in state.phases:
        if p.status in ("running", "failed"):
            p.status, p.started, p.ended, p.detail = "pending", None, None, ""
    state.status, state.error = "running", None
    return state


def start_lab(store: ShootStore, shoot_id: str, options: LabOptions, settings: Settings | None = None,
              client: Any = None, resume: str | None = None) -> str:
    """Start a lab (or resume a failed or cancelled one, with its stored options) and return its id at once.
    Raises KeyError for an unknown shoot or lab, ValueError for bad options, LabConflict when a lab is already
    running on the shoot, and RuntimeError when no Claude credentials are configured."""
    settings = settings or get_settings()
    store.load(shoot_id)
    with _RUNS_LOCK:
        busy = _busy(store, shoot_id)
        if busy:
            raise LabConflict(f"Lab {busy} is still running on this shoot; cancel it or wait for it to finish.")
        if resume:
            state = _resumable(store, shoot_id, resume)
        else:
            _validate(options)
            state = _new_state(store, shoot_id, options)
        run = LabRun(store, state, settings, client if client is not None else default_client(settings), PHASES)
        _RUNS[state.id] = run
    run.start()
    return state.id


def load_lab(store: ShootStore, shoot_id: str, lab_id: str) -> LabState:
    """The lab's state: live from memory while this process runs it, else from state.json. A "running" state
    whose process stopped writing is reported as failed."""
    run = _live(shoot_id, lab_id)
    if run is not None:
        return run.snapshot()
    state = _read(store, shoot_id, lab_id)
    if state.status == "running" and _stale(lab_dir(store, shoot_id, lab_id) / "state.json"):
        state.status = "failed"
        state.error = state.error or "The lab stopped without finishing (its process ended). Resume it to continue."
    return state


def list_labs(store: ShootStore, shoot_id: str) -> list[dict]:
    """Newest first: id, title, created, status, phase, reels (count), usd, elapsed."""
    root = labs_dir(store, shoot_id)
    out = []
    for path in root.glob("*/state.json") if root.is_dir() else []:
        try:
            st = load_lab(store, shoot_id, path.parent.name)
        except (KeyError, OSError, ValueError):  # a half-written or foreign folder must not break the list
            continue
        out.append({"id": st.id, "title": st.title, "created": st.created, "status": st.status, "phase": st.phase,
                    "reels": len(st.reels), "usd": round(st.usage.usd, 4), "elapsed": st.elapsed})
    return sorted(out, key=lambda x: x["created"], reverse=True)


def cancel_lab(store: ShootStore, shoot_id: str, lab_id: str) -> bool:
    """Ask a running lab to stop at its next step (in this process or another). False if it is not running."""
    run = _live(shoot_id, lab_id)
    if run is not None:
        run.cancel()
        return True
    if load_lab(store, shoot_id, lab_id).status == "running":
        (lab_dir(store, shoot_id, lab_id) / CANCEL_FILE).touch()
        return True
    return False


def wait_lab(lab_id: str, timeout: float | None = None) -> bool:
    """Block until a lab started by this process has ended; True if it ended within `timeout`."""
    run = _RUNS.get(lab_id)
    return True if run is None else run.finished.wait(timeout)


def resimulate(store: ShootStore, shoot_id: str, lab_id: str, followers: int, hours: int, seed: int) -> list[Simulation]:
    """Re-run every reel's launch with new parameters and save them (and the parameters) in state.json."""
    _validate_sim(followers, hours)
    with _file_lock(shoot_id, lab_id):
        if _live(shoot_id, lab_id) is not None:
            raise LabConflict(f"Lab {lab_id} is still running; simulate after it finishes.")
        state = load_lab(store, shoot_id, lab_id)
        if state.status == "running":
            raise LabConflict(f"Lab {lab_id} is still running; simulate after it finishes.")
        if state.evaluation is None or not state.reels:
            raise LabConflict(f"Lab {lab_id} has no evaluated reels to simulate yet.")
        sims = simulate_reels(state.reels, state.evaluation, followers, hours, seed)
        state.simulations = sims
        state.options.followers, state.options.hours, state.options.seed = followers, hours, seed
        _atomic_write(lab_dir(store, shoot_id, lab_id) / "state.json", state.model_dump_json())
    return sims
