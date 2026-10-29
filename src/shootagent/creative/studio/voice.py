"""Voiceover with macOS `say` (no OpenAI): one file per line, cached by voice, rate and text.

The schema's voice names are OpenAI's; each maps onto the most natural installed English voice that fits it
(Samantha and Daniel first; the novelty voices never). `say` gives no word timings, so captions get them from the
audio itself: the line's voiced stretches are found from its energy, the words are split into phrases at
punctuation, and when the counts agree each phrase is spread over its own stretch by character length;
otherwise the words are spread over the whole voiced span. That keeps captions on the right word across pauses.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .audio import SR, highpass, lufs, read_wav

VOICES = {  # schema VoiceName -> macOS voice, best first
    "alloy": ("Samantha", "Daniel"), "coral": ("Samantha", "Karen"), "nova": ("Samantha", "Karen"),
    "shimmer": ("Samantha", "Moira"), "sage": ("Karen", "Samantha"), "ash": ("Daniel", "Samantha"),
    "ballad": ("Daniel", "Samantha"), "echo": ("Daniel", "Samantha"), "fable": ("Daniel", "Samantha"),
    "onyx": ("Daniel", "Samantha"), "verse": ("Daniel", "Samantha"),
}
FAST = ("fast", "energetic", "hype", "excited", "punchy", "urgent", "upbeat", "playful", "rapid", "quick", "bright")
SLOW = ("slow", "calm", "soft", "deadpan", "dry", "whisper", "luxur", "breathy", "intimate", "measured", "warm",
        "hushed", "low")


class VoiceError(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    t0: float  # seconds from the start of its line
    t1: float


@dataclass
class Clip:
    text: str
    samples: np.ndarray  # mono float64 at SR, trimmed to the speech
    words: list[Word]

    @property
    def seconds(self) -> float:
        return len(self.samples) / SR


@lru_cache(maxsize=1)
def installed() -> frozenset[str]:
    if not shutil.which("say"):
        return frozenset()
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    # Lines read "Samantha            en_US    # ..." or, on newer systems, "Samantha (English (US)) en_US  # ...".
    names = set()
    for line in out.splitlines():
        first = re.split(r"\s{2,}|\s+[a-z]{2}_[A-Z]{2}\b", line.strip())[0]
        if first:
            names.add(re.sub(r"\s*\(.*$", "", first).strip())
    return frozenset(names)


def pick_voice(name: str) -> str:
    have = installed()
    for v in VOICES.get(name, ("Samantha", "Daniel")) + ("Samantha", "Daniel"):
        if v in have:
            return v
    raise VoiceError("no natural English voice is installed for `say` (Samantha or Daniel)")


def rate_for(direction: str | None) -> int:
    d = (direction or "").lower()
    if any(w in d for w in FAST):
        return 196
    if any(w in d for w in SLOW):
        return 168
    return 182


def speakable(text: str) -> str:
    """Text as it should be said: units and shorthand a listing uses, read out in words."""
    t = re.sub(r"\bsq\.?\s*ft\.?", "square feet", text, flags=re.I)
    t = re.sub(r"\bw/\s*", "with ", t)
    t = t.replace("&", " and ").replace("*", "")
    return re.sub(r"\s+", " ", t).strip()


def _say(text: str, voice: str, rate: int, out: Path) -> None:
    if not shutil.which("say"):
        raise VoiceError("macOS `say` is not available on this machine")
    with tempfile.TemporaryDirectory() as tmp:
        part = Path(tmp) / "line.wav"
        try:
            r = subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(part), "--file-format=WAVE",
                                "--data-format=LEI16@44100", "--", text], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            raise VoiceError(f"`say` failed: {e}") from e
        if r.returncode != 0 or not part.exists() or part.stat().st_size < 1000:
            raise VoiceError(f"`say` failed: {r.stderr.decode(errors='replace').strip()[:200]}")
        shutil.move(str(part), out)


def _segments(x: np.ndarray, min_gap: float = 0.09) -> list[tuple[float, float]]:
    """Voiced stretches (s) of a line: 10 ms frames above a level relative to the line's loudest frames."""
    hop = int(0.01 * SR)
    n = len(x) // hop
    if n == 0:
        return []
    e = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    thr = max(1e-4, np.percentile(e, 95) * 0.06)
    on = e > thr
    segs, start, gap = [], None, 0
    for i, v in enumerate(on):
        if v:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap * 0.01 >= min_gap:
                segs.append((start * 0.01, (i - gap + 1) * 0.01))
                start, gap = None, 0
    if start is not None:
        segs.append((start * 0.01, (n - gap) * 0.01))
    return [(a, b) for a, b in segs if b - a >= 0.04]


def _spread(words: list[str], t0: float, t1: float) -> list[Word]:
    weights = [len(re.sub(r"[^\w$%]", "", w)) + 1.5 for w in words]
    total = sum(weights) or 1.0
    out, t = [], t0
    for w, k in zip(words, weights):
        d = (t1 - t0) * k / total
        out.append(Word(w, t, t + d))
        t += d
    return out


def word_times(text: str, x: np.ndarray) -> list[Word]:
    words = text.split()
    if not words:
        return []
    segs = _segments(x) or [(0.0, len(x) / SR)]
    phrases, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[,.;:!?—–]$", w) or w in ("-", "—"):
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    if len(phrases) == len(segs) and len(segs) > 1:
        out = []
        for ph, (a, b) in zip(phrases, segs):
            out += _spread(ph, a, b)
        return out
    return _spread(words, segs[0][0], segs[-1][1])


def _trim(x: np.ndarray) -> np.ndarray:
    segs = _segments(x, min_gap=0.3)
    if not segs:
        return x
    a = max(0, int((segs[0][0] - 0.01) * SR))
    b = min(len(x), int((segs[-1][1] + 0.06) * SR))
    return x[a:b]


def clip_for(text: str, voice: str, rate: int, cache: Path) -> Clip:
    said = speakable(text)
    key = hashlib.sha1(f"{voice}|{rate}|{said}".encode()).hexdigest()[:16]
    cache.mkdir(parents=True, exist_ok=True)
    wav = cache / f"{key}.wav"
    if not wav.exists():
        _say(said, voice, rate, wav)
    x = _trim(read_wav(wav))
    x = highpass(x, 85)
    gain = 10 ** ((-18.0 - lufs(x)) / 20) if len(x) > SR // 5 else 1.0
    x = np.clip(x * gain, -0.95, 0.95)
    return Clip(text, x, word_times(text, x))


def synthesize(lines: dict[str, str], name: str, direction: str | None, cache: Path) -> dict[str, Clip]:
    """Every line spoken by the voice mapped from `name`, in parallel (each `say` call is mostly startup)."""
    voice, rate = pick_voice(name), rate_for(direction)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {k: pool.submit(clip_for, t, voice, rate, cache) for k, t in lines.items() if t and t.strip()}
        return {k: f.result() for k, f in futures.items()}
