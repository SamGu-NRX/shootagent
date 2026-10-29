"""Audio plumbing for the studio: filters, loudness, a limiter and WAV files. numpy and scipy only, deterministic.

Loudness is ITU-R BS.1770 (K-weighted, gated), the measure the platforms normalise to, so a reel can be mastered to
a target instead of to its peak: two reels with the same peak can differ by 6 dB in how loud they sound.
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import lfilter

SR = 44100


def biquad(kind: str, fc: float, q: float = 0.7071, gain_db: float = 0.0, sr: int = SR):
    """RBJ cookbook coefficients (b, a) for lowpass, highpass, bandpass, peak and highshelf."""
    fc = min(fc, sr * 0.45)
    w = 2 * math.pi * fc / sr
    cw, sw = math.cos(w), math.sin(w)
    alpha = sw / (2 * q)
    A = 10 ** (gain_db / 40)
    if kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "bandpass":
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "peak":
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    elif kind == "highshelf":
        sa = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * cw + sa), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sa)]
        a = [(A + 1) - (A - 1) * cw + sa, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sa]
    else:
        raise ValueError(kind)
    return np.array(b) / a[0], np.array(a) / a[0]


def filt(x: np.ndarray, kind: str, fc: float, q: float = 0.7071, gain_db: float = 0.0) -> np.ndarray:
    b, a = biquad(kind, fc, q, gain_db)
    return lfilter(b, a, x, axis=0)


def lowpass(x: np.ndarray, fc: float, q: float = 0.7071) -> np.ndarray:
    return filt(x, "lowpass", fc, q)


def highpass(x: np.ndarray, fc: float, q: float = 0.7071) -> np.ndarray:
    return filt(x, "highpass", fc, q)


def lufs(x: np.ndarray, sr: int = SR) -> float:
    """Integrated loudness (LUFS) of mono (n,) or stereo (n, 2) audio, BS.1770-4 gating."""
    x = x if x.ndim == 2 else x[:, None]
    if len(x) < sr * 0.4:
        x = np.concatenate([x, np.zeros((int(sr * 0.4) - len(x) + 1, x.shape[1]))])
    # K-weighting: a +4 dB shelf above ~1.7 kHz (the head), then a ~38 Hz high-pass (the RLB curve).
    y = filt(filt(x, "highshelf", 1681.974, 0.7071752, 3.99984), "highpass", 38.13547, 0.5003270)
    block, hop = int(0.4 * sr), int(0.1 * sr)
    p = np.cumsum(np.concatenate([np.zeros((1, y.shape[1])), y ** 2]), axis=0)
    starts = np.arange(0, len(y) - block + 1, hop)
    ms = (p[starts + block] - p[starts]) / block  # mean square per channel per block
    z = ms.sum(axis=1)
    loud = -0.691 + 10 * np.log10(np.maximum(z, 1e-12))
    gated = z[loud > -70]
    if not len(gated):
        return -70.0
    rel = -0.691 + 10 * np.log10(gated.mean()) - 10
    final = z[(loud > -70) & (loud > rel)]
    return float(-0.691 + 10 * np.log10(max(final.mean() if len(final) else gated.mean(), 1e-12)))


def limit(x: np.ndarray, ceiling: float = 0.89, window: float = 0.025) -> np.ndarray:
    """Look-ahead peak limiter. The gain is the minimum of the needed gain over a window, then averaged over the
    same window: the average of minima that each cover a sample can never exceed that sample's own need, so no
    peak passes the ceiling, and the gain glides in and out over ~25 ms instead of clicking."""
    mono = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    g = np.minimum(1.0, ceiling / np.maximum(mono, 1e-9))
    n = max(3, int(window * SR) | 1)
    g = uniform_filter1d(minimum_filter1d(g, size=n, mode="nearest"), size=n, mode="nearest")
    out = x * (g[:, None] if x.ndim == 2 else g)
    np.clip(out, -ceiling, ceiling, out=out)
    return out


def master(x: np.ndarray, target: float, ceiling: float = 0.89) -> np.ndarray:
    """Gain to `target` LUFS, then limit peaks; a second pass makes up what the limiter took off."""
    for _ in range(2):
        now = lufs(x)
        if now <= -69:
            return x
        x = limit(x * 10 ** ((target - now) / 20), ceiling)
    return x


def fade(x: np.ndarray, fade_in: float, fade_out: float) -> np.ndarray:
    n = len(x)
    a, b = min(n, int(fade_in * SR)), min(n, int(fade_out * SR))
    env = np.ones(n)
    if a > 1:
        env[:a] = np.sin(np.linspace(0, math.pi / 2, a)) ** 2
    if b > 1:
        env[n - b:] *= np.cos(np.linspace(0, math.pi / 2, b)) ** 2
    return x * (env[:, None] if x.ndim == 2 else env)


def place(buf: np.ndarray, t: float, sig: np.ndarray, gain: float = 1.0, pan: float = 0.0) -> None:
    """Add a mono signal into a stereo buffer at time t (s), constant-power panned (-1 left .. 1 right)."""
    i = int(round(t * SR))
    if i >= len(buf) or len(sig) == 0:
        return
    if i < 0:
        sig, i = sig[-i:], 0
    n = min(len(sig), len(buf) - i)
    th = (pan + 1) * math.pi / 4
    buf[i:i + n, 0] += sig[:n] * gain * math.cos(th)
    buf[i:i + n, 1] += sig[:n] * gain * math.sin(th)


def write_wav(path: Path, x: np.ndarray) -> Path:
    x = x if x.ndim == 2 else np.stack([x, x], axis=1)
    pcm = (np.clip(x, -1, 1) * 32767).round().astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def read_wav(path: Path) -> np.ndarray:
    """Mono float64 at SR (resampled when the file is at another rate)."""
    with wave.open(str(path), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dtype = {1: np.uint8, 2: "<i2", 4: "<i4"}[width]
    x = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    x = (x - 128) / 128 if width == 1 else x / float(2 ** (8 * width - 1))
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        from scipy.signal import resample_poly
        g = math.gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g)
    return x
