"""The Claude tool-use loop for QC, chat, reel and film runs.

`start_run` returns a TraceRun at once and runs the agent in a worker thread; one run per shoot is active
at a time and later ones queue. The loop owns three rules the model cannot bypass:

1. `submit_plan` comes first: any other tool before it is refused.
2. Every edit that writes a version is verified (agent/verify.py). A failed check moves the view's head back
   to the parent, emits `check` and `retry`, and tells the model to try something different. Failed checks
   per view are capped at `settings.max_fix_attempts`, after which edits on that view are refused.
3. Edits stay inside the run's views.

History is append-only (assistant turns are passed back unchanged, including progress-note thinking blocks),
so the cached prefix and the model's thinking blocks stay valid from turn to turn.
"""

from __future__ import annotations

import json
import re
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..store import ShootStore
from ..tools import REGISTRY, ToolContext, ToolResult, run_tool
from ..tools._common import image_block
from ..trace import NullTrace, TraceRun, read_events
from .usage import usage_event
from .verify import verify_edit

PROMPTS = Path(__file__).parent / "prompts"
LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")  # film languages become folder names; nothing else gets through
MAX_IMAGES_PER_RESULT = 4
MAX_NUDGES = 2

MODE_TOOLS: dict[str, list[str]] = {
    "qc": [
        "submit_plan",
        "list_views",
        "view_photo",
        "run_qc_checks",
        "find_regions",
        "mask_from_box",
        "combine_masks",
        "grow_mask",
        "sample_color",
        "restore_from_brackets",
        "remove_object",
        "recolor_region",
        "adjust_exposure",
        "adjust_white_balance",
        "generative_fill",
        "regroup_brackets",
        "revert",
        "set_finding",
        "label_views",
    ],
    "chat": [
        "submit_plan",
        "list_views",
        "view_photo",
        "find_regions",
        "mask_from_box",
        "combine_masks",
        "grow_mask",
        "sample_color",
        "adjust_exposure",
        "adjust_white_balance",
        "recolor_region",
        "remove_object",
        "generative_fill",
        "restore_from_brackets",
        "place_on_screen",
        "add_watermark",
        "draw_lot_lines",
        "revert",
        "report_view_result",
        "set_listing",
    ],
    "reel": ["submit_plan", "list_views", "view_photo", "label_views", "render_reel", "set_listing"],
    "film": [
        "submit_plan",
        "list_views",
        "view_photo",
        "label_views",
        "plan_film",
        "prepare_shots",
        "write_script",
        "translate_script",
        "narrate",
        "assemble_film",
    ],
}


class Cancelled(Exception):
    pass


def system_prompt(mode: str) -> str:
    return (
        (PROMPTS / "common.md").read_text(encoding="utf-8")
        + "\n\n"
        + (PROMPTS / f"{mode}.md").read_text(encoding="utf-8")
    )


