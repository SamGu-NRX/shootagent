"""Camera moves over a still photo, as in reel/render.py: sub-pixel affine warps, so slow moves stay smooth.

Every photo fills the 9:16 frame edge to edge (a "cover" fit). A landscape photo is then a wide strip the frame
travels across: pans sweep it, and pushes, punches and tilts are aimed by `focus` (default: the middle). That
reads as one camera exploring the room, where a blurred letterbox would read as a pasted-in photo.

A move is a function of progress p in [0, 1] over the shot's visible span (transitions included) returning the
source point at the frame centre and a zoom relative to the cover fit. Pans and pushes run close to linear, so
a move keeps going through a crossfade instead of easing to a stop on either side of it.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from .layers import clamp01, ease_in_out, ease_out, ease_out_back

Cam = tuple[float, float, float]  # (centre x, centre y) in source px, zoom over the cover fit
MAX_ZOOM = 1.32  # sources are prepared with this much headroom, so pushes and punches never upsample


@dataclass
class Source:
    img: np.ndarray  # uint8 RGB, prepared for one output size
    W: int  # output frame size
    H: int

    @property
    def cover(self) -> float:
        """Output px per source px at zoom 1: the photo just covers the frame."""
        h, w = self.img.shape[:2]
        return max(self.W / w, self.H / h)

    @property
    def landscape(self) -> bool:
        h, w = self.img.shape[:2]
        return w > h


def prepare(img: np.ndarray, W: int, H: int) -> Source:
    """Downscale once so the largest zoom maps about one source px to one output px."""
    h, w = img.shape[:2]
    scale = min(1.0, max(W / w, H / h) * MAX_ZOOM)
    if scale < 1.0:
        img = cv2.resize(img, (max(W, round(w * scale)), max(H, round(h * scale))), interpolation=cv2.INTER_AREA)
    return Source(np.ascontiguousarray(img), W, H)


def _lin(p: float, ease: float = 0.35) -> float:
    """Mostly linear with soft ends: a move that never stops dead inside a transition."""
    p = clamp01(p)
    return (1 - ease) * p + ease * ease_in_out(p)


class Rig:
    """Clamps a camera to the photo so the frame never shows past its edges."""

    def __init__(self, src: Source):
        self.src = src
        self.h, self.w = src.img.shape[:2]
        self.s0 = src.cover

    def clamp(self, x: float, y: float, z: float) -> Cam:
        z = max(1.0, z)
        s = self.s0 * z
        hx, hy = self.src.W / (2 * s), self.src.H / (2 * s)
        x = min(self.w - hx, max(hx, x)) if self.w > 2 * hx else self.w / 2
        y = min(self.h - hy, max(hy, y)) if self.h > 2 * hy else self.h / 2
        return x, y, z

    def room(self, z: float) -> tuple[float, float]:
        """How far (source px) the centre can travel at zoom z, horizontally and vertically."""
        s = self.s0 * z
        return max(0.0, self.w - self.src.W / s), max(0.0, self.h - self.src.H / s)


def move(src: Source, motion: str, focus: tuple[float, float] | None, seconds: float,
         punch_at: float = 0.0) -> Callable[[float], Cam]:
    """p in [0, 1] over `seconds` of visible shot -> camera. `punch_at` is where the punch lands, as p."""
    rig = Rig(src)
    W = src.W
    fx, fy = focus if focus else (0.5, 0.5)
    tx, ty = rig.w * fx, rig.h * fy
    cx, cy = rig.w / 2, rig.h / 2
    seconds = max(0.3, seconds)

    if motion in ("push_in", "pull_out"):
        z1 = 1.0 + min(0.24, 0.07 + 0.035 * seconds)

        def push(p: float) -> Cam:
            e = _lin(p)
            if motion == "pull_out":
                e = 1 - e
            return rig.clamp(cx + (tx - cx) * e, cy + (ty - cy) * e, 1.0 + (z1 - 1.0) * e)
        return push

    if motion in ("pan_left", "pan_right"):
        z = 1.0 if src.landscape else 1.13
        room_x = rig.room(z)[0]
        # Travel scales with time (a slow slide, not a whip) and is centred on the focus.
        travel = min(room_x, (0.26 + 0.17 * seconds) * W / (rig.s0 * z))
        mid = min(rig.w - (rig.w - room_x) / 2 - travel / 2, max((rig.w - room_x) / 2 + travel / 2, tx))
        a, b = mid - travel / 2, mid + travel / 2
        if motion == "pan_left":  # the camera turns left: the view slides toward the photo's left side
            a, b = b, a

        def pan(p: float) -> Cam:
            e = _lin(p, 0.25)
            return rig.clamp(a + (b - a) * e, cy + (ty - cy) * 0.3, z + 0.02 * e)
        return pan

    if motion in ("tilt_up", "tilt_down"):
        z = 1.2
        room_y = rig.room(z)[1]
        travel = min(room_y, (0.20 + 0.12 * seconds) * src.H / (rig.s0 * z))
        lo = (rig.h - room_y) / 2
        mid = min(lo + room_y - travel / 2, max(lo + travel / 2, ty))
        a, b = mid + travel / 2, mid - travel / 2  # tilt up: from lower in the photo to higher
        if motion == "tilt_down":
            a, b = b, a

        def tilt(p: float) -> Cam:
            e = _lin(p, 0.3)
            return rig.clamp(cx + (tx - cx) * 0.6, a + (b - a) * e, z + 0.02 * e)
        return tilt

    if motion == "punch_in":
        zp = 1.24
        hit = 0.24 / seconds  # the punch takes ~0.24 s from the moment it lands

        def punch(p: float) -> Cam:
            q = (p - punch_at) / hit
            if q < 0:
                e, drift = 0.0, 0.0
            else:
                e = ease_out_back(q, 1.4)
                drift = 0.035 * clamp01((p - punch_at - hit) / max(1e-3, 1 - punch_at - hit))
            return rig.clamp(cx + (tx - cx) * min(1.0, e), cy + (ty - cy) * min(1.0, e),
                             1.0 + (zp - 1.0) * e + drift)
        return punch

    # static: a very slow drift, so a held shot never reads as a frozen frame
    def drift(p: float) -> Cam:
        e = _lin(p, 0.2)
        return rig.clamp(cx + (tx - cx) * 0.3 + rig.w * 0.012 * (e - 0.5), cy + (ty - cy) * 0.3, 1.035 + 0.03 * e)
    return drift


def render(src: Source, cam: Cam, offset: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """The frame for camera `cam`, shifted by `offset` output px (a hit's camera shake)."""
    x, y, z = cam
    s = src.cover * z
    m = np.array([[s, 0, src.W / 2 - s * x + offset[0]], [0, s, src.H / 2 - s * y + offset[1]]], dtype=np.float64)
    return cv2.warpAffine(src.img, m, (src.W, src.H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def loop_lead_in(move_fn: Callable[[float], Cam], src: Source, seconds: float, first_seconds: float):
    """The camera for a loop ending: it arrives exactly on the opening frame of the first shot, so the last frame
    cuts back to the first without a jump. Where it can, it runs the first move backwards in time (position and
    speed both carry across the loop); otherwise it glides in and settles."""
    rig = Rig(src)
    c0, c1 = move_fn(0.0), move_fn(0.02)
    vel = [(b - a) / 0.02 for a, b in zip(c0, c1)]  # per unit of the first shot's progress
    span = seconds / max(0.3, first_seconds)

    def back(p: float) -> Cam:
        k = (1 - clamp01(p)) * span
        return rig.clamp(c0[0] - vel[0] * k, c0[1] - vel[1] * k, c0[2] - vel[2] * k)

    s = back(0.0)
    if abs(s[0] - c0[0]) + abs(s[1] - c0[1]) + abs(s[2] - c0[2]) * rig.w > rig.w * 0.02:
        return back
    x0, y0, z0 = rig.clamp(c0[0] - rig.w * 0.03, c0[1], c0[2] + 0.05 + 0.01 * min(4.0, seconds))

    def glide(p: float) -> Cam:
        e = ease_out(p)
        return x0 + (c0[0] - x0) * e, y0 + (c0[1] - y0) * e, z0 + (c0[2] - z0) * e
    return glide


def shake(t: float, at: float, amp: float) -> tuple[float, float]:
    """A short decaying camera jolt (px) after a hit at time `at`."""
    k = t - at
    if k < 0 or k > 0.35:
        return 0.0, 0.0
    d = amp * math.exp(-k * 14)
    return d * math.sin(k * 90), d * 0.6 * math.cos(k * 77)
