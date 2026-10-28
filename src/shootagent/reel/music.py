"""A generated background track, so the reel ships with music that carries no licence.

Warm pad chords (I-V-vi-IV in D major) under a soft plucked arpeggio, through a convolution reverb.
Deterministic: the same length gives the same samples.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

SR = 44100
BPM = 84
# MIDI notes per chord, voiced close around middle C for the pad; bass an octave or two below.
CHORDS = [
    ([62, 66, 69, 74], 38),  # D
    ([61, 64, 69, 73], 45),  # A
    ([62, 66, 69, 71], 47),  # Bm (add D on top)
    ([62, 67, 71, 74], 43),  # G
]


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _tone(freq: float, n: int, harmonics=(1.0, 0.35, 0.12, 0.05), detune_cents=0.0) -> np.ndarray:
    t = np.arange(n) / SR
    f = freq * 2 ** (detune_cents / 1200)
    return sum(a * np.sin(2 * np.pi * f * (k + 1) * t) for k, a in enumerate(harmonics))


def _envelope(n: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n)
    a = min(n, int(attack * SR))
    r = min(n - a, int(release * SR))
    env[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    if r > 0:
        env[n - r:] *= np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return env


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    alpha = 1 - np.exp(-2 * np.pi * cutoff / SR)
    # One-pole filter applied twice; vectorised via lfilter-equivalent recursion in scipy.
    from scipy.signal import lfilter

    b, a = [alpha], [1, alpha - 1]
    return lfilter(b, a, lfilter(b, a, x))


def synth_track(seconds: float) -> np.ndarray:
    """Stereo float32 in [-1, 1], shape (n, 2)."""
    beat = 60 / BPM
    bar = 4 * beat
    total = int((seconds + 2.0) * SR)
    left = np.zeros(total)
    right = np.zeros(total)
    rng = np.random.default_rng(7)

    t0 = 0.0
    i = 0
    while t0 < seconds + 1.0:
        notes, bass = CHORDS[i % len(CHORDS)]
        start = int(t0 * SR)
        n = min(int((bar + 1.2) * SR), total - start)
        if n <= 0:
            break
        env = _envelope(n, attack=0.7, release=1.4)
        for j, m in enumerate(notes):
            pan = 0.35 + 0.3 * (j / (len(notes) - 1))
            voice = (_tone(_hz(m), n, detune_cents=-4) + _tone(_hz(m), n, detune_cents=4)) * 0.5
            left[start:start + n] += voice * env * 0.055 * (1 - pan)
            right[start:start + n] += voice * env * 0.055 * pan
        b = _tone(_hz(bass), n, harmonics=(1.0, 0.2)) * env * 0.09
        left[start:start + n] += b
        right[start:start + n] += b
        # Plucked arpeggio: eighth notes walking the chord upward, an octave up.
        for k in range(8):
            ps = start + int(k * beat / 2 * SR)
            pn = min(int(1.6 * SR), total - ps)
            if pn <= 0:
                continue
            m = notes[k % len(notes)] + 12
            pt = np.arange(pn) / SR
            pluck = _tone(_hz(m), pn, harmonics=(1.0, 0.4, 0.15, 0.08)) * np.exp(-pt * 3.2)
            pluck *= _envelope(pn, attack=0.004, release=0.3) * (0.05 + 0.01 * rng.random())
            pan = 0.5 + 0.25 * np.sin(k)
            left[ps:ps + pn] += pluck * (1 - pan)
            right[ps:ps + pn] += pluck * pan
        t0 += bar
        i += 1

    left, right = _lowpass(left, 5200), _lowpass(right, 5200)
    ir_n = int(2.2 * SR)
    ir_t = np.arange(ir_n) / SR
    ir_l = rng.standard_normal(ir_n) * np.exp(-ir_t * 2.6)
    ir_r = rng.standard_normal(ir_n) * np.exp(-ir_t * 2.6)
    ir_l[0] = ir_r[0] = 0
    wet_l = fftconvolve(left, ir_l)[:total] * 0.018
    wet_r = fftconvolve(right, ir_r)[:total] * 0.018
    out = np.stack([left * 0.8 + wet_l, right * 0.8 + wet_r], axis=1)[: int(seconds * SR)]
    fade_in, fade_out = int(0.4 * SR), int(min(2.0, seconds / 4) * SR)
    out[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    out[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None] ** 1.5
    peak = np.abs(out).max() or 1.0
    return (out / peak * 0.7).astype(np.float32)


def write_wav(path: Path, samples: np.ndarray) -> Path:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path
