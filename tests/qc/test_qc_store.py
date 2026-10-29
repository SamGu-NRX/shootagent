"""Ingest -> analyze_view -> regroup_view through the real store, on synthetic AutoHDR-shaped cases."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "imaging"))
from synth import expose, grade, scene

from shootagent.ingest import create_shoot_from_defect_cases
from shootagent.qc import HIGH_CONFIDENCE, analyze_view, regroup_view
from shootagent.qc.analyze import _transfer_finished_look
from shootagent.imaging.color import rgb_to_lab
from shootagent.store import ShootStore
from shootagent.trace import NullTrace


def write_case(root: Path, name: str, brackets: list[np.ndarray], finished: np.ndarray) -> Path:
    d = root / name
    d.mkdir(parents=True)
    inputs = []
    for i, b in enumerate(brackets):
        Image.fromarray(b).save(d / f"bracket_{i:02d}.jpg", quality=95)
        inputs.append(f"/input/bracket_{i:02d}.jpg")
    Image.fromarray(finished).save(d / "edited.jpg", quality=95)
    (d / "case.json").write_text(json.dumps({"case_id": name, "inputs": inputs, "current_image": "/input/edited.jpg",
                                             "request": "", "schema_version": "autohdr-qc-v1"}), encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def shoot(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("qc")
    a, b = scene(30, 1200, 800), scene(31, 1200, 800)
    clean = write_case(tmp / "cases", "clean_case", [expose(a, e) for e in (-2, 0, 2)], grade(expose(a, 0)))
    mixed_br = [expose(a, -2), expose(a, 0), expose(b, -2), expose(a, 2), expose(b, 0), expose(b, 2)]
    blend = ((grade(expose(a, 0)).astype(np.float32) + grade(expose(b, 0)).astype(np.float32)) / 2).astype(np.uint8)
    mixed = write_case(tmp / "cases", "mixed_case", mixed_br, blend)
    store = ShootStore(tmp / "data")
    s = create_shoot_from_defect_cases(store, "synthetic", [clean, mixed])
    return store, s, clean


def test_ingest_stores_inputs_byte_identical_with_finished_as_version_zero(shoot):
    # fails if ingest re-encodes brackets (quality and metadata loss) or the finished photo is not v0 "ingest"
    store, s, clean = shoot
    v = s.views[0]
    assert [b.index for b in v.brackets] == [0, 1, 2]
    for b in v.brackets:
        assert store.blob_path(s.id, b.blob).read_bytes() == (clean / f"bracket_{b.index:02d}.jpg").read_bytes()
    assert v.head == f"{v.id}.0" and v.versions[0].op == "ingest" and v.versions[0].blob == v.finished
    assert v.meta["case_id"] == "clean_case" and v.meta["width"] == 1200
    assert [b.ev for b in v.brackets] == pytest.approx([-2, 0, 2], abs=0.35)


def test_clean_view_has_no_high_confidence_candidates(shoot):
    # fails if a correctly edited photo would be "fixed": precision on clean views
    store, s, _ = shoot
    tr = NullTrace()
    va = analyze_view(store, s.id, s.views[0].id, tr)
    assert va.high_confidence == 0, va.candidates
    view = store.load(s.id).view(s.views[0].id)
    assert view.reference == va.reference and view.meta["qc"]["analyzed_blob"] == view.finished
    again = analyze_view(store, s.id, s.views[0].id)
    assert again.cached and again.candidates == va.candidates


def test_finished_look_transfer_undoes_a_known_tone_shift_without_geometry_change():
    # fails if regrouped photos keep the fusion's flat grade, or if look transfer resizes/crops the part
    source = expose(scene(32, 900, 600), 0)
    finished = grade(source)
    styled, method = _transfer_finished_look(source, finished)
    before = np.mean(np.linalg.norm(rgb_to_lab(source) - rgb_to_lab(finished), axis=2))
    after = np.mean(np.linalg.norm(rgb_to_lab(styled) - rgb_to_lab(finished), axis=2))
    assert styled.shape == source.shape
    assert styled.dtype == np.uint8
    assert method.startswith("grade fitted")
    assert after < before * 0.5


def test_two_view_blend_is_flagged_and_regroup_splits_it(shoot):
    # fails if a finished photo blending two camera positions is not reported, or regrouping loses brackets
    store, s, _ = shoot
    tr = NullTrace()
    vid = s.views[1].id
    va = analyze_view(store, s.id, vid, tr)
    top = va.candidates[0]
    assert top["type"] == "ghosting_grouping" and top["confidence"] >= HIGH_CONFIDENCE
    assert sorted(va.groups) == [[0, 1, 3], [2, 4, 5]]
    assert any(e["kind"] == "finding" and e["data"]["candidate"] for e in tr.events)
    new_ids = regroup_view(store, s.id, vid)
    after = store.load(s.id)
    assert after.view(vid).groups == va.groups and after.view(vid).meta["split_into"] == new_ids
    for nid, g in zip(new_ids, va.groups):
        nv = after.view(nid)
        assert nv.derived_from == vid and nv.groups == [g]
        assert sorted(b.index for b in nv.brackets) == g
        assert nv.versions[0].op == "regroup" and nv.head == nv.versions[0].id
        img = store.get_image(s.id, nv.finished)
        assert img.shape[2] == 3 and img.std() > 10
    with pytest.raises(ValueError, match="regrouped before"):
        regroup_view(store, s.id, vid)
