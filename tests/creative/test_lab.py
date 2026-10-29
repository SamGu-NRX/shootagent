"""The lab runner (creative/lab.py) with a fake for every phase module: the order of phases, what state.json holds
while and after a lab runs, a failure that keeps partial work, resume, cancel, and re-simulation."""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

from shootagent.config import Settings
from shootagent.creative import lab
from shootagent.creative.schema import (AgentCard, Beat, Critique, Ending, Evaluation, Hook, LabOptions, Music,
                                        PropertyBrief, Reaction, Reel, ReelConcept, ReelSummary, ViewerPersona)
from shootagent.store import Listing, ShootStore

ORDER = ["brief", "pitch", "critique", "remix", "greenlight", "render", "watch", "judge", "novelty", "verdict",
         "simulate"]


def card(cid: str, role: str) -> AgentCard:
    return AgentCard(id=cid, name=cid.title(), role=role, lens="", color="#123456", initials=cid[:2].upper())


def concept(cid: str, agent: str = "contrarian", title: str | None = None) -> ReelConcept:
    return ReelConcept(id=cid, agent=agent, round=1, title=title or f"Concept {cid}", logline="", format="",
                       hook=Hook(text="Look", style="bold_center"),
                       beats=[Beat(view="v01", seconds=4.0, motion="push_in"), Beat(view="v02", seconds=6.0, motion="pan_left")],
                       ending=Ending(style="end_card", text="Call"), music=Music(mood="warm", bpm=100))


