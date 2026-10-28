"""Render a ReelSpec to a 9:16 mp4.

Frames are drawn in Python (sub-pixel affine warps, so slow moves stay smooth; ffmpeg's zoompan rounds
the crop to whole pixels and visibly judders) and piped as raw RGB into one ffmpeg process, which also
muxes the music. Text is set with bundled OFL fonts (Fraunces for the address, Inter for everything
else) and kept inside the area social apps leave clear of their own buttons.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..store import Listing, ShootStore
from ..trace import NullTrace, TraceRun
from .music import synth_track, write_wav
from .spec import ReelSpec, Slide

FONTS = Path(__file__).parent / "fonts"


@lru_cache(maxsize=64)
def font(family: str, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / f"{family}.ttf"), size)
    if family == "Inter":
        f.set_variation_by_axes([min(32, max(14, size)), weight])
    else:  # Fraunces: optical size, weight, softness, wonky
        f.set_variation_by_axes([min(144, max(9, size)), weight, 50, 0])
    return f


def ease_in_out(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, t)))


def ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


# ---- text layers ----------------------------------------------------------------------------------------

def _tracked(draw: ImageDraw.ImageDraw, xy, text: str, f, fill, tracking: float, anchor_center=False):
    """Draw text with letter spacing (PIL has none). Returns the drawn width."""
    widths = [f.getlength(ch) for ch in text]
    total = sum(widths) + tracking * max(0, len(text) - 1)
    x, y = xy
    if anchor_center:
        x -= total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=f, fill=fill)
        x += w + tracking
    return total


@dataclass
class Layer:
    """An RGBA overlay with its own fade/rise timing within a slide, cropped to its visible box."""

    rgba: np.ndarray  # (H, W, 4) float32 premultiplied 0..1; cropped to `box` in __post_init__
    t_in: float  # seconds after slide start
    t_out: float | None  # seconds after slide start, or None to stay until the slide ends
    rise: float = 18.0  # px the layer drifts up while fading in
    box: tuple[int, int, int, int] = (0, 0, 0, 0)  # y0, y1, x0, x1 in frame pixels

    def __post_init__(self):
        ys, xs = np.nonzero(self.rgba[..., 3] > 0.002)
        if len(ys) == 0:
            self.box, self.rgba = (0, 0, 0, 0), self.rgba[:0, :0]
            return
        pad = int(self.rise) + 2
        y0, y1 = max(0, ys.min() - 2), min(self.rgba.shape[0], ys.max() + pad)
        x0, x1 = xs.min(), xs.max() + 1
        self.box, self.rgba = (y0, y1, x0, x1), np.ascontiguousarray(self.rgba[y0:y1, x0:x1])


def _canvas(w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _to_layer(img: Image.Image, shadow: bool = True) -> np.ndarray:
    if shadow:
        a = img.split()[3].filter(ImageFilter.GaussianBlur(10))
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sh.putalpha(a.point(lambda v: int(v * 0.45)))
        img = Image.alpha_composite(sh, img)
    arr = np.asarray(img).astype(np.float32) / 255.0
    arr[..., :3] *= arr[..., 3:4]
    return arr


def room_label(w: int, h: int, text: str) -> np.ndarray:
    img, d = _canvas(w, h)
    x, y = int(w * 0.08), int(h * 0.70)
    f = font("Inter", 30, 600)
    d.line([(x, y - 22), (x + 44, y - 22)], fill=(255, 255, 255, 230), width=3)
    _tracked(d, (x, y), text.upper(), f, (255, 255, 255, 255), tracking=5)
    return _to_layer(img)


def title_card(w: int, h: int, listing: Listing) -> np.ndarray:
    img, d = _canvas(w, h)
    cx, y = w // 2, int(h * 0.30)
    _tracked(d, (cx, y), "JUST LISTED", font("Inter", 28, 600), (255, 255, 255, 235), 7, anchor_center=True)
    address = (listing.address or "").strip()
    if address:
        street, _, rest = address.partition(",")
        f = font("Fraunces", 86, 380)
        d.text((cx, y + 64), street, font=f, fill=(255, 255, 255, 255), anchor="ma")
        if rest.strip():
            d.text((cx, y + 172), rest.strip(), font=font("Inter", 34, 450), fill=(255, 255, 255, 225), anchor="ma")
    if listing.price:
        d.text((cx, y + 240), listing.price, font=font("Inter", 58, 650), fill=(255, 255, 255, 255), anchor="ma")
    return _to_layer(img)


def stats_card(w: int, h: int, listing: Listing) -> np.ndarray | None:
    items = []
    if listing.beds:
        items.append((f"{listing.beds:g}", "BEDS"))
    if listing.baths:
        items.append((f"{listing.baths:g}", "BATHS"))
    if listing.sqft:
        items.append((f"{listing.sqft:,}", "SQ FT"))
    if not items:
        return None
    img, d = _canvas(w, h)
    y = int(h * 0.30)
    col = w / (len(items) + 1)
    for i, (value, label) in enumerate(items):
        cx = col * (i + 1)
        d.text((cx, y), value, font=font("Fraunces", 96, 400), fill=(255, 255, 255, 255), anchor="ma")
        _tracked(d, (cx, y + 120), label, font("Inter", 26, 600), (255, 255, 255, 230), 5, anchor_center=True)
    return _to_layer(img)


def end_card(w: int, h: int, listing: Listing, brand: str | None) -> np.ndarray:
    img, d = _canvas(w, h)
    cx, y = w // 2, int(h * 0.36)
    if listing.price:
        d.text((cx, y), listing.price, font=font("Fraunces", 104, 400), fill=(255, 255, 255, 255), anchor="ma")
    if listing.address:
        d.text((cx, y + 140), listing.address, font=font("Inter", 32, 450), fill=(255, 255, 255, 230), anchor="ma")
    _tracked(d, (cx, y + 230), "SCHEDULE A TOUR", font("Inter", 30, 650), (255, 255, 255, 255), 6, anchor_center=True)
    if brand:
        d.text((cx, int(h * 0.80)), brand, font=font("Inter", 26, 500), fill=(255, 255, 255, 190), anchor="ma")
    return _to_layer(img)


def scrim(w: int, h: int) -> np.ndarray:
    """Soft top and bottom darkening so white type reads on bright rooms. Multiplier, (H, 1, 1)."""
    y = np.linspace(0, 1, h)
    top = 1 - 0.30 * np.clip(1 - y / 0.45, 0, 1) ** 2
    bottom = 1 - 0.38 * np.clip((y - 0.55) / 0.45, 0, 1) ** 2
    return (top * bottom).astype(np.float32)[:, None, None]


# ---- camera moves --------------------------------------------------------------------------------------

def prepare_source(img: np.ndarray, out_h: int, headroom: float = 1.18) -> np.ndarray:
    """Resize so the photo's height is out_h * headroom: enough to push in without upscaling."""
    h, w = img.shape[:2]
    scale = out_h * headroom / h
    return cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)


