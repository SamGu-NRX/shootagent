"""EV estimation, exposure-invariant alignment, grouping and registration on synthetic brackets."""
import numpy as np
import pytest
from synth import expose, grade, scene, shift

from shootagent.imaging import brackets as B
from shootagent.imaging import features as F
from shootagent.imaging import grouping as G
from shootagent.imaging.align import register


def test_estimate_ev_recovers_stops():
    # fails if EV estimation is not relative to the median bracket or mis-scales stops
    lin = scene(5)
    evs = [-2.0, 0.0, 2.0, -4.0]
    est = B.estimate_ev([expose(lin, e) for e in evs])
    assert est == pytest.approx([-2.0, 0.0, 2.0, -4.0], abs=0.3)


def test_align_recovers_known_shift_across_exposures():
    # fails if alignment breaks when brackets differ by stops or loses sub-pixel accuracy
    lin = scene(6)
    imgs = [expose(lin, 0.0), expose(shift(lin, 7.0, -4.5), -2.0), expose(shift(lin, -3.0, 2.0), 2.0)]
    al = B.align_brackets(imgs, 0)
    assert all(a.ok for a in al)
    # H maps bracket -> reference, so it undoes the shift.
    assert al[1].H[0, 2] == pytest.approx(-7.0, abs=0.6) and al[1].H[1, 2] == pytest.approx(4.5, abs=0.6)
    assert al[2].H[0, 2] == pytest.approx(3.0, abs=0.6) and al[2].H[1, 2] == pytest.approx(-2.0, abs=0.6)
    aligned, cover = B.apply_alignment(imgs, al, 0)
    _, score = B.ghost_map(aligned, 0, cover)
    assert score < 0.01


def test_ghost_map_flags_unaligned_brackets():
    # fails if the ghost score cannot tell a 9 px camera shift from a static bracket pair
    lin = scene(7)
    _, moved = B.ghost_map([expose(lin, 0.0), expose(shift(lin, 9, 0), -1.5)], 0)
    _, still = B.ghost_map([expose(lin, 0.0), expose(lin, -1.5)], 0)
    assert still < 0.002 and moved > 0.01


def test_grouping_splits_interleaved_camera_positions_and_detects_overlay():
    # fails if brackets of different views are grouped together, or a two-view blend is not recognised
    a, b = scene(10), scene(11)
    imgs = [expose(a, 0), expose(b, 0), expose(a, -2), expose(b, -2), expose(a, 2), expose(b, 2)]
    feats = [F.detect(im) for im in imgs]
    g = G.group_brackets(feats)
    assert sorted(g.groups) == [[0, 2, 4], [1, 3, 5]]
    fm = G.match_finished(F.detect(grade(expose(b, 0.5))), feats, g.groups)
    assert g.groups[fm.matched[0]] == [1, 3, 5] and not fm.overlay
    blend = ((expose(a, 0).astype(np.float32) + expose(b, 0).astype(np.float32)) / 2).astype(np.uint8)
    assert G.match_finished(F.detect(blend), feats, g.groups).overlay


def test_grouping_keeps_one_view_together_despite_small_camera_motion():
    lin = scene(12)
    imgs = [expose(shift(lin, i * 1.5, 0), ev) for i, ev in enumerate([-2, -1, 0, 1, 2])]
    g = G.group_brackets([F.detect(im) for im in imgs])
    assert g.groups == [[0, 1, 2, 3, 4]]


def test_register_maps_reference_onto_cropped_graded_finished_at_any_resolution():
    # fails if registering the fusion to a cropped, regraded finished photo is off, or warp ignores resolution
    ref = expose(scene(13, 1100, 740), 0)
    fin = grade(ref)[50:690, 70:1030]
    target = ref[50:690, 70:1030]
    reg = register(ref, fin)
    warped, valid = reg.warp(ref, (fin.shape[1], fin.shape[0]))
    assert valid.mean() > 0.98
    assert np.abs(warped.astype(np.int16) - target.astype(np.int16))[valid].mean() < 4.0
    up = lambda x: np.repeat(np.repeat(x, 2, 0), 2, 1)
    w2, v2 = reg.warp(up(ref), (fin.shape[1] * 2, fin.shape[0] * 2))
    assert np.abs(w2.astype(np.int16) - up(target).astype(np.int16))[v2].mean() < 6.0
