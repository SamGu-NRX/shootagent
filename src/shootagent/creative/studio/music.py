"""Generated scores for the seven music moods, so every reel ships with licence-free music cut to its edit.

Extends reel/music.py (warm pads, a plucked arpeggio, convolution reverb) into a small arranger: drum, bass, chord
and lead parts per mood, a chord progression per bar, and sections around the concept's drop:

    pre   before the drop: no kick or bass, chords filtered (tension)
    build the bar before the drop: a snare or tom roll and a riser, then an eighth of silence
    main  from the drop (or the top, with no drop): the full groove

The bar grid is placed so a bar line lands exactly on the drop, and the plan snaps cuts to the same grid.
Everything is seeded, so a concept always gets the same samples. Mastered to -16 LUFS here; the final mix with
voice and effects is mastered again in engine.py.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import fftconvolve

from .audio import SR, fade, filt, highpass, lowpass, master, place

MOODS = ("warm", "upbeat", "suspense", "lofi", "cinematic", "playful", "luxe")


@dataclass(frozen=True)
class Grid:
    """The music's beat grid: beat n sits at phase + n * spb; bars start on beats divisible by 4."""

    bpm: float
    phase: float = 0.0

    @property
    def spb(self) -> float:
        return 60.0 / self.bpm

    @property
    def bar(self) -> float:
        return 4 * self.spb

    def beat(self, n: float) -> float:
        return self.phase + n * self.spb

    def nearest(self, t: float) -> float:
        return self.beat(round((t - self.phase) / self.spb))

    def ticks(self, t0: float, t1: float, n: int) -> list[float]:
        """n landing times from t0 on eighths (or sixteenths when eighths do not fit before t1)."""
        for div in (2, 4):
            step = self.spb / div
            first = self.phase + math.ceil((t0 - self.phase) / step - 1e-6) * step
            if first + (n - 1) * step <= t1 or div == 4:
                if first - t0 > step * 0.6:
                    first = t0
                return [first + i * step for i in range(n)]
        return []


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


# ---- oscillators and envelopes -----------------------------------------------------------------------------

def _t(n: int) -> np.ndarray:
    return np.arange(n) / SR


def saw(f: float, n: int, phase: float = 0.0) -> np.ndarray:
    """Band-limited sawtooth (polyBLEP), so bright pads do not alias."""
    dt = f / SR
    p = (phase + dt * np.arange(n)) % 1.0
    y = 2 * p - 1
    lo, hi = p < dt, p > 1 - dt
    x = p[lo] / dt
    y[lo] -= x + x - x * x - 1
    x = (p[hi] - 1) / dt
    y[hi] -= x * x + x + x + 1
    return y


def sine(f: float, n: int, phase: float = 0.0) -> np.ndarray:
    return np.sin(2 * math.pi * f * _t(n) + phase)