def _inline_refs(schema: dict) -> dict:
    """Replace pydantic `$ref`s with their definitions so every tool schema is self-contained."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def tool_schemas(mode: str) -> list[dict]:
    out = []
    for name in MODE_TOOLS[mode]:
        s = REGISTRY[name].anthropic_schema()
        s["input_schema"] = _inline_refs(s["input_schema"])
        out.append(s)
    return out


@dataclass
class Counts:
    edits_applied: int = 0
    checks_failed: int = 0
    tool_calls: int = 0
    model_calls: int = 0
    refused: int = 0


@dataclass
class AgentRun:
    store: ShootStore
    shoot_id: str
    mode: str
    trace: TraceRun
    settings: Settings
    client: Any
    request: str | None = None
    views: list[str] | None = None
    mask: str | None = None
    languages: list[str] | None = None  # film runs: narration languages, the first is the script's own
    cancel: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self):
        if self.mode not in MODE_TOOLS:
            raise ValueError(f"mode must be one of {sorted(MODE_TOOLS)}, not {self.mode!r}")
        if self.mode == "chat" and not (self.request or "").strip():
            raise ValueError("A chat run needs a request message.")
        shoot = self.store.load(self.shoot_id)
        if self.mask and self.mask not in shoot.masks:
            raise ValueError(f"Shoot {self.shoot_id} has no mask {self.mask!r}.")
        all_ids = [v.id for v in shoot.views]
        if self.views:
            unknown = [v for v in self.views if v not in all_ids]
            if unknown:
                raise ValueError(f"Unknown view(s) {', '.join(unknown)}; views are {', '.join(all_ids)}.")
            scope = list(self.views)
        elif self.mask:
            scope = [shoot.masks[self.mask].view]
        elif self.mode in ("reel", "film"):
            scope = [v.id for v in shoot.views if v.approved and v.head] or [v.id for v in shoot.views if v.head]
        else:
            scope = all_ids
        self.scope: list[str] = scope
        if self.mode == "film":
            self.languages = [lang.strip().lower() for lang in (self.languages or ["en"])]
            bad = [lang for lang in self.languages if not LANG_RE.match(lang)]
            if bad or not self.languages or len(set(self.languages)) != len(self.languages):
                raise ValueError("A film run needs one or more distinct language codes such as 'en' or 'es'"
                                 + (f", not {', '.join(map(repr, bad))}." if bad else "."))
            empty = [v for v in scope if shoot.view(v).head is None]
            if empty:
                raise ValueError(f"View(s) {', '.join(empty)} have no photo yet and cannot be filmed.")
        self.allowed: set[str] = set(scope)
        self.plan_done = False
        self.failures: dict[str, int] = {}
        self.counts = Counts()
        self.messages: list[dict] = []
        self._lock = threading.Lock()
        self._tools = tool_schemas(self.mode)
        self._system = [
            {"type": "text", "text": system_prompt(self.mode), "cache_control": {"type": "ephemeral"}}
        ]
        self._final_text = ""

    # ---- public ------------------------------------------------------------------------------------------
    def run(self) -> None:
        ok, summary = False, ""
        self.trace.emit(
            "run_start",
            mode=self.mode,
            request=self.request,
            views=self.scope,
            mask=self.mask,
            model=self.settings.agent_model,
            **({"languages": self.languages, "media": self.settings.film_media} if self.mode == "film" else {}),
        )
        try:
            self._loop()
            ok, summary = True, self._final_text or "Done."
        except Cancelled:
            summary = "Cancelled."
        except Exception as e:  # surfaced in the trace; the run ends instead of crashing the server
            self.trace.emit("error", message=f"{type(e).__name__}: {e}")
            summary = f"Stopped: {type(e).__name__}: {e}"
        self.trace.emit(
            "run_end",
            ok=ok,
            summary=summary[:600],
            ms=self.trace.elapsed_ms,
            usd=round(self.trace.usd, 4),
            counts=self._final_counts(),
        )

    # ---- loop --------------------------------------------------------------------------------------------
    def _loop(self) -> None:
        if self.mode == "qc":
            from ..tools import _ops
            from ..tools.perceive import bracket_problem

            shoot = self.store.load(self.shoot_id)
            # Same guard as run_qc_checks: views without real brackets are never analysed.
            _ops.prewarm_analysis(
                self.store, self.shoot_id, [v for v in self.scope if bracket_problem(shoot.view(v)) is None]
            )
        if self.mode == "film":
            from ..tools.film import start_film

            start_film(ToolContext(self.store, self.shoot_id, self.trace, self.settings), self.scope, self.languages)
        self.messages.append({"role": "user", "content": self._opening()})
        nudges = 0
        for _ in range(self.settings.max_agent_steps):
            self._check_cancel()
            resp = self._call()
            self.messages.append({"role": "assistant", "content": resp.content})
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            self._emit_text(resp.content, final=not tool_uses)
            if resp.stop_reason == "refusal":
                raise RuntimeError("the model declined the request")
            if tool_uses:
                self.messages.append({"role": "user", "content": self._run_tools(tool_uses)})
                continue
            if resp.stop_reason == "max_tokens":
                self.messages.append({"role": "user", "content": "Your reply was cut off. Continue."})
                continue
            missing = self._unfinished()
            if missing and nudges < MAX_NUDGES:
                nudges += 1
                self.trace.emit("thought", text=f"(loop) Not finished yet: {missing}")
                self.messages.append({"role": "user", "content": f"Before you finish: {missing}"})
                continue
            return
        raise RuntimeError(f"step limit reached ({self.settings.max_agent_steps} model calls)")

    def _mark_cache(self) -> None:
        """Keep one message breakpoint, on the last block of the newest user turn.

        With the system breakpoint (tools render before system, so it caches them too) that is two of the
        API's four. Top-level automatic caching is not used because the local gateway already places a 1h
        breakpoint on the last block and rejects a second TTL on it. Moving the marker does not change
        message content, so the cached prefix stays valid."""
        for msg in self.messages[:-1]:
            if msg["role"] == "user" and isinstance(msg["content"], list):
                msg["content"][-1].pop("cache_control", None)
        last = self.messages[-1]
        if last["role"] == "user":
            if isinstance(last["content"], str):
                last["content"] = [{"type": "text", "text": last["content"]}]
            last["content"][-1]["cache_control"] = {"type": "ephemeral"}

    def _call(self):
        self._mark_cache()
        kwargs: dict[str, Any] = dict(
            model=self.settings.agent_model,
            max_tokens=8000,
            system=self._system,
            tools=self._tools,
            messages=self.messages,
            output_config={"effort": self.settings.agent_effort},
        )
        if self.settings.agent_model.startswith("claude-sonnet-5-5"):
            # Lowest thinking setting: no extended thinking, progress notes come back with their text.
            kwargs["thinking"] = {"type": "between_tools"}
        resp = self.client.messages.create(**kwargs)
        self.counts.model_calls += 1
        self.trace.emit("usage", purpose="agent", **usage_event(self.settings.agent_model, resp.usage))
        return resp

    def _emit_text(self, content, final: bool) -> None:
        for b in content:
            text = (
                getattr(b, "text", None)
                if b.type == "text"
                else getattr(b, "thinking", None)
                if b.type == "thinking"
                else None
            )
            if not text or not text.strip():
                continue
            if final and b.type == "text":
                self._final_text = text.strip()
                self.trace.emit("message", text=text.strip())
            else:
                self.trace.emit("thought", text=text.strip())

    # ---- tools -------------------------------------------------------------------------------------------
    def _run_tools(self, blocks) -> list[dict]:
        results: dict[str, dict] = {}
        first = [b for b in blocks if b.name == "submit_plan" or not (b.input or {}).get("view")]
        first_ids = {b.id for b in first}
        by_view: dict[str, list] = {}
        for b in blocks:
            if b.id not in first_ids:
                by_view.setdefault(b.input["view"], []).append(b)
        for b in first:
            self._check_cancel()
            results[b.id] = self._run_one(b)

        def run_group(group):
            for b in group:
                self._check_cancel()
                results[b.id] = self._run_one(b)

        if len(by_view) <= 1:
            for group in by_view.values():
                run_group(group)
        else:
            with ThreadPoolExecutor(max_workers=max(1, self.settings.tool_workers)) as pool:
                for f in [pool.submit(run_group, g) for g in by_view.values()]:
                    f.result()
        return [results[b.id] for b in blocks]

    def _refuse(self, block, message: str) -> dict:
        view = (block.input or {}).get("view")
        self.trace.emit("tool_call", view=view, call_id=block.id, tool=block.name, input=block.input)
        self.trace.emit(
            "tool_result",
            view=view,
            call_id=block.id,
            tool=block.name,
            ok=False,
            summary=message,
            version=None,
            mask=None,
            images=[],
            data={"refused": True},
            ms=0,
        )
        with self._lock:
            self.counts.refused += 1
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "is_error": True,
            "content": [{"type": "text", "text": message}],
        }

    def _run_one(self, block) -> dict:
        name, inp = block.name, dict(block.input or {})
        view = inp.get("view")
        spec = REGISTRY.get(name)
        if name not in MODE_TOOLS[self.mode] or spec is None:
            return self._refuse(block, f"{name} is not available in {self.mode} mode.")
        if not self.plan_done and name != "submit_plan":
            return self._refuse(block, "Call submit_plan first, then continue with this call.")
        if view and view not in self.allowed:
            return self._refuse(
                block,
                f"View {view} is outside this run's scope ({', '.join(sorted(self.allowed))}). "
                "Do not touch it.",
            )
        if spec.kind == "edit" and view and self.failures.get(view, 0) >= self.settings.max_fix_attempts:
            return self._refuse(
                block,
                f"{view} has used all {self.settings.max_fix_attempts} fix attempts. Stop "
                "editing it and record the outcome as failed.",
            )

        ctx = ToolContext(self.store, self.shoot_id, self.trace, self.settings, call_id=block.id)
        result = run_tool(ctx, name, inp)
        with self._lock:
            self.counts.tool_calls += 1
            if name == "submit_plan" and result.ok:
                self.plan_done = True
            if name == "regroup_brackets" and result.ok:
                self.allowed.update(result.data.get("new_views", []))
                self.scope += [v for v in result.data.get("new_views", []) if v not in self.scope]

        check = None
        if result.ok and result.version and "verify" in result.data:
            check = self._verify(ctx, view, result)
        return self._result_block(ctx, block.id, result, check)

    def _verify(self, ctx: ToolContext, view: str, result: ToolResult) -> dict:
        try:
            check = verify_edit(ctx, view, result)
        except Exception as e:  # an edit nobody checked must not stay current; treat it as a failed check
            parent = result.data.get("parent") or result.data.get("verify", {}).get("parent")
            check = {
                "version": result.version,
                "parent": parent,
                "passed": False,
                "intent": result.data.get("verify", {}).get("intent"),
                "metrics": {},
                "failures": [f"the self-check itself failed ({type(e).__name__}: {e})"],
                "judge": None,
            }
        self.trace.emit("check", view=view, call_id=ctx.call_id, **check)
        if check["passed"]:
            with self._lock:
                self.counts.edits_applied += 1
            return check
        self.store.set_head(self.shoot_id, view, check["parent"])
        with self._lock:
            self.failures[view] = attempt = self.failures.get(view, 0) + 1
            self.counts.checks_failed += 1
        cap = self.settings.max_fix_attempts
        reason = "; ".join(check["failures"])
        if attempt < cap:
            nxt = "try a different tool, a different mask, or different parameters"
            self.trace.emit("retry", view=view, attempt=attempt, reason=reason, next=nxt, max_attempts=cap)
            check["instruction"] = (
                f"The edit was reverted ({view} is back on {check['parent']}). Attempt {attempt} "
                f"of {cap} failed: {reason}. Do not repeat the same call; {nxt}."
            )
        else:
            check["instruction"] = (
                f"The edit was reverted. {view} has used all {cap} attempts; stop editing it and "
                "record the outcome as failed with a short reason."
            )
        return check

    def _result_block(self, ctx: ToolContext, call_id: str, result: ToolResult, check: dict | None) -> dict:
        data = {k: v for k, v in result.data.items() if k not in ("verify", "traceback")}
        payload: dict[str, Any] = {"ok": result.ok, "summary": result.summary}
        if data:
            payload["data"] = data
        if result.mask:
            payload["mask"] = result.mask
        if check is not None:
            payload["check"] = {k: check[k] for k in ("passed", "metrics", "failures") if k in check}
            if check.get("judge"):
                payload["check"]["judge"] = {
                    k: check["judge"].get(k) for k in ("verdict", "reason", "issues")
                }
            if "instruction" in check:
                payload["instruction"] = check["instruction"]
        content: list[dict] = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}]
        # The API allows only text in an is_error result. A failed check is not a tool error: it goes back as
        # a normal result whose text says it was reverted, with the before/after image the model needs.
        if result.ok:
            for blob in result.images[:MAX_IMAGES_PER_RESULT]:
                content.append(image_block(ctx, blob))
        return {"type": "tool_result", "tool_use_id": call_id, "content": content, "is_error": not result.ok}

    # ---- framing -----------------------------------------------------------------------------------------
    def _opening(self) -> list[dict]:
        from ..tools.perceive import build_contact_sheet

        ctx = ToolContext(self.store, self.shoot_id, self.trace, self.settings)
        sheet, status = build_contact_sheet(ctx, self.scope)
        shoot = self.store.load(self.shoot_id)
        lines = [f"Shoot: {shoot.title} ({len(shoot.views)} views). Views in scope: {', '.join(self.scope)}."]
        if self.mode == "chat":
            lines.insert(0, f"Customer request: {self.request.strip()}")
        elif self.mode == "reel":
            lines.insert(0, "Assemble the listing reel from the views in scope. Listing: "
                         + shoot.listing.model_dump_json(exclude_none=True))
        elif self.mode == "film":
            from ..tools.film import film_opening

            lines[0:0] = film_opening(ctx, self.scope, self.languages)
        else:
            lines.insert(0, "Run the autonomous QC pass on the views in scope.")
        if self.mask:
            m = shoot.masks[self.mask]
            lines.append(
                f"The customer selected mask {m.id} on view {m.view} (box {m.bbox}). Apply the request to "
                "that area; use this mask unless it is clearly wrong."
            )
        if self.mode == "qc":
            from ..tools.perceive import view_order

            orders = {v: view_order(shoot, shoot.view(v)) for v in self.scope}
            lines.append(
                "What the customer ordered for each view (requested edits and order notes; 'unknown' when the "
                "order was not supplied): "
                + json.dumps({v: o or "unknown" for v, o in orders.items()}, ensure_ascii=False)
            )
        labelled = self._reference_blobs(shoot) if self.mode == "chat" else []
        refs = [blob for blob, _ in labelled]
        if labelled:
            # Photo-level references come from an earlier request on that photo (the editor dataset), not from
            # this message; saying so stops the agent thanking the customer for images they never sent.
            lines.append(
                "Reference images, shown below in this order. Shoot-level ones came with this request; ones "
                "tagged with a view were attached to that photo earlier and matter only if this request asks "
                "for a reference: "
                + ", ".join(f"{blob} ({view or 'this request'})" for blob, view in labelled)
            )
        lines.append("View status: " + json.dumps(status, ensure_ascii=False))
        lines.append("The contact sheet of the views in scope follows.")
        content: list[dict] = [{"type": "text", "text": "\n".join(lines)}, image_block(ctx, sheet)]
        for r in refs[:3]:
            try:
                content.append(image_block(ctx, r))
            except FileNotFoundError:
                pass
        return content

    def _reference_blobs(self, shoot) -> list[tuple[str, str | None]]:
        """(blob, view) for each reference image: shoot-level blobs (view None), plus files listed in a view's
        `meta["request_references"]` (editor-request ingest), copied into the store and tagged with the view."""
        refs: list[tuple[str, str | None]] = [
            (r, None) for r in (shoot.source.get("references") or []) if isinstance(r, str)
        ]
        seen = {b for b, _ in refs}
        for vid in self.scope:
            for path in shoot.view(vid).meta.get("request_references") or []:
                p = Path(path)
                if p.is_file():
                    blob = self.store.put_file(self.shoot_id, p)
                    if blob not in seen:
                        refs.append((blob, vid))
                        seen.add(blob)
        return refs

    def _unfinished(self) -> str:
        """What the run still owes before it may end, or '' when complete."""
        shoot = self.store.load(self.shoot_id)
        if not self.plan_done:
            return "call submit_plan, then do the work."
        if self.mode == "reel":
            rendered = any(
                e["kind"] == "tool_result" and e["data"].get("tool") == "render_reel" and e["data"].get("ok")
                for e in self._events()
            )
            return "" if rendered else "call render_reel to produce the reel."
        if self.mode == "film":
            # Only assemblies after the latest change to the plan, clips, scripts or audio count.
            made: set[str] = set()
            for e in self._events():
                if e["kind"] != "tool_result" or not e["data"].get("ok"):
                    continue
                if e["data"].get("tool") in ("plan_film", "prepare_shots", "write_script", "translate_script", "narrate"):
                    made = set()
                elif e["data"].get("tool") == "assemble_film":
                    made |= set((e["data"].get("data") or {}).get("languages", {}))
            missing = [lang for lang in self.languages if lang not in made]
            return f"assemble the film for {', '.join(missing)} (narrate first if needed)." if missing else ""
        if self.mode == "chat":
            missing = [
                v
                for v in self.scope
                if shoot.view(v).meta.get("instruction_results", {}).get("run") != self.trace.id
            ]
            if missing:
                return f"call report_view_result for {', '.join(missing)} (applied / not_needed / failed per view)."
            return ""
        events = self._events()
        checked = {
            e["data"]["input"].get("view")
            for e in events
            if e["kind"] == "tool_call" and e["data"].get("tool") == "run_qc_checks"
        }
        unchecked = [v for v in self.scope if v not in checked]
        if unchecked:
            return f"run_qc_checks has not run on {', '.join(unchecked)}."
        open_ = [
            f"{v}:{f.id}"
            for v in self.scope
            for f in shoot.view(v).findings
            if f.status == "open" and "agent_note" not in f.evidence
        ]
        if open_:
            return f"give every candidate a verdict with set_finding; still open: {', '.join(open_)}."
        return ""

    def _events(self) -> list[dict]:
        return list(self.trace.events) if isinstance(self.trace, NullTrace) else read_events(self.trace.path)

    def _final_counts(self) -> dict[str, Any]:
        latest: dict[str, str] = {}
        for e in self._events():
            if e["kind"] == "finding":
                f = e["data"]["finding"]
                latest[f["id"]] = f["status"]
        not_needed = 0
        for e in self._events():
            if (
                e["kind"] == "tool_result"
                and e["data"].get("tool") == "report_view_result"
                and e["data"].get("ok")
            ):
                not_needed += sum(
                    r["status"] == "not_needed" for r in e["data"].get("data", {}).get("results", [])
                )
        c = self.counts
        return {
            "findings_detected": sum(1 for s in latest.values() if s != "dismissed"),
            "findings_fixed": sum(1 for s in latest.values() if s == "fixed"),
            "findings_dismissed": sum(1 for s in latest.values() if s == "dismissed"),
            "findings_failed": sum(1 for s in latest.values() if s == "failed"),
            "edits_applied": c.edits_applied,
            "edits_reverted": c.checks_failed,
            "edits_skipped": not_needed,
            "tool_calls": c.tool_calls,
            "refused_calls": c.refused,
            "model_calls": c.model_calls,
        }

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()


# ---- run manager ------------------------------------------------------------------------------------------


@dataclass
class RunHandle:
    agent: AgentRun
    status: str = "queued"  # queued | running | done | cancelled
    thread: threading.Thread | None = None
    finished: threading.Event = field(default_factory=threading.Event)  # set once run_end is written

    @property
    def trace(self) -> TraceRun:
        return self.agent.trace


class RunManager:
    """One active run per shoot; later runs on the same shoot wait in a FIFO queue."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active: dict[str, RunHandle] = {}
        self._queues: dict[str, deque[RunHandle]] = {}
        self._runs: dict[str, RunHandle] = {}

    def start(self, agent: AgentRun) -> RunHandle:
        handle = RunHandle(agent)
        sid = agent.shoot_id
        agent.store.mutate(sid, lambda s: s.runs.append(agent.trace.id))
        with self._lock:
            self._runs[agent.trace.id] = handle
            if sid in self._active:
                self._queues.setdefault(sid, deque()).append(handle)
                return handle
            self._active[sid] = handle
        self._launch(handle)
        return handle

    def _launch(self, handle: RunHandle) -> None:
        handle.status = "running"
        handle.thread = threading.Thread(
            target=self._work, args=(handle,), name=f"run-{handle.trace.id}", daemon=True
        )
        handle.thread.start()

    def _work(self, handle: RunHandle) -> None:
        try:
            handle.agent.run()
        finally:
            handle.status = "cancelled" if handle.agent.cancel.is_set() else "done"
            handle.finished.set()
            self._next(handle.agent.shoot_id)

    def _next(self, sid: str) -> None:
        with self._lock:
            q = self._queues.get(sid)
            nxt = q.popleft() if q else None
            if nxt is None:
                self._active.pop(sid, None)
                return
            self._active[sid] = nxt
        self._launch(nxt)

    def get(self, run_id: str) -> RunHandle | None:
        return self._runs.get(run_id)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            handle = self._runs.get(run_id)
            if handle is None or handle.status in ("done", "cancelled"):
                return False
            handle.agent.cancel.set()
            q = self._queues.get(handle.agent.shoot_id)
            queued = q is not None and handle in q
            if queued:
                q.remove(handle)
                handle.status = "cancelled"
        if queued:
            handle.trace.emit(
                "run_end", ok=False, summary="Cancelled before it started.", ms=0, usd=0.0, counts={}
            )
            handle.finished.set()
        return True


MANAGER = RunManager()


def start_run(
    store: ShootStore,
    shoot_id: str,
    mode: str,
    request: str | None = None,
    views: list[str] | None = None,
    mask: str | None = None,
    settings: Settings | None = None,
    client: Any = None,
    manager: RunManager | None = None,
    languages: list[str] | None = None,
) -> TraceRun:
    """Create a run and start it (or queue it behind the shoot's active run). Returns its trace at once."""
    settings = settings or get_settings()
    if client is None:
        from .client import default_client

        client = default_client()
    trace = TraceRun(store.shoot_dir(shoot_id), mode)
    agent = AgentRun(store, shoot_id, mode, trace, settings, client, request=request, views=views, mask=mask,
                     languages=languages)
    (manager or MANAGER).start(agent)
    return trace


def wait(trace: TraceRun, timeout: float | None = None, manager: RunManager | None = None) -> bool:
    """Block until the run has ended (for scripts and tests); returns whether it ended within `timeout`."""
    handle = (manager or MANAGER).get(trace.id)
    if handle is None:
        return trace.done
    return handle.finished.wait(timeout)