class Fakes:
    """Instant, offline stand-ins for room, baseline, render and evaluate. `calls` records what ran; `fail` names
    calls that raise (e.g. "render:C"); `hold` names a room phase that blocks until the lab is cancelled."""

    def __init__(self):
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.hold: str | None = None
        self.held = threading.Event()
        self.burst = 0  # extra feed messages the room emits during the pitch
        self.drip = 0.0  # seconds between them
        self.seen: dict[str, dict[str, str]] = {}  # phase statuses observed from inside a phase
        self._lock = threading.Lock()

    def phases(self) -> lab.Phases:
        return lab.Phases(room_agents=self.room_agents, write_brief=self.write_brief, run_room=self.run_room,
                          template_concept=self.template_concept, single_agent_card=self.single_agent_card,
                          single_agent_concepts=self.single_agent_concepts, render_concept=self.render_concept,
                          evaluation_agents=self.evaluation_agents, evaluate=self.evaluate)

    def _call(self, name: str) -> None:
        with self._lock:
            self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} blew up")

    def _look(self, ctx, name: str) -> None:
        state = lab.load_lab(ctx.store, ctx.shoot_id, ctx.lab_dir.name)
        self.seen[name] = {p.key: p.status for p in state.phases}

    def room_agents(self, ctx):
        return [card("showrunner", "showrunner"), card("contrarian", "creator"), card("critic", "critic")]

    def write_brief(self, ctx):
        self._call("write_brief")
        ctx.progress("brief", "reading the photos")
        ctx.emit("showrunner", "brief", "A warm one-bedroom; lead with the arched headboard wall.")
        ctx.usage.add_usd("showrunner", 0.25)
        return PropertyBrief(summary="A warm one-bedroom.", facts=["640 sq ft"])

    def run_room(self, ctx, brief):
        self._call("run_room")
        for key in lab.ROOM:
            ctx.check_cancel()
            ctx.progress(key, f"{key} under way")
            ctx.emit("contrarian", "pitch" if key == "pitch" else "note", f"{key}: a message", concept="c1")
            if key == "pitch":  # work handed over mid-phase, the way room.py publishes it
                ctx.publish("concepts", [concept("c1", title="Draft c1"), concept("c2")])
            elif key == "critique":
                ctx.publish("critiques", [Critique(critic="critic", concept="c1", originality=7, hook=6, specificity=8,
                                                   truth=9, virality=6, verdict="revise", notes="Tighten the hook.")])
            elif key == "greenlight":
                ctx.publish("finalists", ["c2"])
            if key == self.hold:
                self.held.set()
                while True:
                    ctx.check_cancel()
                    time.sleep(0.01)
            if key == "pitch":
                for i in range(self.burst):
                    ctx.emit("critic", "note", f"burst {i}")
                    time.sleep(self.drip)
        ctx.usage.add_usd("creator", 1.0)
        return SimpleNamespace(agents=[card("showrunner", "showrunner"), card("contrarian", "creator"),
                                       card("critic", "critic")],
                               concepts=[concept("c1"), concept("c2"), concept("c3")], critiques=[],
                               finalists=["c2", "c1", "c3"])

    def single_agent_card(self):
        return card("single_agent", "creator")

    def single_agent_concepts(self, ctx, brief, n=8):
        self._call("single_agent_concepts")
        ctx.progress("single_agent", "writing alone")
        ctx.publish("concepts", [concept("s1", "single_agent"), concept("s2", "single_agent")])
        return [concept("s1", "single_agent"), concept("s2", "single_agent")], "s2"

    def template_concept(self, ctx):
        self._call("template_concept")
        return concept("template", "template")

    def render_concept(self, ctx, c, reel_id, label, kind, voice=True):
        self._call(f"render:{reel_id}")
        reels = ctx.lab_dir / "reels"
        (reels / f"{reel_id}.mp4").write_bytes(bytes(range(256)) * 64)
        (reels / f"{reel_id}.jpg").write_bytes(b"\xff\xd8poster")
        return Reel(id=reel_id, label=label, kind=kind, concept=c.id, title=c.title, video=f"reels/{reel_id}.mp4",
                    poster=f"reels/{reel_id}.jpg", seconds=c.seconds, has_voice=voice)

    def evaluation_agents(self):
        return [card("v1", "viewer"), card("v2", "viewer"), card("j1", "judge")]

    def evaluate(self, ctx, reels, concepts, single_agent):
        self._call("evaluate")
        self._look(ctx, "evaluate")
        for key in lab.EVAL:
            ctx.progress(key, f"{key} under way")
        viewers = [ViewerPersona(id="v1", name="Dana", segment="first-time buyers", weight=0.6, bio=""),
                   ViewerPersona(id="v2", name="Lee", segment="investors", weight=0.4, bio="")]
        strength = {"A": 15, "B": 70, "C": 45, "S": 35}
        reactions = [Reaction(viewer=v.id, reel=r.id, stopped=strength[r.id] > 30, first_impression="",
                              watch_seconds=r.seconds * strength[r.id] / 100, completed=strength[r.id] > 60,
                              rewatch=strength[r.id], like=strength[r.id], comment=strength[r.id],
                              share=strength[r.id], save=strength[r.id], follow=10, inquire=5,
                              comment_text=f"{v.name} on {r.id}")
                     for r in reels for v in viewers]
        summaries = [ReelSummary(reel=r.id, creativity=strength[r.id] / 10, creativity_sd=1, pairwise_win_rate=0.5,
                                 novelty=0.5, stop_rate=0.5, avg_watch_fraction=0.5, completion_rate=0.5,
                                 like_rate=0.5, comment_rate=0.5, share_rate=0.5, save_rate=0.5, inquire_rate=0.1)
                     for r in reels]
        return Evaluation(viewers=viewers, reactions=reactions, summaries=summaries, verdict="B is the most creative.",
                          diversity={"room": len(concepts), "single_agent": len(single_agent)})


def stop_labs() -> None:
    """Cancel any lab a failed test left running, so it can neither block later tests nor hang the exit."""
    for run in list(lab._RUNS.values()):
        if not run.finished.is_set():
            run.cancel()
            run.finished.wait(5)


