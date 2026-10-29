"""The studio renderer (creative/render.py, creative/studio/): every value of the reel language renders, the video
is as long as its timeline, renders repeat exactly, the score and effects are sane, `say` gives timed words, and a
card falls back to PIL when node is unavailable. Renders are tiny (270x480 at 10 fps) on a synthetic shoot."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from shootagent.config import get_settings
from shootagent.creative.context import LabContext
from shootagent.creative.render import render_concept
from shootagent.creative.schema import Beat, CardSpec, Ending, Hook, LabOptions, Music, ReelConcept, Voice
from shootagent.creative.studio import audio, cards, kinetic, music, plan, sfx
from shootagent.creative.studio import voice as tts
from shootagent.creative.studio.look import look_for
from shootagent.store import Listing, ShootStore, View

MOTIONS = ["push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "punch_in", "static"]
TRANSITIONS = ["cut", "crossfade", "whip", "flash", "zoom_through", "dip_black"]
STYLES = ["pop_words", "typewriter", "bold_center", "sticker", "redacted", "label", "countdown"]
POSITIONS = ["top", "center", "bottom"]
SFX = ["none", "whoosh", "pop", "riser", "record_scratch", "bass_drop", "click", "ding"]
CARDS = ["none", "price_tag", "stat_count_up", "list_reveal", "big_quote", "typing_chat"]
ENDINGS = ["end_card", "loop", "question", "price_reveal", "punchline"]
MOODS = ["warm", "upbeat", "suspense", "lofi", "cinematic", "playful", "luxe"]
SMALL = dict(width=270, height=480, fps=10)


def _photo(w: int, h: int, seed: int) -> np.ndarray:
    """A bright, textured stand-in for a room photo."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([200 + 30 * np.sin(x / 37.0 + seed), 190 + 25 * np.cos(y / 53.0), 170 + 20 * np.sin((x + y) / 71.0)], -1)
    return np.clip(base + rng.normal(0, 6, (h, w, 3)), 0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def shoot(tmp_path_factory) -> tuple[ShootStore, str]:
    root = tmp_path_factory.mktemp("shoots")
    store = ShootStore(root)
    s = store.create("Test flat", shoot_id="testshoot1")
    s.listing = Listing(address="Unit 4B, demo listing", price="$489,000", beds=1, baths=1, sqft=640)
    s.views = [View(id=f"v0{i}", label=f"Room {i}", room=f"room {i}", approved=True) for i in range(1, 5)]
    store.save(s)
    for i, (w, h) in enumerate([(600, 900), (900, 600), (600, 900), (900, 600)], start=1):
        store.add_version(s.id, f"v0{i}", _photo(w, h, i), op="ingest")
    return store, s.id


def _ctx(shoot, lab_dir: Path) -> tuple[LabContext, list[tuple[str, str]]]:
    store, sid = shoot
    seen: list[tuple[str, str]] = []
    ctx = LabContext(store=store, shoot_id=sid, settings=get_settings(), lab_dir=lab_dir, options=LabOptions(),
                     client=None, emit_fn=lambda m: None, progress_fn=lambda phase, d: seen.append((phase, d)))
    lab_dir.mkdir(parents=True, exist_ok=True)
    return ctx, seen


@pytest.fixture
def no_node(monkeypatch):
    """Cards drawn with PIL: fast, and the path a machine without node takes."""
    monkeypatch.setattr(cards, "node_ok", lambda: False)


def _card(kind: str) -> CardSpec | None:
    return {
        "none": None,
        "price_tag": CardSpec(kind="price_tag", value="$489,000", text="1 bed · 1 bath · 640 sq ft"),
        "stat_count_up": CardSpec(kind="stat_count_up", value="640", text="square feet"),
        "list_reveal": CardSpec(kind="list_reveal", text="Clues", items=["Stone kitchen", "Lantern", "Niche"]),
        "big_quote": CardSpec(kind="big_quote", text="The wall glows at night", highlight="glows"),
        "typing_chat": CardSpec(kind="typing_chat", text="is the niche lit?", items=["Every night."]),
    }[kind]


def concept_for(i: int, beats: int = 3) -> ReelConcept:
    """Case i uses the i-th value of every Literal (wrapping), so 8 cases cover every value at least once."""
    pick = lambda xs, k=0: xs[(i + k) % len(xs)]  # noqa: E731
    bs = []
    for k in range(beats):
        bs.append(Beat(view=f"v0{(i + k) % 4 + 1}", seconds=1.6, motion=pick(MOTIONS, k), focus=[0.6, 0.4],
                       transition=pick(TRANSITIONS, k), text=f"{k + 1}. Shot number {k + 1}" if k != 1 else None,
                       text_style=pick(STYLES, k), text_pos=pick(POSITIONS, k), sfx=pick(SFX, k),
                       card=_card(CARDS[i % len(CARDS)]) if k == 1 else None))
    return ReelConcept(id=f"c{i}", agent="test", round=1, title=f"Case {i}", logline="", format="test",
                       hook=Hook(text="This flat has a *secret*", style=pick(STYLES, 3), sfx=pick(SFX, 5)),
                       beats=bs, ending=Ending(style=pick(ENDINGS), text="Schedule a tour"),
                       music=Music(mood=pick(MOODS), bpm=90 + 7 * i, drop_beat=1 if i % 2 else None))


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _md5(path: Path, stream: str) -> str:
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{stream}", "-f", "md5", "-"],
                          capture_output=True, text=True, check=True).stdout.strip()