def adsr(n: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    env = np.full(n, s, dtype=np.float64)
    na, nd, nr = min(n, int(a * SR)), int(d * SR), min(n, int(r * SR))
    if na:
        env[:na] = np.linspace(0, 1, na, endpoint=False)
    if nd and na < n:
        k = min(nd, n - na)
        env[na:na + k] = 1 - (1 - s) * (1 - np.exp(-np.arange(k) / max(1, nd) * 4))
    if nr > 1:
        env[n - nr:] *= np.linspace(1, 0, nr) ** 2
    return env


def decay(n: int, tau: float) -> np.ndarray:
    return np.exp(-_t(n) / tau)


# ---- instruments ------------------------------------------------------------------------------------------

@dataclass
class Kit:
    """Drum samples, synthesised once per score."""

    rng: np.random.Generator
    cache: dict = field(default_factory=dict)

    def noise(self, n: int) -> np.ndarray:
        return self.rng.standard_normal(n)

    def kick(self, tone: float = 50.0, punch: float = 150.0, length: float = 0.45, click: float = 0.35):
        key = ("kick", tone, punch, length, click)
        if key not in self.cache:
            n = int(length * SR)
            t = _t(n)
            f = tone + (punch - tone) * np.exp(-t / 0.035)
            body = np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t / (length * 0.45))
            c = highpass(self.noise(n), 2000) * np.exp(-t / 0.004) * click
            self.cache[key] = np.tanh(1.6 * (body + c)) / np.tanh(1.6)
        return self.cache[key]

    def snare(self, tone: float = 185.0, length: float = 0.22, crisp: float = 1.0):
        key = ("snare", tone, length, crisp)
        if key not in self.cache:
            n = int(length * SR)
            t = _t(n)
            body = np.sin(2 * math.pi * tone * t) * np.exp(-t / 0.05) * 0.7
            nz = filt(self.noise(n), "bandpass", 3500, 0.6) * np.exp(-t / (length * 0.35)) * crisp
            self.cache[key] = body + nz * 1.4
        return self.cache[key]

    def clap(self, length: float = 0.24):
        key = ("clap", length)
        if key not in self.cache:
            n = int(length * SR)
            t = _t(n)
            env = np.zeros(n)
            for k, at in enumerate((0.0, 0.011, 0.022, 0.034)):
                i = int(at * SR)
                tail = 0.006 if k < 3 else length * 0.3
                env[i:] += np.exp(-(t[: n - i]) / tail) * (0.8 if k < 3 else 1.0)
            self.cache[key] = filt(self.noise(n), "bandpass", 1400, 0.9) * env * 1.6
        return self.cache[key]

    def hat(self, open_: bool = False):
        key = ("hat", open_)
        if key not in self.cache:
            length = 0.32 if open_ else 0.06
            n = int(length * SR)
            t = _t(n)
            metal = sum(np.sign(np.sin(2 * math.pi * f * t)) for f in (317, 421, 553, 671, 803, 1063)) / 6
            sig = highpass(metal * 0.6 + self.noise(n) * 0.5, 7000) * np.exp(-t / (length * 0.33))
            self.cache[key] = sig * 1.4
        return self.cache[key]

    def shaker(self):
        if "shaker" not in self.cache:
            n = int(0.09 * SR)
            t = _t(n)
            env = np.minimum(1, t / 0.012) * np.exp(-t / 0.025)
            self.cache["shaker"] = filt(self.noise(n), "bandpass", 6500, 0.9) * env * 1.6
        return self.cache["shaker"]

    def rim(self):
        if "rim" not in self.cache:
            n = int(0.07 * SR)
            t = _t(n)
            sig = np.sin(2 * math.pi * 1700 * t) * np.exp(-t / 0.012) + highpass(self.noise(n), 3000) * np.exp(-t / 0.006)
            self.cache["rim"] = sig * 0.8
        return self.cache["rim"]

    def tick(self, high: bool = True):
        key = ("tick", high)
        if key not in self.cache:
            n = int(0.05 * SR)
            t = _t(n)
            f = 2400 if high else 1800
            self.cache[key] = (np.sin(2 * math.pi * f * t) * np.exp(-t / 0.006)
                               + highpass(self.noise(n), 4000) * np.exp(-t / 0.002) * 0.5)
        return self.cache[key]

    def tom(self, tone: float = 90.0):
        key = ("tom", tone)
        if key not in self.cache:
            n = int(0.6 * SR)
            t = _t(n)
            f = tone * (1 + 0.6 * np.exp(-t / 0.05))
            body = np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t / 0.22)
            self.cache[key] = np.tanh(1.5 * body + lowpass(self.noise(n), 1500) * np.exp(-t / 0.03) * 0.5)
        return self.cache[key]

    def boom(self):
        if "boom" not in self.cache:
            n = int(2.2 * SR)
            t = _t(n)
            f = 38 + 50 * np.exp(-t / 0.08)
            body = np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t / 0.9)
            hit = lowpass(self.noise(n), 900) * np.exp(-t / 0.12) * 0.7
            self.cache["boom"] = np.tanh(1.8 * (body + hit)) * 0.9
        return self.cache["boom"]


def pluck(f: float, dur: float, bright: float = 0.6, length: float | None = None) -> np.ndarray:
    """A plucked string (harp, pizzicato, guitar-ish): harmonics that die faster the higher they are."""
    n = int((length or dur + 0.5) * SR)
    t = _t(n)
    out = np.zeros(n)
    for k in range(1, 13):
        if f * k > 12000:
            break
        out += (bright ** (k - 1)) / k * np.sin(2 * math.pi * f * k * t) * np.exp(-t * (2.2 + 1.6 * k) / max(0.2, dur))
    return out * np.minimum(1, t / 0.003)


def marimba(f: float, dur: float = 0.6) -> np.ndarray:
    n = int(max(0.5, dur + 0.3) * SR)
    t = _t(n)
    body = np.sin(2 * math.pi * f * t) * np.exp(-t / 0.42) + 0.35 * np.sin(2 * math.pi * f * 3.93 * t) * np.exp(-t / 0.06)
    body += 0.12 * np.sin(2 * math.pi * f * 9.2 * t) * np.exp(-t / 0.02)
    return body * np.minimum(1, t / 0.002)


