"""The wall paint check on synthetic Lab surfaces: which walls it reports and where."""
import numpy as np
import pytest

from shootagent.imaging.color import lab_to_rgb
from shootagent.qc.candidates import wall_paint_candidates

H, W = 120, 160
WALL = (slice(10, 70), slice(80, 150))  # rows, cols


def lab_image(base, wall):
    lab = np.empty((H, W, 3), np.float32)
    lab[:] = base
    lab[WALL] = wall
    return lab_to_rgb(lab)


def wall_mask():
    m = np.zeros((H, W), bool)
    m[WALL] = True
    return m


def test_blue_wall_neutralised_in_the_finished_photo_is_reported_over_the_wall():
    # fails if a pale blue wall rendered grey (AutoHDR paint cases 0029/0032/0033) produces no candidate,
    # or the candidate does not cover the wall the agent must recolour
    ref = lab_image((50, 0, 1), (48, -1.5, -5.5))
    fin = lab_image((75, 0, 2), (75, -0.5, 2.0))
    ceiling = np.zeros((H, W), bool)
    ceiling[:8] = True
    [c] = wall_paint_candidates(ref, fin, np.ones((H, W), bool), [wall_mask()], ceiling)
    assert c.type == "paint_color" and c.suggested_fix == "recolor_region"
    assert np.array_equal(c.mask, wall_mask())
    assert c.region == pytest.approx([80 / W, 10 / H, 150 / W, 70 / H])
    assert c.evidence["bracket_hue"] == "blue"
    assert c.evidence["ceiling_ab_brackets"] == pytest.approx([0, 1], abs=0.6)


@pytest.mark.parametrize("wall_ref,window_frac", [
    ((48, 3.0, 10.0), 0.0),  # warm wall neutralised: ordinary white balance
    ((48, -1.5, -5.5), 0.4),  # window wall: the blue is sky light through the glass
    ((48, -0.5, -1.0), 0.0),  # near-grey wall: nothing to lose
])
def test_walls_white_balance_explains_are_not_reported(wall_ref, window_frac):
    # fails if the check flags walls on which neutralisation is legitimate (clean control 0018 has a window
    # wall that is blue in the brackets; 7 defect cases have warm walls that white balance neutralised)
    ref = lab_image((50, 0, 1), wall_ref)
    fin = lab_image((75, 0, 2), (75, -0.5, 2.0))
    window = np.zeros((H, W), bool)
    rows = round((WALL[0].stop - WALL[0].start) * window_frac)
    window[WALL[0].start : WALL[0].start + rows, WALL[1]] = True
    assert wall_paint_candidates(ref, fin, np.ones((H, W), bool), [wall_mask()], None, window) == []


def test_a_room_whose_whole_cast_was_neutralised_is_not_reported():
    # fails if a blue-lit room (wall AND ceiling blue in the brackets, both neutral in the finished photo)
    # yields a paint candidate: that is white balance, and the QC prompt would have the agent recolour a
    # clean wall (found by review of the wall check, reproduced with these values)
    ref = lab_image((50, -1.5, -5.5), (48, -1.5, -5.5))  # ceiling rows share the wall's blue cast
    fin = lab_image((75, -0.5, 2.0), (75, -0.5, 2.0))
    ceiling = np.zeros((H, W), bool)
    ceiling[:8] = True
    assert wall_paint_candidates(ref, fin, np.ones((H, W), bool), [wall_mask()], ceiling) == []
