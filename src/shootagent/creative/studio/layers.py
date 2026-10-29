"""Premultiplied RGBA sprites and the animated overlays built from them.

All type and graphics are drawn once with PIL while the reel is planned (PIL's FreeType faces are not
thread-safe, and per-frame text layout would be slow); a frame only warps and blends finished sprites.
Sprites are float32 premultiplied RGBA cropped to their ink, so blending touches a small box, not the frame.
Frames are uint8 RGB, written in place.

An `Overlay` is a group of `Element`s, each a sprite at an anchor point with an animation: local time ->
`State` (offset, scale, rotation, opacity), or None while hidden. Every animation is a pure function of time,
so frames can be rendered in any order on any thread.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

RGB = tuple[int, int, int]


# ---- easing -------------------------------------------------------------------------------------------------

def clamp01(p: float) -> float:
    return 0.0 if p <= 0 else 1.0 if p >= 1 else p


def ease_out(p: float) -> float:
    p = clamp01(p)
    return 1 - (1 - p) ** 3


def ease_in(p: float) -> float:
    return clamp01(p) ** 3


def ease_in_out(p: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * clamp01(p))


def ease_out_back(p: float, s: float = 1.7) -> float:
    p = clamp01(p) - 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


# ---- sprites ------------------------------------------------------------------------------------------------

@dataclass
class Sprite:
    px: np.ndarray  # (h, w, 4) float32 premultiplied, 0..1
    ax: float  # anchor point inside px: elements are positioned by it, and scale/rotate about it
    ay: float

    @property
    def w(self) -> int:
        return self.px.shape[1]

    @property
    def h(self) -> int:
        return self.px.shape[0]


@dataclass(frozen=True)
class Shadow:
    blur: float
    opacity: float
    dx: float = 0.0
    dy: float = 0.0
    spread: int = 0  # grow the shape (px) before blurring: a soft scrim instead of a thin halo
    color: RGB = (0, 0, 0)


def premultiply(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGBA"), dtype=np.float32) / 255.0
    a[..., :3] *= a[..., 3:4]
    return np.ascontiguousarray(a)


def to_sprite(img: Image.Image, ax: float, ay: float, shadows: tuple[Shadow, ...] = ()) -> Sprite:
    """Add shadows under `img` (growing the canvas to fit them) and premultiply."""
    if shadows:
        img, (ax, ay) = add_shadows(img, (ax, ay), shadows)
    return Sprite(premultiply(img), ax, ay)


def add_shadows(img: Image.Image, anchor: tuple[float, float], shadows: tuple[Shadow, ...]):
    pad = int(max(3 * s.blur + max(abs(s.dx), abs(s.dy)) + s.spread for s in shadows)) + 2
    w, h = img.size
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    alpha = Image.new("L", out.size, 0)
    alpha.paste(img.getchannel("A"), (pad, pad))
    for s in shadows:
        a = alpha
        if s.spread or s.blur > 6:
            # Blur (and spread) at quarter size: big soft scrims cost nothing that way and look the same.
            q = 4
            small = a.resize((max(1, a.width // q), max(1, a.height // q)), Image.Resampling.BOX)
            if s.spread:
                k = max(3, (2 * round(s.spread / q)) | 1)
                small = small.filter(ImageFilter.MaxFilter(k))
            small = small.filter(ImageFilter.GaussianBlur(max(0.5, s.blur / q)))
            a = small.resize(a.size, Image.Resampling.BILINEAR)
        elif s.blur:
            a = a.filter(ImageFilter.GaussianBlur(s.blur))
        a = a.point(lambda v, o=s.opacity: int(v * o))
        layer = Image.new("RGBA", out.size, s.color + (0,))
        layer.putalpha(a)
        if s.dx or s.dy:
            moved = Image.new("RGBA", out.size, (0, 0, 0, 0))
            moved.paste(layer, (round(s.dx), round(s.dy)))
            layer = moved
        out = Image.alpha_composite(out, layer)
    out.alpha_composite(img, (pad, pad))
    return out, (anchor[0] + pad, anchor[1] + pad)


def advance(text: str, f: ImageFont.FreeTypeFont, tracking: float = 0.0) -> float:
    if not tracking:
        return f.getlength(text)
    return sum(f.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1)


def text_image(text: str, f: ImageFont.FreeTypeFont, fill: RGB | tuple, stroke: int = 0, stroke_fill: RGB = (0, 0, 0),
               tracking: float = 0.0) -> tuple[Image.Image, tuple[float, float]]:
    """One line of text. Returns the image and its origin (left of the advance, baseline) inside it."""
    ascent, descent = f.getmetrics()
    pad = stroke + max(4, int(f.size * 0.08))
    width = advance(text, f, tracking)
    img = Image.new("RGBA", (math.ceil(width) + 2 * pad, ascent + descent + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y = pad, pad + ascent
    fill = tuple(fill) if len(fill) == 4 else tuple(fill) + (255,)
    if not tracking:
        d.text((x0, y), text, font=f, fill=fill, anchor="ls", stroke_width=stroke, stroke_fill=stroke_fill)
        return img, (x0, y)
    # Strokes first, then fills, so a letter's stroke never covers its neighbour's fill.
    for pass_ in (("stroke",) if stroke else ()) + ("fill",):
        x = x0
        for ch in text:
            if pass_ == "stroke":
                d.text((x, y), ch, font=f, fill=stroke_fill, anchor="ls", stroke_width=stroke, stroke_fill=stroke_fill)
            else:
                d.text((x, y), ch, font=f, fill=fill, anchor="ls")
            x += f.getlength(ch) + tracking
    return img, (x0, y)


def text_sprite(text: str, f: ImageFont.FreeTypeFont, fill: RGB = (255, 255, 255), stroke: int = 0,
                stroke_fill: RGB = (0, 0, 0), tracking: float = 0.0, shadows: tuple[Shadow, ...] = ()) -> Sprite:
    """A line of text anchored at its origin (left of the advance, baseline)."""
    img, (ox, oy) = text_image(text, f, fill, stroke, stroke_fill, tracking)
    return to_sprite(img, ox, oy, shadows)


def rounded_rect(w: int, h: int, radius: float, fill: tuple, outline: tuple | None = None, width: int = 0,
                 ss: int = 4) -> Image.Image:
    """An anti-aliased rounded rectangle (drawn 4x and reduced: PIL's own shapes have hard edges)."""
    w, h = max(1, int(round(w))), max(1, int(round(h)))
    big = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    fill = tuple(fill) if len(fill) == 4 else tuple(fill) + (255,)
    d.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=max(0, radius * ss), fill=fill,
                        outline=outline, width=width * ss)
    return big.resize((w, h), Image.Resampling.LANCZOS)