def bell(f: float, dur: float = 1.6) -> np.ndarray:
    n = int(dur * SR)
    t = _t(n)
    parts = ((1.0, 1.0, 1.0), (2.0, 0.5, 0.6), (2.76, 0.35, 0.35), (5.4, 0.18, 0.18), (8.93, 0.08, 0.1))
    return sum(a * np.sin(2 * math.pi * f * r * t) * np.exp(-t / (dur * d)) for r, a, d in parts) * np.minimum(1, t / 0.002)


def ep(f: float, dur: float, vel: float = 1.0) -> np.ndarray:
    """An electric piano (FM, a Rhodes-like tine): the bark fades fast, the tone rings."""
    n = int((dur + 0.6) * SR)
    t = _t(n)
    idx = (1.6 * vel) * np.exp(-t / 0.25) + 0.2
    tone = np.sin(2 * math.pi * f * t + idx * np.sin(2 * math.pi * f * t))
    tine = 0.15 * vel * np.sin(2 * math.pi * f * 14 * t) * np.exp(-t / 0.02)
    env = np.exp(-t / 1.6) * np.where(t < dur, 1.0, np.exp(-(t - dur) / 0.12))
    return (tone + tine) * env * np.minimum(1, t / 0.002)


def supersaw(f: float, n: int, voices: int = 3, cents: float = 9.0, phase_seed: int = 0) -> np.ndarray:
    out = np.zeros(n)
    for v in range(voices):
        det = (v - (voices - 1) / 2) * cents / max(1, (voices - 1) / 2) if voices > 1 else 0
        out += saw(f * 2 ** (det / 1200), n, phase=((phase_seed * 7 + v * 3) % 10) / 10)
    return out / voices


def chord_pad(notes: list[int], dur: float, attack: float, release: float, cutoff: float, voices: int = 3,
              cents: float = 10.0, seed: int = 0) -> np.ndarray:
    n = int((dur + release) * SR)
    out = sum(supersaw(hz(m), n, voices, cents, i + 3 * seed) for i, m in enumerate(notes)) / max(1, len(notes))
    out = lowpass(lowpass(out, cutoff), cutoff * 1.3)
    env = adsr(n, attack, 0.5, 0.85, release)
    return out * env


def stab(notes: list[int], dur: float, cutoff: float) -> np.ndarray:
    n = int((dur + 0.05) * SR)
    out = sum(supersaw(hz(m), n, 2, 12.0, i) for i, m in enumerate(notes)) / max(1, len(notes))
    return lowpass(out, cutoff) * adsr(n, 0.004, dur * 0.6, 0.35, 0.05)


def bass_note(m: int, dur: float, kind: str = "sine", cutoff: float = 600.0) -> np.ndarray:
    n = int((dur + 0.04) * SR)
    f = hz(m)
    if kind == "saw":
        sig = lowpass(saw(f, n) * 0.7 + sine(f, n) * 0.6, cutoff)
    elif kind == "pluck":
        sig = pluck(f, dur, 0.45, dur + 0.04)
    else:
        sig = sine(f, n) + 0.25 * sine(2 * f, n) + 0.08 * sine(3 * f, n)
    return np.tanh(1.3 * sig) * adsr(n, 0.005, dur * 0.5, 0.75, min(0.06, dur * 0.4))


def riser(seconds: float, rng: np.random.Generator) -> np.ndarray:
    n = int(seconds * SR)
    t = np.linspace(0, 1, n)
    noise = rng.standard_normal(n)
    # Filter sweep by blending band-limited copies, cheaper than a time-varying filter and smooth enough.
    lo, mid, hi = lowpass(noise, 600), filt(noise, "bandpass", 2500, 0.7), highpass(noise, 5000)
    sweep = lo * (1 - t) ** 2 + mid * 2 * t * (1 - t) * 1.5 + hi * t ** 2 * 1.4
    f = 180 * 2 ** (3 * t ** 1.5)
    tone = np.sin(2 * math.pi * np.cumsum(f) / SR) * 0.25
    return (sweep * 0.5 + tone) * t ** 2


# ---- arrangement ------------------------------------------------------------------------------------------

