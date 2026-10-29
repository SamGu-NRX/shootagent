"""Beat tracking on the music bed and cuts snapped to beats; listing cards' params."""

from __future__ import annotations

import numpy as np

from shootagent.film.assemble import card_params
from shootagent.film.beats import beat_times, snap_cuts
from shootagent.film.spec import FilmSpec
from shootagent.store import Listing


def test_beats_land_on_the_clicks_of_a_known_track():
    """Fails if detected beats drift from the music: a cut 'on the beat' would then land off it."""
    sr = 22050
    truth = np.arange(0.25, 12, 0.6)
    x = np.zeros(sr * 12)
    noise = np.random.default_rng(1).standard_normal(200)
    for t in truth:
        x[int(t * sr):int(t * sr) + 200] = noise
    beats = np.array(beat_times(x, sr))
    assert len(beats) >= len(truth) - 1
    assert np.abs(beats[: len(truth)] - truth[: len(beats)]).max() < 0.05


def test_cuts_move_to_the_nearest_beat_only_within_the_window_and_keep_shots_long_enough():
    """Fails if a cut jumps further than the window from where the narration put it, reorders shots, or
    leaves a shot shorter than the minimum."""
    beats = [0.7, 1.4, 2.1, 2.8, 3.5, 4.2, 4.9]
    starts = [0.0, 1.3, 2.45, 4.6]
    out = snap_cuts(starts, beats, window=0.35, min_len=1.2)
    # 1.3 -> 1.4 (0.1 s away); 2.45 -> 2.8, since 2.1 would leave the previous shot 0.7 s long; 4.6 -> 4.9.
    assert out == [0.0, 1.4, 2.8, 4.9]
    assert snap_cuts([0.0, 3.0], [1.0], window=0.35) == [0.0, 3.0]  # no beat near: the cut stays


def test_cards_use_only_listing_fields_and_fit_the_templates_limits():
    """Fails if a card states a fact the listing does not give, or sends a value longer than Reel Studio's
    templates accept (price-tag: value 12, name 30, text 40), which the renderer rejects."""
    assert card_params(FilmSpec(), "en") == {}
    li = Listing(price="$549,000", beds=3, baths=2, sqft=2140, headline="A headline far longer than thirty characters",
                 address="Sample Home, High Plains")
    cards = card_params(FilmSpec(listing=li), "es")
    t = cards["title"]["params"]
    assert len(t["value"]) <= 12 and len(t["name"]) <= 30 and len(t["text"]) <= 40
    assert t["name"] == "Sample Home" and "rec" in t["text"] and "High Plains" in t["text"]
    assert cards["stat"]["params"]["value"] == "2,140"
    assert "stat" not in card_params(FilmSpec(listing=Listing(price="$1")), "en")
