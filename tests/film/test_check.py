"""Which judge findings fail a generated clip."""

from __future__ import annotations

from shootagent.film.check import counts_against


def _f(kind: str, confidence: str, in_view: bool = True) -> dict:
    return {"kind": kind, "confidence": confidence, "in_photo_view": in_view}


def test_missing_counts_only_at_high_confidence_while_new_and_changed_count_at_medium():
    """Fails if medium-confidence 'missing' findings (every false alarm on good clips in the experiment) fail a
    clip again, or if a confident invented or reshaped fixture stops failing it."""
    assert not counts_against(_f("missing", "medium"), strict=True)
    assert counts_against(_f("missing", "high"), strict=True)
    assert counts_against(_f("new", "medium"), strict=True)
    assert counts_against(_f("changed", "medium"), strict=True)
    assert not counts_against(_f("changed", "low"), strict=True)


def test_a_new_item_outside_every_photo_fails_only_in_strict_mode():
    """Fails if an unverifiable fixture (T3's switch plate, on wall the photo cuts off) passes in strict mode."""
    assert counts_against(_f("new", "high", in_view=False), strict=True)
    assert not counts_against(_f("new", "high", in_view=False), strict=False)


def test_a_room_to_room_walk_the_tracking_cannot_follow_never_passes_even_if_the_judge_does(tmp_path):
    """Fails if a clip with an end photo that the tracking checks cannot follow passes on the judge alone (the
    judge passed the demo's bedroom-to-bath walk, which added a person in the shower window)."""
    import shutil

    import numpy as np
    import pytest

    from shootagent.film.check import ROOM_TO_ROOM, check_clip
    from shootagent.film.video import VideoWriter

    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")

    def room(seed):
        r = np.random.default_rng(seed)
        img = np.full((540, 960, 3), 200, np.uint8)
        for _ in range(60):
            y, x = r.integers(0, 500), r.integers(0, 900)
            img[y:y + 40, x:x + 60] = r.integers(0, 255, 3)
        return img

    start, end = room(1), room(2)
    clip = tmp_path / "walk.mp4"
    with VideoWriter(clip, 960, 540, 24) as vw:
        for k in range(48):
            vw.write(start if k < 24 else end)
    passing_judge = lambda photo, end_photo, frames: {"pass": True, "failing": [], "changes": [], "usages": [], "usd": 0.0}
    result = check_clip(clip, start, end, judge=passing_judge)
    assert not result.passed
    assert ROOM_TO_ROOM in result.reasons
