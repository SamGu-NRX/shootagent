"""Listing overlays: a watermark and lot lines that look like a designer placed them.

Text and lines are drawn anti-aliased on an RGBA layer (lines supersampled 3x inside their bounding
box) with a soft drop shadow, then alpha-composited. Overlays deliberately change pixels outside any
mask; they are the one edit family without an outside-mask guarantee.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .color import hex_to_rgb

_FONT_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "reel" / "fonts" / "Inter.ttf",
    Path("C:/Windows/Fonts/segoeuisb.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/System/Library/Fonts/SFNS.ttf"),
    Path("/System/Library/Fonts/Helvetica.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]

POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left", "bottom-center", "top-center", "center")


@lru_cache(maxsize=32)
def _font(size: int, weight: str = "SemiBold") -> ImageFont.FreeTypeFont:
    for p in _FONT_CANDIDATES:
        if p.exists():
            f = ImageFont.truetype(str(p), size)
            try:  # variable fonts (Inter) expose named instances
                f.set_variation_by_name(weight)
            except (OSError, ValueError, AttributeError):
                pass
            return f
    return ImageFont.load_default(size=size)


def _alpha_over(base: np.ndarray, layer: Image.Image) -> np.ndarray:
    """Composite an RGBA PIL layer (same size as base) over an RGB uint8 array."""
    la = np.asarray(layer, dtype=np.float32)
    a = la[..., 3:4] / 255.0
    out = base.astype(np.float32) * (1 - a) + la[..., :3] * a
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def _shadow(alpha: Image.Image, radius: float, offset: tuple[int, int], opacity: float, color=(0, 0, 0)) -> Image.Image:
    a = alpha.filter(ImageFilter.GaussianBlur(radius))
    a = a.point(lambda v: int(v * opacity))
    sh = Image.new("RGBA", alpha.size, color + (0,))
    shifted = Image.new("L", alpha.size, 0)
    shifted.paste(a, offset)
    sh.putalpha(shifted)
    return sh


def _mean_luma(img: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = box
    h, w = img.shape[:2]
    crop = img[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
    if crop.size == 0:
        return 128.0
    return float(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).mean())


def watermark(img: np.ndarray, text: str | None = None, logo: np.ndarray | None = None,
              position: str = "bottom-right", opacity: float = 0.85, scale: float = 0.032,
              margin: float = 0.03, color: str = "auto") -> np.ndarray:
    """Brand mark in a corner. `scale` is the text cap height as a fraction of the image's short edge;
    `logo` is RGB or RGBA (H, W, 3|4) and is drawn left of the text at 1.6x the text height.
    `color="auto"` picks white or near-black from the brightness behind the mark."""
    if not text and logo is None:
        raise ValueError("watermark needs text, a logo, or both")
    if position not in POSITIONS:
        raise ValueError(f"position {position!r} must be one of {POSITIONS}")
    if not 0 < opacity <= 1:
        raise ValueError(f"opacity {opacity} must be in (0, 1]")
    h, w = img.shape[:2]
    short = min(h, w)
    size = max(10, round(short * scale * 1.35))  # font px for that cap height
    font = _font(size)
    pad = round(short * margin)
    gap = round(size * 0.45)

    text_w = text_h = 0
    if text:
        l, t, r, b = font.getbbox(text)
        text_w, text_h = r - l, b - t
    logo_img = None
    if logo is not None:
        lh = round(size * 1.6) if text else round(short * scale * 3)
        arr = logo if logo.shape[2] == 4 else np.dstack([logo, np.full(logo.shape[:2], 255, np.uint8)])
        lw = max(1, round(arr.shape[1] * lh / arr.shape[0]))
        logo_img = Image.fromarray(arr, "RGBA").resize((lw, lh), Image.LANCZOS)
    block_w = text_w + (logo_img.width + (gap if text else 0) if logo_img else 0)
    block_h = max(text_h, logo_img.height if logo_img else 0)

    vert, _, horiz = position.partition("-") if "-" in position else ("center", "", "center")
    x = {"left": pad, "right": w - pad - block_w, "center": (w - block_w) // 2}[horiz or "center"]
    y = {"top": pad, "bottom": h - pad - block_h, "center": (h - block_h) // 2}[vert]

    if color == "auto":
        lum = _mean_luma(img, (x - gap, y - gap, x + block_w + gap, y + block_h + gap))
        fg, shadow_col, shadow_op = ((255, 255, 255), (0, 0, 0), 0.55) if lum < 165 else ((24, 24, 24), (255, 255, 255), 0.35)
    else:
        fg = hex_to_rgb(color)
        shadow_col, shadow_op = ((0, 0, 0), 0.5) if sum(fg) > 380 else ((255, 255, 255), 0.35)

    mark_alpha = Image.new("L", (w, h), 0)
    mark_rgb = Image.new("RGBA", (w, h), fg + (0,))
    cx = x
    if logo_img is not None:
        ly = y + (block_h - logo_img.height) // 2
        mark_rgb.paste(logo_img, (cx, ly), logo_img)
        mark_alpha.paste(logo_img.getchannel("A"), (cx, ly))
        cx += logo_img.width + gap
    if text:
        l, t, _, _ = font.getbbox(text)
        ty = y + (block_h - text_h) // 2 - t
        d = ImageDraw.Draw(mark_alpha)
        d.text((cx - l, ty), text, font=font, fill=255)
        txt_layer = Image.new("RGBA", (w, h), fg + (0,))
        txt_alpha = Image.new("L", (w, h), 0)
        ImageDraw.Draw(txt_alpha).text((cx - l, ty), text, font=font, fill=255)
        txt_layer.putalpha(txt_alpha)
        mark_rgb = Image.alpha_composite(mark_rgb, txt_layer)
    shadow = _shadow(mark_alpha, radius=max(1.0, size * 0.12), offset=(0, max(1, round(size * 0.05))),
                     opacity=shadow_op, color=shadow_col)
    layer = Image.alpha_composite(shadow, mark_rgb)
    if opacity < 1:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * opacity)))
    return _alpha_over(img, layer)


def lot_lines(img: np.ndarray, points: list[list[float]], color: str = "#FFD23F", width_px: int | None = None,
              closed: bool = True, label: str | None = None, label_at: list[float] | None = None,
              fill_opacity: float = 0.12, shadow: bool = True) -> np.ndarray:
    """Property boundary from normalised [x, y] points: a crisp line with rounded joins, a soft shadow,
    an optional translucent fill (closed shapes) and an optional label pill at `label_at` (normalised;
    default: the polygon's centroid)."""
    if len(points) < 2:
        raise ValueError("lot lines need at least two points")
    if closed and len(points) < 3:
        raise ValueError("a closed lot outline needs at least three points")
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2 or (pts < -0.05).any() or (pts > 1.05).any():
        raise ValueError("points must be [[x, y], ...] normalised to 0..1")
    h, w = img.shape[:2]
    lw = width_px or max(2, round(max(h, w) * 0.0035))
    col = hex_to_rgb(color)
    ss = 3
    px = pts * [w, h]
    margin = lw * 6 + 4
    x0, y0 = int(max(0, px[:, 0].min() - margin)), int(max(0, px[:, 1].min() - margin))
    x1, y1 = int(min(w, px[:, 0].max() + margin)), int(min(h, px[:, 1].max() + margin))
    bw, bh = x1 - x0, y1 - y0
    loc = [((p[0] - x0) * ss, (p[1] - y0) * ss) for p in px]
    seq = loc + [loc[0]] if closed else loc

    def stroke(width: int) -> Image.Image:
        a = Image.new("L", (bw * ss, bh * ss), 0)
        d = ImageDraw.Draw(a)
        d.line(seq, fill=255, width=width * ss, joint="curve")
        r = width * ss / 2
        for p in (seq if not closed else loc):  # round caps and joins
            d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=255)
        return a.resize((bw, bh), Image.LANCZOS)

    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    if closed and fill_opacity > 0:
        fa = Image.new("L", (bw * ss, bh * ss), 0)
        ImageDraw.Draw(fa).polygon(loc, fill=int(255 * fill_opacity))
        fill = Image.new("RGBA", (bw, bh), col + (0,))
        fill.putalpha(fa.resize((bw, bh), Image.LANCZOS))
        layer = Image.alpha_composite(layer, fill)
    line_a = stroke(lw)
    if shadow:
        layer = Image.alpha_composite(layer, _shadow(stroke(lw), radius=lw * 1.2, offset=(0, max(1, lw // 2)),
                                                     opacity=0.45))
    line = Image.new("RGBA", (bw, bh), col + (0,))
    line.putalpha(line_a)
    layer = Image.alpha_composite(layer, line)

    full = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    full.paste(layer, (x0, y0))
    if label:
        if label_at is not None:
            lx, ly = label_at[0] * w, label_at[1] * h
        else:
            lx, ly = px[:, 0].mean(), px[:, 1].mean()
        size = max(12, round(min(h, w) * 0.028))
        font = _font(size)
        l, t, r, b = font.getbbox(label)
        tw, th = r - l, b - t
        padx, pady = round(size * 0.6), round(size * 0.38)
        bx0, by0 = int(lx - tw / 2 - padx), int(ly - th / 2 - pady)
        bx1, by1 = int(lx + tw / 2 + padx), int(ly + th / 2 + pady)
        pill_a = Image.new("L", (w, h), 0)
        ImageDraw.Draw(pill_a).rounded_rectangle((bx0, by0, bx1, by1), radius=(by1 - by0) // 2, fill=255)
        full = Image.alpha_composite(full, _shadow(pill_a, radius=size * 0.25, offset=(0, max(1, size // 10)), opacity=0.35))
        pill = Image.new("RGBA", (w, h), col + (0,))
        pill.putalpha(pill_a)
        full = Image.alpha_composite(full, pill)
        txt = (20, 20, 20) if sum(col) > 380 else (255, 255, 255)
        ImageDraw.Draw(full).text((lx - tw / 2 - l, ly - th / 2 - t), label, font=font, fill=txt + (255,))
    return _alpha_over(img, full)
