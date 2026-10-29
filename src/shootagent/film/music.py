"""The film's music bed: a Lyria track from music/ (or the generated synth), levelled for use under a voice.

The Lyria tracks (Gemini app, 28 October 2026; lab/music/NOTES.md on explore/media-tests) are mastered loud:
-11.4 and -12.3 LUFS integrated with true peaks of +0.1 and +1.0 dBFS. Under a narration that is far too hot,
and the overs clip once anything is added on top. So the bed is brought to BED_LUFS and limited to BED_PEAK
before the finisher ducks it under the voice and normalises the mix to -14 LUFS.

"first-light-on-oak" has wordless vocal pads from 0:27; a film longer than its clean stretch gets the
default track instead, and the manifest notes the swap.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

from ..reel.music import SR, synth_track, write_wav
from .video import run_ffmpeg

MUSIC = Path(__file__).with_name("music")
TRACKS: dict[str, dict] = json.loads((MUSIC / "tracks.json").read_text(encoding="utf-8"))
DEFAULT = "cinematic-calm"
# A bed that sits under speech: well below the -14 LUFS mix before ducking takes it further down. One loudnorm
# pass lands near, not on, the target: The Morning Foyer measured -18.2 LUFS, -6.2 dBFS peak for a 34 s bed.
# The limiter keeps the decoded mp3's overs (up to +1.0 dBFS) from clipping. Not tuned by listening tests.
BED_LUFS = -20.0
BED_PEAK = 0.7  # linear, about -3 dBFS
FADE_S = 1.5


def choose(track: str | None, seconds: float) -> tuple[str, list[str]]:
    """The track to use for a film of `seconds`, and a note when the requested one was swapped."""
    if track == "synth":
        return track, []
    want = track or DEFAULT
    if want not in TRACKS:
        return DEFAULT, [f"unknown music {want!r}; used {DEFAULT}"]
    clean = TRACKS[want]["vocal_free_s"]
    if seconds > clean:
        return DEFAULT, [f"{want} has vocals after {clean:g} s and this film runs {seconds:.1f} s; used {DEFAULT}"]
    return want, []


def prepare_bed(track: str | None, seconds: float, out: Path) -> tuple[Path, np.ndarray, str, list[str]]:
    """Write the bed for a film of `seconds` to `out` (44.1 kHz stereo WAV). Returns (path, samples float32
    (n, 2), track id, notes)."""
    chosen, notes = choose(track, seconds)
    if chosen == "synth":
        samples = synth_track(seconds)
        write_wav(out, samples)
        return out, samples, chosen, notes
    src = MUSIC / TRACKS[chosen]["file"]
    length = min(seconds, TRACKS[chosen]["duration_s"])
    af = (f"loudnorm=I={BED_LUFS}:TP=-3:LRA=11,alimiter=limit={BED_PEAK}:level=false,"
          f"afade=t=out:st={max(0.0, length - FADE_S):.2f}:d={FADE_S}")
    run_ffmpeg(["-i", str(src), "-t", f"{length:.3f}", "-af", af, "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le",
                str(out)], f"levelling the {chosen} music bed")
    with wave.open(str(out), "rb") as w:
        samples = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(np.float32) / 32768
    return out, samples, chosen, notes