@dataclass
class Song:
    grid: Grid
    seconds: float
    drop: float | None
    rng: np.random.Generator
    kit: Kit
    n: int
    drums: np.ndarray = None
    bass: np.ndarray = None
    keys: np.ndarray = None
    lead: np.ndarray = None
    fx: np.ndarray = None
    kicks: list[float] = field(default_factory=list)

    def __post_init__(self):
        for name in ("drums", "bass", "keys", "lead", "fx"):
            setattr(self, name, np.zeros((self.n, 2)))

    def bars(self) -> list[tuple[int, float, str]]:
        """(bar index, start time, section) for every bar overlapping the score."""
        g = self.grid
        first = math.floor((0 - g.phase) / g.bar)
        out = []
        b = first
        while g.phase + b * g.bar < self.seconds + 0.5:
            t = g.phase + b * g.bar
            if self.drop is None:
                sec = "main"
            elif t >= self.drop - 1e-6:
                sec = "main"
            elif t + g.bar >= self.drop - 1e-6:
                sec = "build"
            else:
                sec = "pre"
            out.append((b, t, sec))
            b += 1
        return out

    def pad(self, bus: str, t: float, notes: list[int], dur: float, attack: float, release: float, cutoff: float,
            gain: float, voices: int = 3, cents: float = 10.0, width: float = 0.7) -> None:
        """A chord pad spread wide: two differently phased copies, one each side."""
        for side, seed in ((-width, 0), (width, 1)):
            place(getattr(self, bus), t, chord_pad(notes, dur, attack, release, cutoff, voices, cents, seed), gain, side)

    def hit(self, bus: str, t: float, sig: np.ndarray, gain: float, pan: float = 0.0, human: float = 0.004):
        if t < -2 or t > self.seconds + 0.5:
            return
        jitter = float(self.rng.uniform(-human, human)) if human else 0.0
        vel = 1 + float(self.rng.uniform(-0.06, 0.06))
        place(getattr(self, bus), max(0.0, t + jitter) if t >= 0 else t, sig, gain * vel, pan)


def _swing(pos: float, amount: float) -> float:
    """Delay off-beat eighths: amount 0.5 is straight, 0.6 a lazy shuffle."""
    frac = pos % 1
    return pos + (amount - 0.5) * 2 * 0.5 if abs(frac - 0.5) < 1e-6 else pos


Arranger = Callable[[Song], dict]


def _warm(s: Song) -> dict:
    prog = [([62, 66, 69, 74], 38), ([61, 64, 69, 73], 45), ([62, 66, 69, 71], 47), ([62, 67, 71, 74], 43)]
    spb = s.grid.spb
    for b, t, sec in s.bars():
        notes, root = prog[b % 4]
        s.pad("keys", t, notes, s.grid.bar, 0.5, 1.0, 2400 if sec != "pre" else 1100, 0.34)
        for k in range(8):
            m = notes[[0, 1, 2, 3, 2, 1, 2, 3][k]] + 12
            s.hit("lead", t + k * spb / 2, pluck(hz(m), 0.9, 0.72), 0.2, 0.45 * math.sin(k * 1.3))
        if sec == "pre":
            continue
        for at, dur in ((0, 1.6), (2.5, 1.2)):
            for i, m in enumerate(notes[:3]):
                s.hit("keys", t + at * spb + i * 0.012, ep(hz(m - 12), dur * spb, 0.7), 0.10, -0.2 + 0.2 * i)
        for at, m, dur in ((0, root, 1.4), (1.5, root, 0.4), (2, root, 1.2), (3, root + 7, 0.9)):
            s.hit("bass", t + at * spb, bass_note(m, dur * spb), 0.42)
        if sec == "build":
            continue
        for at in (0, 2):
            s.hit("drums", t + at * spb, s.kit.kick(52, 120, 0.4, 0.15), 0.55)
            s.kicks.append(t + at * spb)
        for at in (1, 3):
            s.hit("drums", t + at * spb, s.kit.rim(), 0.22, 0.1)
        for k in range(8):
            s.hit("drums", t + _swing(k / 2, 0.55) * spb, s.kit.shaker(), 0.24 if k % 2 else 0.15, 0.3)
    return {"drums": 0.9, "bass": 0.8, "keys": 1.0, "lead": 0.9, "reverb": (1.8, 0.2), "pump": 0.0}


