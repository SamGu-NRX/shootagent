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


def test_a_fallen_back_two_photo_shot_labels_each_photo_with_its_own_room(tmp_path):
    """Fails if the end photo of a two-photo shot that fell back plays under the start photo's room label."""
    import numpy as np
    from PIL import Image

    from shootagent.film.assemble import overlay_image

    clip = tmp_path / "flat.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x606060:s=1080x1920:r=30:d=2", "-pix_fmt", "yuv420p", str(clip))
    audio = tmp_path / "voice.m4a"
    _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2", "-c:a", "aac", str(audio))
    spec = FilmSpec(languages=["en"], shots=[Shot(
        view="v01", end_view="v02", motion="generated", camera="glide", label="Primary suite", end_label="Primary bath",
        result=ShotResult(source="depth", clips=["a", "b"], fell_back=True))])
    spec.narrations["en"] = Narration(language="en", segments=["a"], labels=["Primary suite"], end_labels=["Primary bath"])
    fctx = SimpleNamespace(media=SimpleNamespace(path=lambda fp: clip))
    source = build_picture(fctx, spec, "en", [0.0], 2.0, audio, tmp_path)

    cap = cv2.VideoCapture(str(source))
    cap.set(cv2.CAP_PROP_POS_MSEC, 1800)
    ok, frame = cap.read()
    cap.release()
    assert ok
    label_box = (slice(int(1920 * 0.07), int(1920 * 0.13)), slice(0, 540))
    seen = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)[label_box].astype(float)
    base = Image.new("RGBA", (1080, 1920), (0x60, 0x60, 0x60, 255))

    def drawn(label):
        return np.asarray(Image.alpha_composite(base, overlay_image(1080, 1920, label, None, None)).convert("RGB"))[label_box]

    assert np.abs(seen - drawn("Primary bath")).mean() < np.abs(seen - drawn("Primary suite")).mean()


def test_a_long_last_line_ends_on_a_closing_card_instead_of_holding_the_room(tmp_path):
    """Fails if the last shot holds its photo for the whole of a long closing line (the demo's game room held
    about 8 s): after 4.5 s the picture must change to the blurred closing card."""
    import numpy as np

    clip = tmp_path / "pattern.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=6", "-pix_fmt", "yuv420p", str(clip))
    audio = tmp_path / "voice.m4a"
    _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "8", "-c:a", "aac", str(audio))
    spec = FilmSpec(languages=["en"], shots=[Shot(view="v01", motion="depth", label="", result=ShotResult(source="depth", clips=["d"]))])
    spec.narrations["en"] = Narration(language="en", segments=["a"], labels=[""])
    fctx = SimpleNamespace(media=SimpleNamespace(path=lambda fp: clip))
    source = build_picture(fctx, spec, "en", [0.0], 8.0, audio, tmp_path)

    cap = cv2.VideoCapture(str(source))
    sharp = []
    for t in (2.0, 7.0):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        assert ok
        sharp.append(float(cv2.Laplacian(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)[1200:1800], cv2.CV_64F).var()))
    cap.release()
    assert sharp[1] < 0.2 * sharp[0], sharp
    assert np.isfinite(sharp).all()
