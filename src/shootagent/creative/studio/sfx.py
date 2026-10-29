"""Sound effects, synthesised with numpy (seeded, so the same reel gets the same sounds). Each effect returns stereo
samples and its anchor: the offset of the moment that should land on the beat (a whoosh peaks after it starts,
a riser peaks at its end), so the mixer can line the hit up with the cut.
"""
from __future__ import annotations

import math

import numpy as np

from .audio import SR, filt, highpass, lowpass

KINDS = ("whoosh", "pop", "riser", "record_scratch", "bass_drop", "click", "ding")
GAIN = {"whoosh": 0.42, "pop": 0.5, "riser": 0.3, "record_scratch": 0.55, "bass_drop": 0.5, "click": 0.45,
        "ding": 0.36}


def _t(n: int) -> np.ndarray:
    return np.arange(n) / SR


def _norm(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = float(np.abs(x).max()) or 1.0
    return x * (peak / m)


def _stereo(mono: np.ndarray, pan: np.ndarray | float = 0.0) -> np.ndarray:
    th = (np.asarray(pan) + 1) * math.pi / 4
    return np.stack([mono * np.cos(th), mono * np.sin(th)], axis=1)


def whoosh(rng: np.random.Generator, length: float = 0.55) -> tuple[np.ndarray, float]:
    n = int(length * SR)
    p = np.linspace(0, 1, n)
    peak = 0.62
    env = np.where(p < peak, (p / peak) ** 2.2, np.exp(-(p - peak) / 0.12))
    noise = rng.standard_normal(n)
    low, mid, high = lowpass(noise, 700), filt(noise, "bandpass", 1800, 0.8), filt(noise, "bandpass", 4200, 0.9)
    w = np.clip(p / peak, 0, 1)
    body = low * (1 - w) + mid * 1.6 * w * (1 - 0.5 * w) + high * 0.9 * w ** 2
    return _norm(_stereo(body * env, -0.7 + 1.4 * p)), length * peak


def pop(rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = int(0.12 * SR)
    t = _t(n)
    f = 300 + 900 * np.exp(-t / 0.012)
    tone = np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t / 0.035)
    click = highpass(rng.standard_normal(n), 2500) * np.exp(-t / 0.0015) * 0.3
    return _norm(_stereo(tone + click)), 0.0


def riser(rng: np.random.Generator, length: float = 1.6) -> tuple[np.ndarray, float]:
    n = int(max(0.5, length) * SR)
    p = np.linspace(0, 1, n)
    noise = rng.standard_normal(n)
    sweep = lowpass(noise, 800) * (1 - p) + filt(noise, "bandpass", 2600, 0.7) * 1.8 * p * (1 - p) * 2 \
        + highpass(noise, 6000) * 1.3 * p ** 2
    f = 220 * 2 ** (2.6 * p ** 1.6)
    tone = sum(np.sin(2 * math.pi * np.cumsum(f * r) / SR) / r for r in (1.0, 1.5, 2.0)) * 0.35
    body = (sweep * 0.6 + tone) * p ** 2.2
    return _norm(_stereo(body, 0.0)), n / SR


def record_scratch(rng: np.random.Generator) -> tuple[np.ndarray, float]:
    """A needle dragged back and forth: a pitched, noisy grain played at a speed that swings forward and back."""
    n = int(0.42 * SR)
    p = np.linspace(0, 1, n)
    speed = np.sin(2 * math.pi * 2.2 * p) * (1 - p) ** 0.6 * 1.6 + 0.15
    phase = np.cumsum(np.abs(speed)) / SR
    grain = np.sin(2 * math.pi * 420 * phase * 6) * 0.6 + np.sin(2 * math.pi * 640 * phase * 6) * 0.3
    hiss = filt(rng.standard_normal(n), "bandpass", 2200, 0.6) * np.abs(speed) * 0.8
    env = np.minimum(1, p / 0.02) * np.exp(-p * 2.2)
    return _norm(_stereo(np.tanh(2.0 * (grain * np.abs(speed) + hiss)) * env, 0.1)), 0.0


def bass_drop(rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = int(1.6 * SR)
    t = _t(n)
    f = 32 + 78 * np.exp(-t / 0.25)
    sub = np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t / 0.75)
    hit = lowpass(rng.standard_normal(n), 1200) * np.exp(-t / 0.05) * 0.6
    click = highpass(rng.standard_normal(n), 3000) * np.exp(-t / 0.003) * 0.25
    return _norm(_stereo(np.tanh(1.8 * (sub + hit + click)))), 0.0


def click(rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = int(0.05 * SR)
    t = _t(n)
    body = np.sin(2 * math.pi * 3100 * t) * np.exp(-t / 0.004) + highpass(rng.standard_normal(n), 4000) * np.exp(-t / 0.0012)
    second = np.zeros(n)
    k = int(0.018 * SR)  # a shutter-like double click
    second[k:] = body[: n - k] * 0.45
    return _norm(_stereo(body + second, 0.15)), 0.0


def ding(rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = int(1.5 * SR)
    t = _t(n)
    f = 1318.5
    parts = ((1.0, 1.0, 1.2), (2.0, 0.45, 0.5), (3.0, 0.25, 0.3), (4.2, 0.12, 0.18))
    tone = sum(a * np.sin(2 * math.pi * f * r * t) * np.exp(-t / d) for r, a, d in parts)
    return _norm(_stereo(tone * np.minimum(1, t / 0.002))), 0.0


def make(kind: str, seed: int, length: float | None = None) -> tuple[np.ndarray, float]:
    """(stereo samples, anchor seconds) for one effect. `length` stretches the whoosh and the riser."""
    rng = np.random.default_rng(seed)
    if kind == "whoosh":
        return whoosh(rng, length or 0.55)
    if kind == "riser":
        return riser(rng, length or 1.6)
    return {"pop": pop, "record_scratch": record_scratch, "bass_drop": bass_drop, "click": click,
            "ding": ding}[kind](rng)
