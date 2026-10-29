"""ffmpeg helpers shared by the film backends and assembly."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg is not on PATH. Install ffmpeg to render films.")
    return path


def ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise RuntimeError("ffprobe is not on PATH. It ships with ffmpeg; install ffmpeg.")
    return path


def run_ffmpeg(args: list[str], what: str, timeout: float = 600) -> None:
    proc = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", *args], capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed while {what}: {proc.stderr.strip()[-800:]}")


def duration(path: Path) -> float:
    """Container duration in seconds."""
    proc = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {path}: {proc.stderr.strip()[-300:]}")
    return float(json.loads(proc.stdout)["format"]["duration"])


def size(path: Path) -> tuple[int, int]:
    """(width, height) of the first video stream."""
    proc = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {path}: {proc.stderr.strip()[-300:]}")
    s = json.loads(proc.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


class VideoWriter:
    """Raw RGB frames piped into ffmpeg (H.264, yuv420p, CRF 18). stderr goes to a file: an unread pipe that
    fills up blocks ffmpeg, which then stops reading frames and blocks the writer too."""

    def __init__(self, path: Path, width: int, height: int, fps: float, crf: int = 18):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.size = (width, height)
        self.err_path = path.with_suffix(".ffmpeg.log")
        self._err = self.err_path.open("wb")
        self.proc = subprocess.Popen(
            [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
             "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
            stdin=subprocess.PIPE, stderr=self._err,
        )

    def write(self, rgb: np.ndarray) -> None:
        if rgb.shape != (self.size[1], self.size[0], 3) or rgb.dtype != np.uint8:
            raise ValueError(f"frame {rgb.shape} {rgb.dtype} does not match writer size {self.size}")
        self.proc.stdin.write(np.ascontiguousarray(rgb).tobytes())

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            code = self.proc.wait(timeout=120)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()
            self._err.close()
        if code != 0:
            raise RuntimeError(f"ffmpeg exited with {code}: {self.err_path.read_text(errors='replace')[-500:]}")
        self.err_path.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
