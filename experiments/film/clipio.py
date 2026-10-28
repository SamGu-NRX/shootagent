"""Video and image I/O shared by the film experiments: read sampled frames, write mp4 through ffmpeg, and
draw the contact sheet (photo plus frames) that both the vision judge and a person look at."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


def load_rgb(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"cannot read image {path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def save_jpg(path: str | Path, rgb: np.ndarray, quality: int = 90) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality]):
        raise OSError(f"could not write {path}")


@dataclass
class Samples:
    times: list[float]  # seconds
    frames: list[np.ndarray]  # RGB uint8 at native resolution
    fps: float
    n_frames: int  # frames decoded


def read_samples(path: str | Path, step_s: float) -> Samples:
    """Frames at t = 0, step, 2*step, ... plus the last frame. Decodes sequentially and keeps only those,
    so a long clip never sits in memory."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video {path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        raise ValueError(f"{path} reports no frame rate")
    every = max(1, round(step_s * fps))
    times, frames, last, i = [], [], None, 0
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        if i % every == 0:
            times.append(i / fps)
            frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        last = bgr
        i += 1
    cap.release()
    if i == 0:
        raise ValueError(f"{path} decoded no frames")
    if (i - 1) % every != 0:  # always end on the true last frame
        times.append((i - 1) / fps)
        frames.append(cv2.cvtColor(last, cv2.COLOR_BGR2RGB))
    return Samples(times, frames, fps, i)


class VideoWriter:
    """Raw RGB frames piped into ffmpeg (H.264, yuv420p, CRF 18)."""

    def __init__(self, path: str | Path, width: int, height: int, fps: float, crf: int = 18):
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg is not on PATH")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.size = (width, height)
        self.proc = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
             "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
            stdin=subprocess.PIPE,
        )

    def write(self, rgb: np.ndarray) -> None:
        if rgb.shape != (self.size[1], self.size[0], 3) or rgb.dtype != np.uint8:
            raise ValueError(f"frame {rgb.shape} {rgb.dtype} does not match writer size {self.size}")
        self.proc.stdin.write(np.ascontiguousarray(rgb).tobytes())

    def close(self) -> None:
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise RuntimeError(f"ffmpeg exited with {self.proc.returncode}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """img scaled to fit (w, h), centred on a dark cell."""
    s = min(w / img.shape[1], h / img.shape[0])
    nw, nh = max(1, round(img.shape[1] * s)), max(1, round(img.shape[0] * s))
    small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    cell = np.full((h, w, 3), 24, np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    cell[y0:y0 + nh, x0:x0 + nw] = small
    return cell


def contact_sheet(tiles: list[tuple[str, np.ndarray]], long_edge: int = 1568) -> np.ndarray:
    """Labelled tiles in a grid no larger than `long_edge` (Claude's vision input size). Cells take the
    aspect of the last tile (a video frame); 3 columns for landscape frames, 4 for portrait."""
    fh, fw = tiles[-1][1].shape[:2]
    cols = 3 if fw >= fh else 4
    rows = -(-len(tiles) // cols)
    gap = 6
    cell_w = (long_edge - gap * (cols - 1)) // cols
    cell_h = round(cell_w * fh / fw)
    if rows * cell_h + gap * (rows - 1) > long_edge:
        cell_h = (long_edge - gap * (rows - 1)) // rows
        cell_w = round(cell_h * fw / fh)
    sheet = np.full((rows * cell_h + gap * (rows - 1), cols * cell_w + gap * (cols - 1), 3), 255, np.uint8)
    for i, (label, img) in enumerate(tiles):
        r, c = divmod(i, cols)
        y, x = r * (cell_h + gap), c * (cell_w + gap)
        cell = _fit(img, cell_w, cell_h)
        scale = max(0.5, cell_w / 700)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        cv2.rectangle(cell, (0, 0), (tw + 12, th + 14), (0, 0, 0), -1)
        cv2.putText(cell, label, (6, th + 6), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)
        sheet[y:y + cell_h, x:x + cell_w] = cell
    return sheet


def pick_evenly(n_available: int, n: int) -> list[int]:
    """n indices spread from the first to the last item inclusive."""
    if n_available <= n:
        return list(range(n_available))
    return [round(i * (n_available - 1) / (n - 1)) for i in range(n)]
