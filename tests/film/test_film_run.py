"""Film mode in the agent loop: what the agent owes before it may finish."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from fakes import ScriptedClient, make_shoot, reply, settings, tool  # noqa: E402

from shootagent.agent.loop import AgentRun  # noqa: E402
from shootagent.trace import TraceRun, read_events  # noqa: E402


def _approved_shoot(tmp_path):
    store, sid = make_shoot(tmp_path / "shoots")

    def approve(s):
        for v in s.views:
            v.approved = True

    store.mutate(sid, approve)
    return store, sid


def test_film_run_is_held_until_every_language_is_assembled(tmp_path):
    """Fails if a film run can end with a language never rendered."""
    store, sid = _approved_shoot(tmp_path)
    client = ScriptedClient([reply(tool("submit_plan", steps=[{"intent": "walk the home"}], text="Plan.")),
                             reply(text="Done.")])
    trace = TraceRun(store.shoot_dir(sid), "film")
    AgentRun(store, sid, "film", trace, settings(tmp_path), client, languages=["en", "es"]).run()

    nudges = [e["data"]["text"] for e in read_events(trace.path) if e["kind"] == "thought" and "Not finished" in e["data"]["text"]]
    assert nudges and "en, es" in nudges[0]


def test_a_language_code_cannot_name_a_folder_outside_the_film(tmp_path):
    """Fails if a language such as '../x' reaches assembly, where it becomes a directory name."""
    import pytest

    store, sid = _approved_shoot(tmp_path)
    trace = TraceRun(store.shoot_dir(sid), "film")
    with pytest.raises(ValueError):
        AgentRun(store, sid, "film", trace, settings(tmp_path), ScriptedClient([]), languages=["en", "../x"])


def test_a_clip_rejected_before_a_replan_stays_in_the_manifest(tmp_path):
    """Fails if re-planning a shot from generated to depth drops its rejected clip from the manifest, which the
    AB 723 disclosure page lists."""
    from shootagent.film.assemble import write_manifest
    from shootagent.film.spec import Attempt, FilmSpec, ShotResult
    from shootagent.tools import ToolContext, run_tool
    from shootagent.tools.film import film_context, spec_path, start_film

    store, sid = make_shoot(tmp_path / "shoots")
    ctx = ToolContext(store, sid, TraceRun(store.shoot_dir(sid), "film"), settings(tmp_path))
    start_film(ctx, ["v01", "v02"], ["en"])
    shots = [{"view": "v01", "motion": "generated", "camera": "Slow dolly", "label": "Kitchen"},
             {"view": "v02", "motion": "depth", "label": "Bath"}, {"view": "v01", "motion": "still", "label": "Kitchen"}]
    assert run_tool(ctx, "plan_film", {"shots": shots}).ok
    spec = FilmSpec.load(spec_path(ctx))
    spec.shots[0].result = ShotResult(source="depth", clips=["d1"], fell_back=True, attempts=[
        Attempt(fingerprint="g1", prompt="p", reasons=["judge: ceiling_light new: pendant lights"])])
    spec.save(spec_path(ctx))

    shots[0] = {"view": "v01", "motion": "depth", "label": "Kitchen"}
    assert run_tool(ctx, "plan_film", {"shots": shots}).ok
    spec = FilmSpec.load(spec_path(ctx))
    for s in spec.shots:
        s.result = ShotResult(source="depth", clips=["d"])
    manifest = write_manifest(film_context(ctx), spec, {})

    kept = [a for s in manifest["shots"] if s["source_photos"][0]["view"] == "v01" for a in s["attempts"]]
    assert [a["fingerprint"] for a in kept] == ["g1"]  # listed once, though v01 plays in two shots


def test_a_film_uses_only_approved_photos_and_says_how_to_get_there(tmp_path):
    """Fails if a film starts on photos nobody reviewed (the demo's ghosted game room), if the refusal does not
    say to run QC, or if a view QC split into camera positions is filmed instead of its parts."""
    import pytest

    from shootagent.film.readiness import NeedsReview

    store, sid = make_shoot(tmp_path / "shoots", n_views=3)
    trace = TraceRun(store.shoot_dir(sid), "film")
    with pytest.raises(NeedsReview, match="(?i)run QC on v01, v02, v03"):
        AgentRun(store, sid, "film", trace, settings(tmp_path), ScriptedClient([]))

    def review(s):
        s.view("v01").approved = True
        s.view("v02").approved = True
        s.view("v02").meta["split_into"] = ["v02-a"]

    store.mutate(sid, review)
    run = AgentRun(store, sid, "film", trace, settings(tmp_path), ScriptedClient([]))
    assert run.scope == ["v01"]


def test_a_view_qc_advised_against_delivering_is_never_counted_as_clean(tmp_path):
    """Fails if launch could approve a view QC itself recommended not delivering (a partial framing)."""
    from shootagent.film.readiness import review_state
    from shootagent.tools import ToolContext, run_tool

    store, sid = make_shoot(tmp_path / "shoots")
    ctx = ToolContext(store, sid, TraceRun(store.shoot_dir(sid), "qc"), settings(tmp_path))
    assert run_tool(ctx, "recommend_delivery", {"view": "v02", "deliver": False, "reason": "partial framing"}).ok
    assert review_state(store, store.load(sid))["v02"] == "not_deliverable"  # launch approves only "qc_ok"