def _upbeat(s: Song) -> dict:
    prog = [([57, 60, 64, 69], 45), ([57, 60, 65, 69], 41), ([55, 60, 64, 67], 48), ([55, 59, 62, 67], 43)]
    spb = s.grid.spb
    arp = [0, 1, 2, 3, 2, 1, 3, 2]
    for b, t, sec in s.bars():
        notes, root = prog[b % 4]
        cut = 900 if sec != "main" else 2600
        for p16 in (0, 3, 6, 10, 12):
            s.hit("keys", t + p16 * spb / 4, stab(notes, spb * 0.4, cut), 0.62, 0.15 if p16 % 2 else -0.15, human=0.002)
        s.pad("keys", t, [m + 12 for m in notes], s.grid.bar, 0.08, 0.3, 3200, 0.16)
        for at in (1, 3):
            s.hit("drums", t + at * spb, s.kit.clap(), 0.42, 0.05)
        for k in range(8 if sec != "main" else 16):
            step = spb / (2 if sec != "main" else 4)
            s.hit("drums", t + k * step, s.kit.hat(), (0.14 if k % 2 else 0.22) * (0.7 if sec != "main" else 1), 0.25)
        if sec == "build":
            for k in range(16):
                s.hit("drums", t + k * spb / 4, s.kit.snare(200, 0.12), 0.08 + 0.3 * k / 15, -0.1)
            s.hit("fx", t, riser(s.grid.bar, s.rng), 0.5, human=0)
        if sec != "main":
            continue
        for k in range(4):
            s.hit("drums", t + k * spb, s.kit.kick(48, 160, 0.42, 0.45), 0.78)
            s.kicks.append(t + k * spb)
            s.hit("drums", t + (k + 0.5) * spb, s.kit.hat(True), 0.16, -0.25)
            s.hit("bass", t + (k + 0.5) * spb, bass_note(root, spb * 0.42, "saw", 1100), 0.5)
        for k in range(8):
            s.hit("lead", t + k * spb / 2, pluck(hz(notes[arp[k]] + 12), 0.5, 0.75), 0.24 if b % 2 else 0.16,
                  0.4 * math.sin(k))
    return {"drums": 1.0, "bass": 0.9, "keys": 0.75, "lead": 0.8, "reverb": (1.4, 0.16), "pump": 0.55}


def _suspense(s: Song) -> dict:
    prog = [([50, 57, 62, 65], 38), ([50, 58, 62, 65], 34), ([50, 55, 58, 62], 31), ([49, 57, 61, 64], 33)]
    spb = s.grid.spb
    for b, t, sec in s.bars():
        notes, root = prog[b % 4]
        s.pad("keys", t, [38, 45], s.grid.bar, 0.6, 0.8, 380, 0.6, 3, 14, 0.4)
        s.pad("keys", t, [n + 12 for n in notes], s.grid.bar, 1.0, 1.0, 1800, 0.2, 2, 6)
        if sec == "pre" or sec == "build":
            s.hit("lead", t, chord_pad([69, 70], s.grid.bar, 1.5, 0.5, 3000, 2, 4), 0.06, human=0)
        for k in range(8):
            s.hit("drums", t + k * spb / 2, s.kit.tick(k % 2 == 0), 0.22 if sec != "main" else 0.28, 0.5 if k % 2 else -0.5,
                  human=0)
        if sec == "build":
            s.hit("fx", t, riser(s.grid.bar, s.rng), 0.6, human=0)
            for k in range(8):
                s.hit("drums", t + k * spb / 2, s.kit.tom(70 + 4 * k), 0.12 + 0.05 * k)
        if sec != "main":
            continue
        s.hit("drums", t, s.kit.boom(), 0.5 if b % 2 == 0 else 0.0)
        for k in range(4):
            s.hit("drums", t + k * spb, s.kit.kick(44, 90, 0.35, 0.1), 0.6)
            s.hit("drums", t + (k + 0.25) * spb, s.kit.kick(44, 90, 0.3, 0.05), 0.35)
            s.kicks.append(t + k * spb)
        for k in range(8):
            m = root + 12 + (12 if k in (2, 6) else 0)
            s.hit("bass", t + k * spb / 2, bass_note(m, spb * 0.3, "saw", 1100), 0.4)
    return {"drums": 0.9, "bass": 0.8, "keys": 0.9, "lead": 1.0, "reverb": (2.6, 0.26), "pump": 0.0}