def move(slide: Slide, src_w: int, src_h: int, out_w: int, out_h: int) -> Callable[[float], tuple[float, float, float]]:
    """Return t (0..1) -> (centre_x, centre_y, scale) in source pixels; scale = output px per source px."""
    s_fit = out_h / src_h  # the window spans the full photo height at this scale
    half_w = lambda s: out_w / s / 2
    fx, fy = slide.focus or (0.5, 0.5)
    cy = src_h / 2

    def clamp_x(x: float, s: float) -> float:
        return min(src_w - half_w(s), max(half_w(s), x))

    if slide.motion in ("pan_right", "pan_left"):
        s = s_fit * 1.04
        a, b = clamp_x(src_w * 0.30, s), clamp_x(src_w * 0.70, s)
        if slide.motion == "pan_left":
            a, b = b, a
        return lambda t: (a + (b - a) * ease_in_out(t), cy, s)
    s0, s1 = s_fit * 1.02, s_fit * 1.16
    if slide.motion == "pull_out":
        s0, s1 = s1, s0
    x_focus = src_w * fx

    def f(t: float):
        s = s0 + (s1 - s0) * ease_in_out(t)
        x = clamp_x(src_w / 2 + (x_focus - src_w / 2) * ease_in_out(t), s)
        y = min(src_h - out_h / s / 2, max(out_h / s / 2, cy + (src_h * fy - cy) * ease_in_out(t) * 0.5))
        return x, y, s

    return f


