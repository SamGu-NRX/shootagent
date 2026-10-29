"""Beats of the music bed, and shot cuts snapped to them.

A cut that lands on a beat reads as edited; one that drifts against the music reads as a slideshow. The
narration still decides which shot plays when (each segment's first word), so a cut only moves to the
nearest beat within `window` seconds of where the narration puts it.

Beat tracking: a spectral-flux onset envelope, the tempo with the strongest autocorrelation between 60 and
150 BPM (the generated bed is 84 BPM with eighth-note plucks; without the upper bound the plucks win at
168), and the beat phase that collects the most onset energy on that grid.
"""
from __future__ import annotations

import numpy as np

HOP = 512
N_FFT = 2048


def onset_envelope(samples: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    """(envelope, envelope frames per second). `samples` is mono (n,) or stereo (n, 2) float."""
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    n = 1 + max(0, (len(mono) - N_FFT) // HOP)
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    frames = np.pad(mono, (0, max(0, idx.max() + 1 - len(mono))))[idx] * np.hanning(N_FFT)
    mag = np.log1p(100 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")  # remove the slow loudness trend
    return np.maximum(flux, 0), sr / HOP


def beat_times(samples: np.ndarray, sr: int, bpm_range: tuple[float, float] = (60, 150)) -> list[float]:
    env, fps = onset_envelope(samples, sr)
    if len(env) < 8 or not env.any():
        return []
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lo, hi = int(fps * 60 / bpm_range[1]), int(np.ceil(fps * 60 / bpm_range[0]))
    hi = min(hi, len(ac) - 2)
    if lo >= hi:
        return []
    k = lo + int(np.argmax(ac[lo:hi + 1]))
    # Sub-frame period from a parabola through the peak: one 11.6 ms frame of error per beat adds up to a
    # quarter second over a 30 s film.
    a, b, c = ac[k - 1], ac[k], ac[k + 1]
    lag = k + (0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0)
    grid = np.arange(0, len(env), lag)
    phase = max(np.arange(0, lag, 0.25), key=lambda o: env[np.clip(np.round(grid + o).astype(int), 0, len(env) - 1)].sum())
    beats = []
    for f in grid + phase:
        if f >= len(env):
            break
        # Settle on the strongest onset within a fifth of a beat, so the beat sits on the note, not the grid.
        w = int(max(1, lag / 5))
        i0, i1 = max(0, int(f) - w), min(len(env), int(f) + w + 1)
        peak = i0 + int(np.argmax(env[i0:i1])) if env[i0:i1].max() > 0 else int(round(f))
        beats.append(peak)
    # A frame's flux peaks when the onset reaches the middle of its window.
    return [round((f * HOP + N_FFT / 2) / sr, 4) for f in beats]


def snap_cuts(starts: list[float], beats: list[float], window: float = 0.35, min_len: float = 1.2) -> list[float]:
    """Move each cut (starts[1:]) to the nearest beat within `window` s, keeping every shot at least `min_len`
    long and the order unchanged. starts[0] stays where it is (the film's first frame)."""
    out = [starts[0]]
    b = np.asarray(beats, float)
    for i, s in enumerate(starts[1:], start=1):
        # The next cut can itself move up to `window` later, so leave room for that, not for its current spot.
        nxt = starts[i + 1] + window if i + 1 < len(starts) else np.inf
        cand = b[(np.abs(b - s) <= window) & (b >= out[-1] + min_len) & (b <= nxt - min_len)] if len(b) else b
        out.append(float(cand[np.argmin(np.abs(cand - s))]) if len(cand) else max(s, out[-1] + min_len))
    return out