# ---- whole renders ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("i", range(8))
def test_every_literal_renders(shoot, tmp_path, no_node, i):
    concept = concept_for(i)
    ctx, seen = _ctx(shoot, tmp_path / "lab")
    reel = render_concept(ctx, concept, "B", "Case", "creative", voice=False, **SMALL)
    video, poster = ctx.lab_dir / reel.video, ctx.lab_dir / reel.poster
    assert video.is_file() and poster.is_file()
    info = _probe(video)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert info["streams"][0]["width"] == 270 and info["streams"][0]["height"] == 480
    duration = float(info["format"]["duration"])
    end = reel.timeline[-1].t1
    assert abs(duration - end) <= 0.15 and abs(reel.seconds - end) <= 0.15
    kinds = [e.kind for e in reel.timeline]
    assert kinds[0] == "hook" and kinds[-1] == "ending" and kinds.count("beat") == len(concept.beats)
    beats = [e for e in reel.timeline if e.kind == "beat"]
    assert all(a.t1 == pytest.approx(b.t0) for a, b in zip(beats, beats[1:]))
    assert reel.music == f"{concept.music.mood}, {concept.music.bpm} bpm" and not reel.has_voice
    assert any(phase == "render" for phase, _ in seen)
    if concept.beats[1].card:
        assert concept.beats[1].card.kind in reel.cards


def test_all_literals_covered():
    cases = [concept_for(i) for i in range(8)]
    used = {
        "motion": {b.motion for c in cases for b in c.beats}, "transition": {b.transition for c in cases for b in c.beats},
        "style": {b.text_style for c in cases for b in c.beats} | {c.hook.style for c in cases},
        "pos": {b.text_pos for c in cases for b in c.beats}, "sfx": {b.sfx for c in cases for b in c.beats},
        "card": {(b.card.kind if b.card else "none") for c in cases for b in c.beats},
        "ending": {c.ending.style for c in cases}, "mood": {c.music.mood for c in cases},
    }
    assert used["motion"] == set(MOTIONS) and used["transition"] == set(TRANSITIONS)
    assert used["style"] == set(STYLES) and used["pos"] == set(POSITIONS) and used["sfx"] == set(SFX)
    assert used["card"] == set(CARDS) and used["ending"] == set(ENDINGS) and used["mood"] == set(MOODS)


def test_render_is_deterministic(shoot, tmp_path, no_node):
    concept = concept_for(3, beats=2)
    hashes = []
    for run in ("one", "two"):
        ctx, _ = _ctx(shoot, tmp_path / run)
        reel = render_concept(ctx, concept, "C", "Twice", "creative", voice=False, **SMALL)
        video = ctx.lab_dir / reel.video
        hashes.append((_md5(video, "v"), _md5(video, "a")))
    assert hashes[0] == hashes[1]


def test_card_falls_back_to_pil_without_node(shoot, tmp_path, no_node):
    concept = concept_for(1, beats=2)  # price_tag on beat 2
    ctx, _ = _ctx(shoot, tmp_path / "lab")
    reel = render_concept(ctx, concept, "B", "Fallback", "creative", voice=False, **SMALL)
    assert "price_tag" in reel.cards
    assert any("without Reel Studio" in w for w in reel.warnings)
    assert (ctx.lab_dir / reel.video).is_file()


@pytest.mark.parametrize("kind", [k for k in CARDS if k != "none"])
def test_fallback_cards_draw(kind):
    cv = kinetic.Canvas(270, 480)
    look = look_for("upbeat")
    region = cards.region_for(None)
    params = cards.params_for(_card(kind), "$489,000", look, 2.0, region)
    job = cards.CardJob(kind, params, 0.0, 2.0, _card(kind), region)
    frame = np.full((480, 270, 3), 128, np.uint8)
    cards.fallback(job, cv, look).draw(frame, 1.5)
    assert (frame != 128).any()


def test_voice_failure_renders_silently(shoot, tmp_path, no_node, monkeypatch):
    def boom(*a, **k):
        raise tts.VoiceError("no voice here")
    monkeypatch.setattr(tts, "synthesize", boom)
    concept = concept_for(0, beats=2).model_copy(update={"voice": Voice(voice="nova")})
    concept.hook.voiceover = "Hello there."
    ctx, _ = _ctx(shoot, tmp_path / "lab")
    reel = render_concept(ctx, concept, "B", "Silent", "creative", voice=True, **SMALL)
    assert not reel.has_voice and reel.voiceover is None
    assert any("without a voiceover" in w for w in reel.warnings)