def frame_at(src: np.ndarray, cam: tuple[float, float, float], out_w: int, out_h: int) -> np.ndarray:
    cx, cy, s = cam
    m = np.array([[s, 0, out_w / 2 - s * cx], [0, s, out_h / 2 - s * cy]], dtype=np.float64)
    return cv2.warpAffine(src, m, (out_w, out_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


# ---- assembly ------------------------------------------------------------------------------------------

def _blend_layer(frame: np.ndarray, layer: Layer, t: float, slide_seconds: float) -> np.ndarray:
    """Blend in place inside the layer's box. `frame` is float32 RGB 0..1."""
    if layer.rgba.size == 0:
        return frame
    fade = 0.45
    a_in = ease_out((t - layer.t_in) / fade)
    t_out = layer.t_out if layer.t_out is not None else slide_seconds + 1
    alpha = max(0.0, min(a_in, 1 - ease_out((t - t_out) / fade)))
    if alpha <= 0.001:
        return frame
    dy = round(layer.rise * (1 - a_in))
    y0, y1, x0, x1 = layer.box
    h = y1 - y0
    # Drifting up: draw the layer `dy` px lower, clipped to the frame.
    ty0, ty1 = y0 + dy, min(frame.shape[0], y1 + dy)
    src = layer.rgba[: ty1 - ty0]
    if h <= 0 or src.size == 0:
        return frame
    region = frame[ty0:ty1, x0:x1]
    region *= 1 - src[..., 3:4] * alpha
    region += src[..., :3] * alpha
    return frame


def render_reel(
    store: ShootStore,
    shoot_id: str,
    spec: ReelSpec,
    trace: TraceRun | None = None,
    out_path: Path | None = None,
) -> Path:
    trace = trace or NullTrace()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not on PATH. Install ffmpeg to render reels.")
    shoot = store.load(shoot_id)
    W, H, fps, xf = spec.width, spec.height, spec.fps, spec.crossfade
    reel_dir = store.shoot_dir(shoot_id) / "reel"
    reel_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_path or reel_dir / f"reel-{len(list(reel_dir.glob('reel-*.mp4'))) + 1}.mp4"

    sources, cams, layers = [], [], []
    for i, slide in enumerate(spec.slides):
        view = shoot.view(slide.view)
        version = view.version(slide.version) if slide.version else view.head_version
        src = prepare_source(store.get_image(shoot_id, version.blob), H)
        sources.append(src)
        cams.append(move(slide, src.shape[1], src.shape[0], W, H))
        ls: list[Layer] = []
        if i == 0 and (spec.listing.address or spec.listing.price):
            ls.append(Layer(title_card(W, H, spec.listing), 0.35, slide.seconds - 0.7))
        elif slide.caption:
            ls.append(Layer(room_label(W, H, slide.caption), 0.3, slide.seconds - 0.55, rise=10))
        if spec.stats_after is not None and i == spec.stats_after:
            card = stats_card(W, H, spec.listing)
            if card is not None:
                ls.append(Layer(card, 0.4, slide.seconds - 0.6))
        layers.append(ls)
    end_layer = Layer(end_card(W, H, spec.listing, spec.brand), 0.5, None)
    end_seconds = 2.6

    starts, t = [], 0.0
    for slide in spec.slides:
        starts.append(t)
        t += slide.seconds - xf
    total = t + xf + end_seconds
    n_frames = round(total * fps)
    shade = scrim(W, H) / 255.0  # frames come out of the warp as 0..255

    with tempfile.TemporaryDirectory() as tmp:
        audio_args: list[str] = []
        if spec.music == "synth":
            wav = write_wav(Path(tmp) / "music.wav", synth_track(total))
            audio_args = ["-i", str(wav)]
        elif spec.music:
            audio_args = ["-i", spec.music]
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
            *audio_args,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]
        if audio_args:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
        cmd.append(str(out_path))
        trace.emit("tool_result", tool="render_reel", ok=True, summary=f"Rendering {len(spec.slides)} photos, {total:.1f} s at {fps} fps.")
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        last = len(spec.slides) - 1

        def make_frame(k: int) -> bytes:
            tt = k / fps
            frame = None
            for i, slide in enumerate(spec.slides):
                local = tt - starts[i]
                # The last photo holds under the end card instead of cutting to black.
                span = slide.seconds + (end_seconds + xf if i == last else 0)
                if local < 0 or local > span:
                    continue
                img = frame_at(sources[i], cams[i](min(1.0, local / span)), W, H).astype(np.float32)
                img *= shade
                for layer in layers[i]:
                    _blend_layer(img, layer, local, slide.seconds)
                if i == last:
                    end_t = local - slide.seconds + 0.2
                    img *= 1 - 0.35 * ease_out(end_t / 0.8)
                    _blend_layer(img, end_layer, end_t, end_seconds)
                w_in = ease_in_out(local / xf) if (i > 0 and xf > 0) else 1.0
                frame = img if frame is None else cv2.addWeighted(frame, 1 - w_in, img, w_in, 0)
            return (np.clip(frame, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes()

        # OpenCV and large numpy ops release the GIL, so threads render frames in parallel; map keeps order.
        try:
            with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
                for chunk in pool.map(make_frame, range(n_frames), chunksize=4):
                    proc.stdin.write(chunk)
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="replace")
            if proc.wait() != 0:
                raise RuntimeError(f"ffmpeg failed: {err.strip()[-500:]}")
        except BrokenPipeError as e:
            err = proc.stderr.read().decode(errors="replace")
            raise RuntimeError(f"ffmpeg exited early: {err.strip()[-500:]}") from e
    shutil.copyfile(out_path, reel_dir / "latest.mp4")
    (reel_dir / "spec.json").write_text(spec.model_dump_json(indent=1), encoding="utf-8")
    return out_path
