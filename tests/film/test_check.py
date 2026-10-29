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
