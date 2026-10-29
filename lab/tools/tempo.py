"""Rough tempo and level profile for a track: onset-strength autocorrelation, plus RMS per second.

usage: uv run --no-project --with numpy python lab/tools/tempo.py track.mp3
"""
import subprocess, sys

import numpy as np

SR, HOP = 11025, 256
raw = subprocess.run(["ffmpeg", "-v", "error", "-i", sys.argv[1], "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                     capture_output=True, check=True).stdout
x = np.frombuffer(raw, np.float32)
frames = np.lib.stride_tricks.sliding_window_view(x, 1024)[::HOP] * np.hanning(1024)
mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)))
onset = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)  # spectral flux
onset -= onset.mean()
ac = np.correlate(onset, onset, "full")[len(onset) - 1:]
fps = SR / HOP
lags = np.arange(len(ac)) / fps
bpm = 60 / lags[1:]
ok = (bpm >= 60) & (bpm <= 180)
cand = np.argsort(ac[1:][ok])[::-1]
picks = []
for i in cand:
    b = bpm[ok][i]
    if all(abs(b - p) > 3 for p in picks):
        picks.append(b)
    if len(picks) == 3:
        break
strength = ac[1:][ok].max() / ac[0]
print(f"tempo candidates (BPM): {', '.join(f'{p:.1f}' for p in picks)}  (peak autocorrelation {strength:.2f} of lag 0)")
sec = [float(np.sqrt(np.mean(x[i * SR:(i + 1) * SR] ** 2)) + 1e-9) for i in range(int(len(x) / SR) + 1)]
db = [20 * np.log10(s) for s in sec]
print("RMS dBFS per second:", " ".join(f"{d:.0f}" for d in db))
loud = int(np.argmax(db))
print(f"loudest second: {loud}-{loud + 1} s ({db[loud]:.1f} dBFS)")
