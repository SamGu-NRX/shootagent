"""Narration timing and the script fact guard: components with one right answer."""

from __future__ import annotations

import pytest

from shootagent.film.assemble import frame_counts
from shootagent.film.narration import segment_starts, unsupported_facts
from shootagent.store import Listing


def _words(text: str, start: float = 0.4, step: float = 0.35) -> list[dict]:
    return [{"w": w, "s": round(start + i * step, 3), "e": round(start + i * step + 0.3, 3)} for i, w in enumerate(text.split())]


def test_each_segment_starts_on_its_first_spoken_word():
    """Fails if a shot cuts in early or late against its narration."""
    segments = ["Morning light, open fields.", "Step into the primary suite.", "Book your tour today."]
    words = _words("Morning light open fields Step into the primary suite Book your tour today")
    starts = segment_starts(segments, words, total=6.0)
    assert starts[0] == 0.0  # the picture covers the breath before the first word
    assert starts[1] == pytest.approx(words[4]["s"])  # "Step"
    assert starts[2] == pytest.approx(words[9]["s"])  # "Book"


def test_a_segment_the_recogniser_misheard_is_placed_between_its_neighbours():
    """Fails if one misheard segment (digits for words) collapses or reorders the shots after it."""
    segments = ["Welcome home.", "Three tall windows.", "Book your tour today."]
    words = _words("Welcome home 3 tool wind ohs Book your tour today")
    starts = segment_starts(segments, words, total=5.0)
    assert starts[0] < starts[1] < starts[2]
    assert words[1]["s"] < starts[1] < words[6]["s"]
    assert starts[2] == pytest.approx(words[6]["s"])


def test_shot_frames_tile_the_narration_exactly():
    """Fails if the picture track drifts from the narration: frames must sum to the audio length."""
    counts = frame_counts([0.0, 2.37, 7.91, 15.02], total=32.61, fps=30)
    assert sum(counts) == round(32.61 * 30)
    assert counts[0] == round(2.37 * 30)


def test_script_may_not_state_counts_size_or_price_the_listing_lacks():
    """Fails if an invented bed count, size or price passes the guard."""
    bare = Listing()
    text = "Four bedrooms, 2 baths and 2,400 sq ft, offered at $650,000."
    found = " ".join(unsupported_facts(text, bare))
    for field in ("beds", "baths", "sqft", "price"):
        assert field in found
    given = Listing(beds=4, baths=2, sqft=2400, price="$650,000")
    assert unsupported_facts(text, given) == []
    assert unsupported_facts("0 bedrooms, 2 baths", Listing(beds=0, baths=2)) == []  # zero is a stated fact


def test_visual_facts_and_spanish_articles_are_not_counts():
    """Fails if the guard rejects the recorded scripts: 'three tall windows', 'una segunda recámara'."""
    assert unsupported_facts("framed by three tall windows, a second bedroom behind French doors", Listing()) == []
    assert unsupported_facts("una segunda recámara detrás de puertas francesas y un baño tipo spa", Listing()) == []
    assert unsupported_facts("tres recámaras y dos baños", Listing())


def test_a_short_plan_lowers_the_minimum_and_no_line_may_outrun_its_shot():
    """Fails if a script for a short plan must be padded to 28 s (the demo's last shot then held ~8 s), or if a
    line longer than a shot should hold is accepted."""
    from shootagent.film.narration import script_problems

    lines = ["Open prairie views from the table."] + ["A bright room with tall windows and soft light."] * 5
    problems = script_problems(lines, "en", Listing(), 6, shot_seconds=[3.5] * 6)
    assert problems == [], problems  # ~19 s for six 3.5 s shots: fine, no padding needed
    assert any("never pad" in p for p in script_problems(lines, "en", Listing(), 6))  # without a plan: 28 s floor
    long = lines[:-1] + ["A game room with a round table and a bar with three pendants, then three bedrooms and two baths."]
    assert any("segments [5]" in p for p in script_problems(long, "en", Listing(), 6, shot_seconds=[3.5] * 6))
