"""The AI-motion disclosure in the rendered picture track (California AB 723)."""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import cv2
import pytest

from shootagent.film.assemble import build_picture
from shootagent.film.spec import FilmSpec, Narration, Shot, ShotResult

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def test_only_generated_shots_carry_the_ai_motion_label(tmp_path):
    """Fails if a generated clip plays without its on-screen label, or a depth move (which invents nothing)
    is labelled as AI-generated."""
    clip = tmp_path / "flat.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x808080:s=1080x1920:r=30:d=2", "-pix_fmt", "yuv420p", str(clip))
    audio = tmp_path / "voice.m4a"
    _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2", "-c:a", "aac", str(audio))
    spec = FilmSpec(languages=["en"], shots=[
        Shot(view="v01", motion="generated", camera="dolly", label="", result=ShotResult(source="generated", clips=["g"])),
        Shot(view="v02", motion="generated", camera="dolly", label="",
             result=ShotResult(source="depth", clips=["d"], fell_back=True)),
    ])
    spec.narrations["en"] = Narration(language="en", segments=["a", "b"], labels=["", ""])
    fctx = SimpleNamespace(media=SimpleNamespace(path=lambda fp: clip))

    source = build_picture(fctx, spec, "en", [0.0, 1.0], 2.0, audio, tmp_path)

    cap = cv2.VideoCapture(str(source))
    frames = []
    for t in (0.8, 1.8):  # late in each shot, after the overlay has faded in
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        assert ok
        frames.append(frame)
    cap.release()
    # The pill sits top-right, beside the room label row (assemble.overlay_image).
    region = (slice(int(1920 * 0.09), int(1920 * 0.14)), slice(int(1080 * 0.55), int(1080 * 0.92)))
    generated, depth = (float(f[region].std()) for f in frames)
    assert generated > 20, "the generated shot shows no label"
    assert depth < 3, "the depth fallback is labelled as AI-generated"