def solid(w: int, h: int, color: tuple) -> Image.Image:
    color = tuple(color) if len(color) == 4 else tuple(color) + (255,)
    return Image.new("RGBA", (max(1, int(round(w))), max(1, int(round(h)))), color)


# ---- drawing ------------------------------------------------------------------------------------------------

def blit(frame: np.ndarray, px: np.ndarray, x: int, y: int, alpha: float = 1.0) -> None:
    """Composite premultiplied `px` onto the uint8 RGB frame with its top-left corner at (x, y), in place."""
    if alpha <= 0.003:
        return
    H, W = frame.shape[:2]
    h, w = px.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    s = px[y0 - y:y1 - y, x0 - x:x1 - x]
    a = s[..., 3:4] * alpha
    region = frame[y0:y1, x0:x1].astype(np.float32)
    region *= 1.0 - a
    region += s[..., :3] * (255.0 * alpha)
    np.clip(region, 0, 255, out=region)
    frame[y0:y1, x0:x1] = (region + 0.5).astype(np.uint8)


def draw(frame: np.ndarray, sp: Sprite, x: float, y: float, scale: float = 1.0, rot: float = 0.0,
         alpha: float = 1.0, crop: tuple[float, float] | None = None, sx: float = 1.0) -> None:
    """Draw `sp` with its anchor at frame point (x, y), scaled (and stretched by sx horizontally) and rotated
    (degrees, clockwise) about the anchor. `crop` keeps sprite columns [a, b) only (wipes, typing)."""
    if alpha <= 0.003 or scale <= 0.01:
        return
    px, ax, ay = sp.px, sp.ax, sp.ay
    if crop is not None:
        a, b = max(0, int(crop[0])), min(px.shape[1], int(math.ceil(crop[1])))
        if b <= a:
            return
        px, ax = px[:, a:b], ax - a
    H, W = frame.shape[:2]
    if rot == 0 and abs(scale - 1) < 1e-4 and sx == 1.0:
        tx, ty = x - ax, y - ay
        if abs(tx - round(tx)) < 0.02 and abs(ty - round(ty)) < 0.02:
            blit(frame, px, round(tx), round(ty), alpha)
            return
    th = math.radians(rot)
    c, s = math.cos(th) * scale, math.sin(th) * scale
    m = np.array([[c * sx, -s], [s * sx, c]], dtype=np.float64)
    h, w = px.shape[:2]
    corners = np.array([[-ax, -ay], [w - ax, -ay], [-ax, h - ay], [w - ax, h - ay]]) @ m.T + [x, y]
    ix0, iy0 = max(0, math.floor(corners[:, 0].min()) - 1), max(0, math.floor(corners[:, 1].min()) - 1)
    ix1, iy1 = min(W, math.ceil(corners[:, 0].max()) + 1), min(H, math.ceil(corners[:, 1].max()) + 1)
    if ix0 >= ix1 or iy0 >= iy1:
        return
    t = np.array([x - ix0, y - iy0]) - m @ np.array([ax, ay])
    M = np.hstack([m, t[:, None]])
    patch = cv2.warpAffine(px, M, (ix1 - ix0, iy1 - iy0), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    blit(frame, patch, ix0, iy0, alpha)


# ---- animated overlays ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class State:
    dx: float = 0.0
    dy: float = 0.0
    scale: float = 1.0
    rot: float = 0.0
    alpha: float = 1.0
    sx: float = 1.0


Anim = Callable[[float], State | None]


@dataclass
class Element:
    sprite: Sprite
    x: float  # anchor position in frame px
    y: float
    anim: Anim
    crop: Callable[[float], tuple[float, float] | None] | None = None

    def draw(self, frame: np.ndarray, t: float) -> None:
        st = self.anim(t)
        if st is None or st.alpha <= 0.003:
            return
        crop = self.crop(t) if self.crop else None
        if self.crop and crop is None:
            return
        draw(frame, self.sprite, self.x + st.dx, self.y + st.dy, st.scale, st.rot, st.alpha, crop, st.sx)


@dataclass
class Overlay:
    """Elements on a shared clock: local time 0 is reel time `t0`; nothing is drawn outside [t0, t1)."""

    t0: float
    t1: float
    elements: list[Element] = field(default_factory=list)
    name: str = ""

    def draw(self, frame: np.ndarray, t: float) -> None:
        if not (self.t0 <= t < self.t1):
            return
        lt = t - self.t0
        for e in self.elements:
            e.draw(frame, lt)


# ---- the animation vocabulary -----------------------------------------------------------------------------

def _out_alpha(t: float, t_out: float | None, fade: float) -> float:
    if t_out is None or fade <= 0:
        return 1.0 if t_out is None or t < t_out else 0.0
    return 1.0 - ease_in_out((t - (t_out - fade)) / fade)


def pop(t_in: float, t_out: float | None, dur: float = 0.18, start: float = 0.5, overshoot: float = 2.0,
        fade_out: float = 0.14, rot0: float = 0.0, rot1: float = 0.0, rise: float = 0.0) -> Anim:
    """Lands with a scale overshoot (words, stickers, cards): opaque within 50 ms, settled in `dur`."""
    def f(t: float) -> State | None:
        if t < t_in:
            return None
        p = (t - t_in) / dur
        e = ease_out_back(p, overshoot)
        out = _out_alpha(t, t_out, fade_out)
        if out <= 0:
            return None
        return State(dy=rise * (1 - ease_out(p)), scale=start + (1 - start) * e - 0.08 * (1 - out),
                     rot=rot0 + (rot1 - rot0) * ease_out(p), alpha=min(1.0, (t - t_in) / 0.05) * out)
    return f


def fade_rise(t_in: float, t_out: float | None, rise: float = 14.0, dur: float = 0.45, fade_out: float = 0.3,
              scale0: float = 1.0) -> Anim:
    """Fades in while drifting up a few pixels (elegant labels, end-card lines)."""
    def f(t: float) -> State | None:
        if t < t_in:
            return None
        p = ease_out((t - t_in) / dur)
        out = _out_alpha(t, t_out, fade_out)
        if out <= 0:
            return None
        return State(dy=rise * (1 - p), scale=scale0 + (1 - scale0) * p, alpha=p * out)
    return f


def slam(t_in: float, t_out: float | None, start: float = 1.9, dur: float = 0.16, fade_out: float = 0.14,
         shake: float = 0.0) -> Anim:
    """Drops in from big to its size and lands hard (countdown numbers, punchlines)."""
    def f(t: float) -> State | None:
        if t < t_in:
            return None
        p = clamp01((t - t_in) / dur)
        out = _out_alpha(t, t_out, fade_out)
        if out <= 0:
            return None
        k = t - t_in - dur
        jolt = shake * math.exp(-k * 18) * math.sin(k * 70) if k > 0 else 0.0
        return State(dx=jolt, dy=-0.6 * jolt, scale=start + (1 - start) * (1 - (1 - p) ** 2),
                     alpha=min(1.0, (t - t_in) / 0.06) * out)
    return f


def hold(t_in: float, t_out: float | None, fade_in: float = 0.0, fade_out: float = 0.0) -> Anim:
    def f(t: float) -> State | None:
        if t < t_in:
            return None
        a = 1.0 if fade_in <= 0 else ease_out((t - t_in) / fade_in)
        out = _out_alpha(t, t_out, fade_out)
        return None if out <= 0 else State(alpha=a * out)
    return f