def wait_for(check, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


@pytest.fixture
def fakes(monkeypatch):
    f = Fakes()
    monkeypatch.setattr(lab, "PHASES", f.phases())
    yield f
    stop_labs()


@pytest.fixture
def env(tmp_path):
    store = ShootStore(tmp_path / "shoots")
    store.create("Test shoot", shoot_id="s1")
    store.mutate("s1", lambda s: setattr(s, "listing", Listing(address="Unit 4B", price="$489,000")))
    return store, Settings(_env_file=None, data_dir=tmp_path, anthropic_api_key="test", render_workers=2)


def run_lab(env, options: LabOptions | None = None, resume: str | None = None, timeout: float = 10):
    store, settings = env
    lab_id = lab.start_lab(store, "s1", options or LabOptions(), settings=settings, client=object(), resume=resume)
    assert lab.wait_lab(lab_id, timeout)
    return lab_id, lab.load_lab(store, "s1", lab_id)


def on_disk(env, lab_id: str):
    store, _ = env
    return lab.LabState.model_validate_json((lab.lab_dir(store, "s1", lab_id) / "state.json").read_text())


def test_a_lab_runs_every_phase_and_persists_what_it_made(env, fakes):
    lab_id, _ = run_lab(env, LabOptions(render_single_agent=True))
    state = on_disk(env, lab_id)
    assert state.status == "done" and state.error is None and state.title == "Unit 4B"
    assert {p.key: p.status for p in state.phases} == {k: "done" for k in lab.LABELS}
    assert [c.id for c in state.concepts] == ["c1", "c2", "c3", "s2", "s1", "template"]  # room, single agent, template
    assert state.finalists == ["c2", "c1", "c3"] and state.single_agent == ["s2", "s1"]
    assert [(r.id, r.label, r.kind, r.concept) for r in state.reels] == [
        ("A", "Template", "baseline", "template"), ("B", "Showrunner's pick", "creative", "c2"),
        ("C", "Wildcard", "creative", "c1"), ("S", "Single agent", "single_agent", "s2")]
    for r in state.reels:
        assert (lab.lab_dir(env[0], "s1", lab_id) / r.video).is_file()
    assert {c.id: c.status for c in state.concepts}["c2"] == "rendered"
    assert [s.reel for s in state.simulations] == ["A", "B", "C", "S"]
    assert all(s.followers == 8000 and s.hours == 48 for s in state.simulations)
    views = {s.reel: s.totals.views for s in state.simulations}
    assert views["B"] > views["A"]  # the stronger reactions travel further
    ids = [a.id for a in state.agents]
    assert ids[:3] == ["showrunner", "contrarian", "critic"]
    assert {"single_agent", "producer", "template", "v1", "j1"} <= set(ids)
    assert state.usage.usd == pytest.approx(1.25) and state.usage.by_role["creator"] == pytest.approx(1.0)
    texts = [m.text for m in state.feed]
    assert texts[0].startswith("A warm one-bedroom") and any("travels furthest" in t for t in texts)
    assert [m.id for m in state.feed] == [f"m{i:04d}" for i in range(1, len(state.feed) + 1)]
    # evaluate gets every concept in the lab, template included, and the control's concepts on their own
    assert state.evaluation.verdict and state.evaluation.diversity == {"room": 6, "single_agent": 2}
    assert state.elapsed > 0 and state.phase == "simulate"


def test_published_work_shows_while_the_room_runs_and_survives_a_cancel(env, fakes):
    store, settings = env
    fakes.hold = "remix"
    lab_id = lab.start_lab(store, "s1", LabOptions(), settings=settings, client=object())
    assert fakes.held.wait(5)
    wait_for(lambda: {p.key: p.status for p in lab.load_lab(store, "s1", lab_id).phases}["single_agent"] == "done")
    live = lab.load_lab(store, "s1", lab_id)
    assert {c.id: c.title for c in live.concepts if c.agent == "contrarian"} == {"c1": "Draft c1", "c2": "Concept c2"}
    assert [(k.critic, k.concept) for k in live.critiques] == [("critic", "c1")]
    assert live.single_agent == ["s2", "s1"]  # the control finished: its own result, its pick first
    wait_for(lambda: "Draft c1" in (lab.lab_dir(store, "s1", lab_id) / "state.json").read_text())  # saved live
    assert lab.cancel_lab(store, "s1", lab_id) and lab.wait_lab(lab_id, 5)
    kept = on_disk(env, lab_id)
    assert kept.status == "cancelled" and [c.id for c in kept.concepts] == ["c1", "c2", "s2", "s1"]
    assert len(kept.critiques) == 1 and kept.finalists == []
    fakes.hold = None
    _, done = run_lab(env, resume=lab_id)
    assert done.status == "done"
    assert {c.id: c.title for c in done.concepts}["c1"] == "Concept c1"  # the room's final result wins
    assert done.critiques == [] and done.finalists == ["c2", "c1", "c3"]
    assert [c.id for c in done.concepts] == ["c1", "c2", "c3", "s2", "s1", "template"]


def test_the_control_publishes_into_its_own_list(env, fakes, monkeypatch):
    seen = {}

    def single(ctx, brief, n=8):
        ctx.publish("concepts", [concept("s1", "single_agent"), concept("s2", "single_agent")])
        seen.update(state=lab.load_lab(ctx.store, ctx.shoot_id, ctx.lab_dir.name))
        return [concept("s1", "single_agent"), concept("s2", "single_agent")], "s2"

    monkeypatch.setattr(lab.PHASES, "single_agent_concepts", single)
    _, state = run_lab(env)
    mid = seen["state"]
    assert set(mid.single_agent) == {"s1", "s2"}
    assert {c.id for c in mid.concepts if c.agent == "single_agent"} == {"s1", "s2"}
    assert state.single_agent == ["s2", "s1"] and state.status == "done"


def test_feed_text_with_escaped_quotes_reads_plainly(env, fakes, monkeypatch):
    real = fakes.write_brief

    def brief(ctx):
        ctx.emit("critic", "critique", 'Keep c08\'s \\"priced once\\" line.')
        return real(ctx)

    monkeypatch.setattr(lab.PHASES, "write_brief", brief)
    _, state = run_lab(env)
    assert state.feed[0].text == 'Keep c08\'s "priced once" line.'


def test_phases_run_in_order(env, fakes):
    lab_id, state = run_lab(env)
    calls = fakes.calls
    assert calls[0] == "write_brief" and calls[-1] == "evaluate"
    assert {"run_room", "single_agent_concepts"} == set(calls[1:3])
    assert calls.index("template_concept") < min(calls.index("render:A"), calls.index("render:B"))
    phases = {p.key: p for p in state.phases}
    starts = [phases[k].started for k in ORDER]
    assert starts == sorted(starts)
    assert all(phases[k].ended >= phases[k].started for k in ORDER)
    for a, b in zip(ORDER, ORDER[1:]):  # each phase is over before the next one starts
        assert phases[a].ended <= phases[b].started, (a, b)
    # from inside the evaluation, everything before it had finished and nothing after it had started
    seen = fakes.seen["evaluate"]
    assert all(seen[k] == "done" for k in ORDER[:ORDER.index("render") + 1])
    assert seen["simulate"] == "pending"


def test_one_round_skips_the_phases_the_room_never_reports(env, fakes, monkeypatch):
    def pitch_only(ctx, brief):
        ctx.progress("pitch", "pitching")
        ctx.progress("greenlight", "picking")
        return SimpleNamespace(agents=[], concepts=[concept("c1"), concept("c2")], critiques=[], finalists=["c1"])

    monkeypatch.setattr(lab.PHASES, "run_room", pitch_only)
    _, state = run_lab(env, LabOptions(rounds=1, single_agent=False))
    status = {p.key: p.status for p in state.phases}
    assert status["pitch"] == status["greenlight"] == "done"
    assert status["critique"] == status["remix"] == "skipped" and status["single_agent"] == "skipped"
    assert state.single_agent == [] and state.status == "done"
    assert [r.id for r in state.reels] == ["A", "B"]  # only one finalist to render
    assert "single_agent" not in {a.id for a in state.agents}


def test_a_failed_phase_keeps_the_partial_state(env, fakes):
    fakes.fail.add("render:C")
    lab_id, state = run_lab(env)
    assert state.status == "failed" and "Render failed" in state.error and "C: RuntimeError" in state.error
    phases = {p.key: p for p in state.phases}
    assert phases["render"].status == "failed" and "render:C blew up" in phases["render"].detail
    assert all(phases[k].status == "done" for k in ("brief", "pitch", "critique", "remix", "greenlight"))
    assert all(phases[k].status == "pending" for k in ("watch", "judge", "novelty", "verdict", "simulate"))
    assert state.brief is not None and [c.id for c in state.concepts][:3] == ["c1", "c2", "c3"]
    assert [r.id for r in state.reels] == ["A", "B"] and state.evaluation is None
    assert "evaluate" not in fakes.calls
    assert on_disk(env, lab_id).status == "failed"
    assert (lab.lab_dir(env[0], "s1", lab_id) / "errors.log").read_text().count("render:C blew up") >= 1


def test_resume_skips_finished_work(env, fakes):
    fakes.fail.add("render:C")
    lab_id, failed = run_lab(env)
    fakes.fail.clear()
    again, state = run_lab(env, resume=lab_id)
    assert again == lab_id and state.status == "done" and state.error is None
    for name in ("write_brief", "run_room", "single_agent_concepts", "render:A", "render:B", "evaluate"):
        assert fakes.calls.count(name) == 1, name
    assert fakes.calls.count("render:C") == 2
    assert [r.id for r in state.reels] == ["A", "B", "C"] and len(state.simulations) == 3
    assert all(p.status == "done" for p in state.phases)
    ids = [m.id for m in state.feed]
    assert len(ids) == len(set(ids)) and len(state.feed) > len(failed.feed)
    assert state.elapsed >= failed.elapsed
    with pytest.raises(lab.LabConflict):  # a finished lab has nothing left to resume
        lab.start_lab(env[0], "s1", LabOptions(), settings=env[1], client=object(), resume=lab_id)


def test_cancel_stops_the_lab_and_resume_finishes_it(env, fakes):
    store, settings = env
    fakes.hold = "critique"
    lab_id = lab.start_lab(store, "s1", LabOptions(), settings=settings, client=object())
    assert fakes.held.wait(5)
    live = lab.load_lab(store, "s1", lab_id)
    assert live.status == "running" and live.phase == "critique"
    with pytest.raises(lab.LabConflict):  # one lab per shoot at a time
        lab.start_lab(store, "s1", LabOptions(), settings=settings, client=object())
    assert lab.cancel_lab(store, "s1", lab_id)
    assert lab.wait_lab(lab_id, 5)
    state = on_disk(env, lab_id)
    phases = {p.key: p for p in state.phases}
    assert state.status == "cancelled" and phases["critique"].status == "failed"
    assert phases["critique"].detail == "Cancelled." and phases["brief"].status == "done"
    assert phases["pitch"].status == "done" and state.reels == []
    assert not lab.cancel_lab(store, "s1", lab_id)
    fakes.hold = None
    _, done = run_lab(env, resume=lab_id)
    assert done.status == "done" and fakes.calls.count("write_brief") == 1 and fakes.calls.count("run_room") == 2


def test_another_process_can_cancel_through_the_lab_folder(env, fakes):
    store, settings = env
    fakes.hold = "pitch"
    lab_id = lab.start_lab(store, "s1", LabOptions(), settings=settings, client=object())
    assert fakes.held.wait(5)
    (lab.lab_dir(store, "s1", lab_id) / lab.CANCEL_FILE).touch()  # what cancel_lab does for a lab it does not run
    assert lab.wait_lab(lab_id, 5)
    assert on_disk(env, lab_id).status == "cancelled"
    assert not (lab.lab_dir(store, "s1", lab_id) / lab.CANCEL_FILE).exists()
    # a lab another process is running (fresh "running" state.json) gets the request file
    other = lab.lab_dir(store, "s1", "20261028-000000-abcdef")
    other.mkdir(parents=True)
    (other / "state.json").write_text(on_disk(env, lab_id).model_copy(
        update={"id": other.name, "status": "running"}).model_dump_json())
    assert lab.cancel_lab(store, "s1", other.name) and (other / lab.CANCEL_FILE).exists()


def test_state_writes_are_live_but_throttled(env, fakes, monkeypatch):
    writes: list[tuple[float, int]] = []  # (time, feed length written)
    real = lab._atomic_write

    def counting(path, text):
        writes.append((time.monotonic(), len(lab.LabState.model_validate_json(text).feed)))
        real(path, text)

    monkeypatch.setattr(lab, "_atomic_write", counting)
    fakes.burst, fakes.drip = 300, 0.005  # a feed message every ~5 ms for about 1.5 s
    t0 = time.monotonic()
    lab_id, state = run_lab(env)
    took = time.monotonic() - t0
    assert took > 1.2 and len(state.feed) > 300
    assert len(writes) <= 2 + 2 * took + 1  # first and final writes, plus at most two a second
    mid = [n for _, n in writes[1:-1]]
    assert mid and 0 < min(mid) and max(mid) < len(state.feed)  # the dashboard sees the feed grow while it runs
    gaps = [b - a for (a, _), (b, _) in zip(writes[1:-1], writes[2:-1])]
    assert all(g >= lab.SAVE_INTERVAL * 0.9 for g in gaps)
    assert on_disk(env, lab_id).feed == state.feed  # the final write has everything


def test_a_running_state_nobody_writes_reads_as_failed(env, fakes):
    store, settings = env
    lab_id, state = run_lab(env)
    path = lab.lab_dir(store, "s1", lab_id) / "state.json"
    path.write_text(state.model_copy(update={"status": "running"}).model_dump_json())
    old = time.time() - 3600
    os.utime(path, (old, old))
    stale = lab.load_lab(store, "s1", lab_id)
    assert stale.status == "failed" and "Resume" in stale.error
    assert lab.list_labs(store, "s1")[0]["status"] == "failed"
    os.utime(path, None)  # rewritten just now: another process is running it
    assert lab.load_lab(store, "s1", lab_id).status == "running"
    with pytest.raises(lab.LabConflict):
        lab.start_lab(store, "s1", LabOptions(), settings=settings, client=object())


def test_list_and_resimulate(env, fakes):
    store, _ = env
    lab_id, state = run_lab(env)
    rows = lab.list_labs(store, "s1")
    assert rows == [{"id": lab_id, "title": "Unit 4B", "created": state.created, "status": "done",
                     "phase": "simulate", "reels": 3, "usd": 1.25, "elapsed": state.elapsed}]
    before = {s.reel: s.totals.views for s in state.simulations}
    sims = lab.resimulate(store, "s1", lab_id, followers=40_000, hours=24, seed=3)
    assert [s.reel for s in sims] == ["A", "B", "C"] and all(s.hours == 24 and s.followers == 40_000 for s in sims)
    saved = on_disk(env, lab_id)
    assert saved.simulations == sims and (saved.options.followers, saved.options.hours, saved.options.seed) == (40_000, 24, 3)
    assert sum(s.totals.views for s in sims) > sum(before.values())
    with pytest.raises(ValueError):
        lab.resimulate(store, "s1", lab_id, followers=10, hours=0, seed=1)


def test_resimulate_needs_an_evaluation(env, fakes):
    fakes.fail.add("evaluate")
    lab_id, state = run_lab(env)
    assert state.status == "failed" and {p.key: p.status for p in state.phases}["watch"] == "failed"
    with pytest.raises(lab.LabConflict):
        lab.resimulate(env[0], "s1", lab_id, followers=8000, hours=48, seed=7)


def test_bad_requests_raise_before_anything_runs(env, fakes):
    store, settings = env
    for bad in (LabOptions(finalists=9), LabOptions(rounds=0), LabOptions(hours=0), LabOptions(creators=[])):
        with pytest.raises(ValueError):
            lab.start_lab(store, "s1", bad, settings=settings, client=object())
    with pytest.raises(KeyError):
        lab.start_lab(store, "nope", LabOptions(), settings=settings, client=object())
    with pytest.raises(KeyError):
        lab.load_lab(store, "s1", "../../shoot")
    assert fakes.calls == [] and lab.list_labs(store, "s1") == []