def _lofi(s: Song) -> dict:
    prog = [([53, 57, 60, 64], 38), ([53, 59, 64], 43), ([52, 55, 59, 62], 36), ([55, 60, 64, 67], 45)]
    spb = s.grid.spb
    sw = 0.6
    for b, t, sec in s.bars():
        notes, root = prog[b % 4]
        for at, dur in ((0, 1.5), (2.5, 1.3)):
            for i, m in enumerate(notes):
                s.hit("keys", t + at * spb + i * 0.016, ep(hz(m), dur * spb, 0.8), 0.15, -0.3 + 0.2 * i, human=0.006)
        if sec != "main":
            continue
        for at in (0, 2.5) + ((1.75,) if b % 2 else ()):
            s.hit("drums", t + at * spb, s.kit.kick(50, 110, 0.38, 0.1), 0.75)
            s.kicks.append(t + at * spb)
        for at in (1, 3):
            s.hit("drums", t + at * spb, s.kit.snare(190, 0.2, 0.8), 0.42, 0.05)
        for k in range(8):
            s.hit("drums", t + _swing(k / 2, sw) * spb, s.kit.hat(), 0.11 if k % 2 else 0.17, 0.3, human=0.008)
        for at, m, dur in ((0, root, 1.5), (2.5, root, 0.9), (3.5, root + 7, 0.4)):
            s.hit("bass", t + at * spb, bass_note(m, dur * spb), 0.6)
    crackle = np.zeros(s.n)
    idx = s.rng.integers(0, s.n, size=int(s.seconds * 9))
    crackle[idx] = s.rng.uniform(-1, 1, size=len(idx))
    hiss = lowpass(s.rng.standard_normal(s.n), 5000) * 0.004
    place(s.fx, 0, highpass(crackle, 1500) * 0.06 + hiss, 1.0)
    return {"drums": 0.8, "bass": 0.8, "keys": 1.0, "lead": 0.8, "reverb": (1.6, 0.18), "pump": 0.0, "lofi": True}


def _cinematic(s: Song) -> dict:
    prog = [([45, 52, 57, 60, 64], 33), ([41, 48, 53, 57, 60], 29), ([48, 52, 55, 60, 64], 36), ([43, 50, 55, 59, 62], 31)]
    spb = s.grid.spb
    for b, t, sec in s.bars():
        notes, root = prog[b % 4]
        s.pad("keys", t, notes, s.grid.bar, 0.9, 1.4, 2600, 0.55, 3, 12)
        r = notes[2]
        pattern = [r, r + 7, r + 12, r + 7, r, r + 7, r + 12, r + 15 if b % 4 == 0 else r + 7]
        gain = 0.1 if sec == "pre" else 0.16
        for k, m in enumerate(pattern):
            s.hit("lead", t + k * spb / 2, stab([m], spb * 0.3, 3600), gain, 0.4 * math.sin(k))
        if sec == "build":
            s.hit("fx", t, riser(s.grid.bar, s.rng), 0.55, human=0)
            for k in range(8):
                s.hit("drums", t + 2 * spb + k * spb / 4, s.kit.tom(80), 0.25 + 0.06 * k)
        if sec != "main":
            continue
        s.hit("drums", t, s.kit.boom(), 0.7)
        s.kicks.append(t)
        s.hit("bass", t, chord_pad([root + 12, root + 19], s.grid.bar, 0.3, 0.8, 500, 2, 6), 0.7, human=0)
        if b % 2 == 1:
            for at, tone in ((2.5, 95), (3.0, 80), (3.5, 70)):
                s.hit("drums", t + at * spb, s.kit.tom(tone), 0.45)
        s.pad("lead", t, [m + 24 for m in notes[2:]], s.grid.bar, 1.2, 1.0, 6000, 0.05, 2, 5)
    return {"drums": 1.0, "bass": 0.8, "keys": 0.95, "lead": 0.9, "reverb": (3.0, 0.3), "pump": 0.0}


def _playful(s: Song) -> dict:
    prog = [([55, 59, 62, 67], 43), ([52, 55, 59, 64], 40), ([52, 55, 60, 64], 36), ([54, 57, 62, 66], 38)]
    spb = s.grid.spb
    pattern = [0, 2, 1, 3, 2, 1, 3, 2]
    hook = [79, 76, 74, 76, 79, 81, 79, 76]
    for b, t, sec in s.bars():
        notes, root = prog[b % 4]
        for k in range(8):
            m = notes[pattern[k]] + 12
            s.hit("lead", t + k * spb / 2, marimba(hz(m), 0.5), 0.22 if k % 2 == 0 else 0.15, 0.3 * math.sin(k * 1.7))
        for at, dur in ((0.5, 0.3), (1.5, 0.3), (2.5, 0.3), (3.5, 0.3)):
            for i, m in enumerate(notes[1:]):
                s.hit("keys", t + at * spb, pluck(hz(m), dur, 0.55, 0.4), 0.09, -0.3 + 0.3 * i)
        for at in (1, 3):
            s.hit("drums", t + at * spb, s.kit.clap(0.16), 0.3, 0.1)
        for k in range(8):
            s.hit("drums", t + k * spb / 2, s.kit.shaker(), 0.12 if k % 2 else 0.18, -0.3)
        if sec == "build":
            s.hit("fx", t, riser(s.grid.bar, s.rng), 0.35, human=0)
        if sec != "main":
            continue
        for at, m in ((0, root), (1, root + 7), (2, root), (3, root + 7)):
            s.hit("bass", t + at * spb, bass_note(m, spb * 0.5, "pluck"), 0.8)
        for at in (0, 2):
            s.hit("drums", t + at * spb, s.kit.kick(55, 140, 0.3, 0.2), 0.6)
            s.kicks.append(t + at * spb)
        if b % 2 == 1:
            for k, m in enumerate(hook):
                s.hit("lead", t + k * spb / 2, bell(hz(m), 0.9), 0.07, 0.2)
    return {"drums": 0.9, "bass": 0.85, "keys": 0.9, "lead": 1.0, "reverb": (1.3, 0.14), "pump": 0.0}


