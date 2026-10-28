"""QC-mode tool behaviour the agent depends on: bracket colours it can recolour to, and the order it sees."""
from __future__ import annotations

import numpy as np
from fakes import ScriptedClient, make_shoot, reply, settings

from shootagent.agent.loop import AgentRun
from shootagent.agent.verify import region_metrics
from shootagent.imaging import adjust
from shootagent.imaging.color import rgb_to_lab
from shootagent.store import Bracket
from shootagent.tools.base import ToolContext, run_tool
from shootagent.trace import TraceRun


def test_bracket_colour_is_recolourable_onto_the_photo_and_moves_it_toward_the_brackets(tmp_path):
    # fails if sample_color(source="brackets") returns the fusion's raw colour: the fusion is darker than the
    # photo (L 30 vs 80 here), so recolouring to it always failed the delta-E check (case 0013 failed 3 times)
    store, sid = make_shoot(tmp_path, n_views=1, size=(40, 60))
    photo = np.full((40, 60, 3), 200, np.uint8)  # the finished wall, neutral grey
    ref = np.zeros((40, 60, 3), np.uint8)
    ref[:] = (55, 70, 105)  # the same wall in the brackets: dark and blue
    store.add_version(sid, "v01", photo, op="ingest")
    blob = store.put_image(sid, ref)
    store.mutate(sid, lambda s: setattr(s.view("v01"), "reference", blob))
    ctx = ToolContext(store, sid, TraceRun(store.shoot_dir(sid), "qc"), settings(tmp_path))

    res = run_tool(ctx, "sample_color", {"view": "v01", "box": [0, 0, 1, 1], "source": "brackets"})
    assert res.ok, res.summary
    mask = np.ones(photo.shape[:2], bool)
    after = adjust.recolor(photo, mask, res.data["hex"])
    _, fails = region_metrics("delta_e", {"hex": res.data["hex"]}, photo, after, mask, None)
    assert fails == []
    assert rgb_to_lab(after)[..., 2].mean() < rgb_to_lab(photo)[..., 2].mean() - 10  # it turned blue


def test_qc_opening_tells_the_agent_what_each_view_ordered(tmp_path):
    # fails if the QC run starts without the order, which decides whether an added fire is a defect
    # (cases 0041/0042: "Keep fireplaces unlit") and whether a removed person was intended (0049)
    store, sid = make_shoot(tmp_path / "shoots")
    store.mutate(sid, lambda s: s.view("v01").meta.update(
        editing_context="Fire addition is disabled. Keep all fireplaces unlit.", allowed_edits=["Keep fireplaces unlit"]))
    client = ScriptedClient([reply(text="Done.")])
    trace = TraceRun(store.shoot_dir(sid), "qc")
    AgentRun(store, sid, "qc", trace, settings(tmp_path), client).run()
    opening = client.requests[0]["messages"][0]["content"][0]["text"]
    assert "Keep all fireplaces unlit" in opening and "Keep fireplaces unlit" in opening
    assert '"v02": "unknown"' in opening


def _with_brackets(store, sid, view, n=3, **meta):
    img = np.full((40, 60, 3), 120, np.uint8)
    blobs = [store.put_image(sid, img) for _ in range(n)]

    def add(s):
        v = s.view(view)
        v.brackets = [Bracket(blob=b, index=i) for i, b in enumerate(blobs)]
        v.meta.update(meta)

    store.mutate(sid, add)


def test_editor_request_previews_are_neither_checked_nor_split(tmp_path):
    # fails if QC treats an editor request's preview images as brackets (run 20261027-235730-b73cba split a
    # customer's TV photo into two views from them)
    store, sid = make_shoot(tmp_path, n_views=1)
    _with_brackets(store, sid, "v01", source="editor_request")
    ctx = ToolContext(store, sid, TraceRun(store.shoot_dir(sid), "qc"), settings(tmp_path))
    res = run_tool(ctx, "run_qc_checks", {"view": "v01"})
    assert res.ok and res.data["candidates"] == [] and "no original brackets" in res.summary
    res = run_tool(ctx, "regroup_brackets", {"view": "v01", "groups": [[0], [1, 2]]})
    assert not res.ok and "no original brackets" in res.summary
    assert [v.id for v in store.load(sid).views] == ["v01"]


def test_brackets_detected_as_one_camera_position_are_not_split(tmp_path):
    # fails if same-view ghosting is "fixed" by splitting one position into near-identical views
    # (run 20261028-001738-ee3055 on defect_train_0026)
    store, sid = make_shoot(tmp_path, n_views=1)
    _with_brackets(store, sid, "v01", qc={"groups": [[0, 1, 2]]})
    ctx = ToolContext(store, sid, TraceRun(store.shoot_dir(sid), "qc"), settings(tmp_path))
    res = run_tool(ctx, "regroup_brackets", {"view": "v01", "groups": [[0, 1], [2]]})
    assert not res.ok and "restore_from_brackets" in res.summary
    assert [v.id for v in store.load(sid).views] == ["v01"]


def test_set_finding_rejects_types_outside_autohdr_taxonomy():
    # A live QC run recorded an invented 'erased_object' type, which maps onto nothing in AutoHDR's results.
    import pydantic
    import pytest

    from shootagent.tools.shoot import SetFindingIn

    with pytest.raises(pydantic.ValidationError):
        SetFindingIn(view="v01", finding="new", status="open", note="x", type="erased_object")
    assert SetFindingIn(view="v01", finding="new", status="open", note="x", type="camera_person_removal").type