@pytest.mark.skipif(not shutil.which("say"), reason="macOS `say` is not available")
def test_voiceover_with_say_extends_beats_and_captions(shoot, tmp_path, no_node):
    concept = concept_for(2, beats=2).model_copy(update={"voice": Voice(voice="alloy", direction="calm")})
    concept.hook.voiceover = "This flat is hiding something."
    concept.beats[0].voiceover = "A long line that will not fit inside a very short shot of one second at all."
    concept.beats[0].seconds = 1.0
    ctx, _ = _ctx(shoot, tmp_path / "lab")
    reel = render_concept(ctx, concept, "B", "Voice", "creative", voice=True, **SMALL)
    assert reel.has_voice and "hiding something" in reel.voiceover
    assert any("extended" in w for w in reel.warnings)
    first = next(e for e in reel.timeline if e.kind == "beat")
    assert first.t1 - first.t0 > 2.0 and first.voiceover
    assert abs(float(_probe(ctx.lab_dir / reel.video)["format"]["duration"]) - reel.timeline[-1].t1) <= 0.15


@pytest.mark.skipif(not cards.node_ok(), reason="node or Playwright is not installed for Reel Studio")
def test_reel_studio_card_renders_and_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a relative lab dir: node runs elsewhere, so the output path must be absolute
    look = look_for("warm")
    region = cards.region_for(None)
    params = cards.params_for(_card("stat_count_up"), None, look, 1.0, region)
    job = cards.CardJob("stat_count_up", params, 0.0, 1.0, _card("stat_count_up"), region)
    assert cards.render_all([job], Path("cards")) == [None] and job.path and job.path.exists()
    assert job.path.parent == (tmp_path / "cards").resolve()
    clip = cards.load(job.path, 270, 480, 0.0, 1.0)
    frame = np.zeros((480, 270, 3), np.uint8)
    clip.draw(frame, 0.8)
    assert frame.any()
    again = cards.CardJob("stat_count_up", params, 0.0, 1.0, _card("stat_count_up"), region)
    assert cards.render_all([again], Path("cards")) == [None] and again.path == job.path  # cached by params


# ---- the parts ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mood", MOODS)
def test_score_length_level_and_peak(mood):
    x = music.score(mood, 110, 6.0, drop=3.27)
    assert x.shape == (round(6.0 * audio.SR), 2)
    assert np.isfinite(x).all() and np.abs(x).max() <= 0.81
    assert -17.5 < audio.lufs(x) < -14.5


@pytest.mark.parametrize("kind", sfx.KINDS)
def test_sfx_are_finite_and_bounded(kind):
    x, anchor = sfx.make(kind, 3, 1.2)
    assert x.ndim == 2 and x.shape[1] == 2 and len(x) > 100
    assert np.isfinite(x).all() and 0.3 < np.abs(x).max() <= 1.0
    assert 0.0 <= anchor <= len(x) / audio.SR
    y, _ = sfx.make(kind, 3, 1.2)
    assert np.array_equal(x, y)


def test_plan_snaps_cuts_lands_the_drop_and_extends_for_voice():
    beats = [Beat(view="v01", seconds=2.05, motion="push_in"), Beat(view="v02", seconds=1.9, motion="pan_left"),
             Beat(view="v03", seconds=1.0, motion="static", voiceover="x"), Beat(view="v04", seconds=2.0, motion="static")]
    c = ReelConcept(id="p", agent="t", round=1, title="t", logline="", format="", beats=beats,
                    hook=Hook(text="Hook", style="pop_words"), ending=Ending(style="end_card", text="Tour"),
                    music=Music(mood="upbeat", bpm=120, drop_beat=3))
    p = plan.make_plan(c, ["v01", "v02", "v03", "v04"], {"b2": 2.0})
    starts = [s.start for s in p.shots]
    bars = (p.drop - p.grid.phase) / p.grid.bar  # the drop sits on a bar line (of a shifted grid if need be)
    assert p.drop is not None and abs(bars - round(bars)) < 1e-6
    assert p.shots[3].motion == "punch_in" and any(e.kind == "bass_drop" for e in p.effects)
    assert p.shots[2].end - p.shots[2].start >= 0.12 + 2.0 + 0.2 - 1e-6
    assert any("extended" in w for w in p.warnings)
    assert all(b > a for a, b in zip(starts, starts[1:]))
    assert p.lines[0].at == pytest.approx(p.shots[2].start + plan.LINE_LEAD)


def test_text_stays_in_the_safe_area():
    cv = kinetic.Canvas(1080, 1920, captions=True)
    look = look_for("upbeat")
    for style in STYLES:
        for pos in POSITIONS:
            frame = np.zeros((1920, 1080, 3), np.uint8)
            kinetic.build(style, "A long line of words that has to wrap onto more lines", pos, 0.0, 3.0, cv, look,
                          number=3).draw(frame, 1.5)
            ys, xs = np.nonzero(frame.max(axis=2) > 200)  # the type itself, not its soft shadow
            assert len(ys), (style, pos)
            assert ys.min() >= 1920 * 0.09 and ys.max() <= 1920 * 0.83, (style, pos)
            assert xs.max() <= 1080 * 0.90, (style, pos)