def _luxe(s: Song) -> dict:
    prog = [([56, 60, 63, 67], 41), ([53, 56, 60, 63], 37), ([55, 58, 60, 63], 39), ([55, 58, 60, 63], 36)]
    spb = s.grid.spb
    for b, t, sec in s.bars():
        notes, root = prog[b % 4]
        s.pad("keys", t, [m + 12 for m in notes], s.grid.bar, 0.6, 1.2, 3400 if sec == "main" else 1400, 0.3, 3, 14)
        for at in (0.5, 1.75, 3.0):
            s.hit("lead", t + at * spb, sum(ep(hz(m), spb * 0.3, 0.6) for m in notes) / len(notes), 0.42,
                  -0.35 if at < 2 else 0.35)
        if sec == "build":
            s.hit("fx", t, riser(s.grid.bar, s.rng), 0.4, human=0)
        if sec != "main":
            continue
        for k in range(4):
            s.hit("drums", t + k * spb, s.kit.kick(46, 110, 0.5, 0.15), 0.7)
            s.kicks.append(t + k * spb)
            s.hit("drums", t + (k + 0.5) * spb, s.kit.hat(True), 0.07, 0.3)
        for at in (1, 3):
            s.hit("drums", t + at * spb, s.kit.rim(), 0.2, -0.15)
            s.hit("drums", t + at * spb, s.kit.clap(0.3), 0.14, 0.15)
        for k in range(16):
            s.hit("drums", t + _swing(k / 4 * 2, 0.56) / 2 * spb, s.kit.shaker(), 0.09 if k % 2 else 0.14, 0.35)
        for at, m, dur in ((0, root, 0.5), (0.75, root, 0.25), (1.5, root + 12, 0.25), (2.5, root, 0.5),
                           (3.25, root + 7, 0.25)):
            s.hit("bass", t + at * spb, bass_note(m, dur * spb * 1.6, "saw", 520), 0.55)
    return {"drums": 0.95, "bass": 0.95, "keys": 0.85, "lead": 0.8, "reverb": (2.2, 0.22), "pump": 0.45, "delay": True}


ARRANGERS: dict[str, Arranger] = {"warm": _warm, "upbeat": _upbeat, "suspense": _suspense, "lofi": _lofi,
                                  "cinematic": _cinematic, "playful": _playful, "luxe": _luxe}


# ---- mixing ------------------------------------------------------------------------------------------------

def _reverb(x: np.ndarray, seconds: float, rng: np.random.Generator) -> np.ndarray:
    n = int(seconds * SR)
    t = _t(n)
    pre = int(0.012 * SR)
    out = np.zeros_like(x)
    for c in range(2):
        ir = rng.standard_normal(n) * np.exp(-t * 6.9 / seconds)
        ir = lowpass(ir, 6000)
        ir[:pre] = 0
        ir /= np.sqrt((ir ** 2).sum())
        out[:, c] = fftconvolve(x[:, c], ir)[: len(x)]
    return out


def _pump(n: int, kicks: list[float], depth: float, spb: float) -> np.ndarray:
    """Sidechain gain: dips under every kick and recovers over most of a beat (the house 'pump')."""
    g = np.ones(n)
    if depth <= 0 or not kicks:
        return g
    rec = int(spb * 0.75 * SR)
    shape = 1 - depth * (1 - np.sin(np.linspace(0, math.pi / 2, rec)) ** 2)
    for k in kicks:
        i = int(k * SR)
        if 0 <= i < n:
            m = min(rec, n - i)
            g[i:i + m] = np.minimum(g[i:i + m], shape[:m])
    return g


def _delay(x: np.ndarray, time: float, feedback: float, wet: float) -> np.ndarray:
    d = int(time * SR)
    out = x.copy()
    tap = x
    for k in range(1, 5):
        tap = np.roll(tap, d, axis=0) * feedback
        tap[:d] = 0
        if k % 2:
            out[:, 0] += tap[:, 1] * wet
            out[:, 1] += tap[:, 0] * wet
        else:
            out += tap * wet
    return out


def _wobble(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Tape flutter (lofi keys): a slowly modulated delay, which bends the pitch by a few tenths of a percent."""
    n = len(x)
    t = _t(n)
    d = SR * (0.0016 + 0.0011 * np.sin(2 * math.pi * 0.55 * t) + 0.0004 * np.sin(2 * math.pi * 1.9 * t + rng.uniform(0, 6)))
    pos = np.clip(np.arange(n) - d, 0, n - 1)
    return np.stack([np.interp(pos, np.arange(n), x[:, c]) for c in range(2)], axis=1)


def _level_pre(x: np.ndarray, drop: float, spb: float, under: float = 4.0) -> np.ndarray:
    """The thinner pre-drop arrangement would sit 10+ dB under the drop, and the hook plays over it: lift it to
    `under` dB below the drop (never more than +9 dB), ramping back to unity across the build."""
    i_drop = int(drop * SR)
    pre, main = x[: max(1, i_drop - int(spb * SR))], x[i_drop: i_drop + int(8 * SR)]
    if len(pre) < SR // 4 or len(main) < SR // 4:
        return x
    rms = lambda a: math.sqrt(float((a ** 2).mean()) + 1e-12)  # noqa: E731
    boost_db = min(9.0, max(0.0, 20 * math.log10(rms(main) / rms(pre)) - under))
    if boost_db < 0.5:
        return x
    g = np.ones(len(x))
    ramp0 = max(0, i_drop - int(2 * spb * SR))
    g[:ramp0] = 10 ** (boost_db / 20)
    g[ramp0:i_drop] = np.linspace(10 ** (boost_db / 20), 1.0, i_drop - ramp0)
    return x * g[:, None]


def score(mood: str, bpm: float, seconds: float, drop: float | None = None, phase: float = 0.0,
          seed: int = 7, target_lufs: float = -16.0) -> np.ndarray:
    """A stereo score (n, 2) float64 of exactly `seconds`, mood and tempo as asked, bar line on `drop`."""
    mood = mood if mood in ARRANGERS else "warm"
    rng = np.random.default_rng((seed * 1_000_003 + int(bpm) * 97 + MOODS.index(mood)) % (2 ** 32))
    n = int(round(seconds * SR))
    s = Song(Grid(bpm, phase), seconds, drop, rng, Kit(rng), n + int(3 * SR))
    mix_opts = ARRANGERS[mood](s)
    pump = _pump(s.n, s.kicks, mix_opts.get("pump", 0.0), s.grid.spb)[:, None]
    keys = s.keys * pump
    lead = s.lead * (pump if mix_opts.get("pump") else 1.0)
    if mix_opts.get("delay"):
        lead = _delay(lead, s.grid.spb * 0.75, 0.42, 0.45)
    if mix_opts.get("lofi"):
        keys = _wobble(lowpass(keys, 5200), rng)
    dry = (s.drums * mix_opts["drums"] + highpass(s.bass, 30) * mix_opts["bass"] + keys * mix_opts["keys"]
           + lead * mix_opts["lead"] + s.fx)
    send = keys * mix_opts["keys"] + lead * mix_opts["lead"] + s.drums * 0.35 * mix_opts["drums"]
    rv_seconds, rv_wet = mix_opts["reverb"]
    wet = _reverb(highpass(send, 200), rv_seconds, rng) * rv_wet * 3.0
    out = highpass(dry + wet, 28)
    if mix_opts.get("lofi"):
        out = lowpass(out, 7000)
    out = np.tanh(out * 1.1) / 1.1
    if drop is not None and drop > 1.0:
        out = _level_pre(out, drop, s.grid.spb)
    if drop is not None and drop > 0.3:  # an eighth of silence right before the drop lands
        gap0, gap1 = int((drop - s.grid.spb / 2) * SR), int(drop * SR)
        if gap0 > 0:
            ramp = min(400, (gap1 - gap0) // 3)
            env = np.ones(len(out))
            env[gap0:gap1] = 0.0
            env[gap0:gap0 + ramp] = np.linspace(1, 0, ramp)
            env[gap1 - ramp // 2:gap1] = 0.0
            out *= env[:, None]
    out = out[:n]
    return master(fade(out, 0.01, 0.0), target_lufs, ceiling=0.8)
