"""How a shot enters from the previous one. A transition is centred on the cut point: for `d` seconds both shots
are on screen (each still moving with its own camera, overlays included), and progress u runs 0 -> 1 across the
window. Frames are built lazily, so a transition only renders the shots it actually shows at u.
"""
from __future__ import annotations

import math
from collections.abc import Callable

import cv2
import numpy as np

from .layers import ease_in_out

SECONDS = {"cut": 0.0, "crossfade": 0.45, "whip": 0.28, "flash": 0.36, "zoom_through": 0.4, "dip_black": 0.5}
Lazy = Callable[[], np.ndarray]


def length(kind: str, before: float, after: float) -> float:
    """Transition length, kept well inside both shots so a short shot is never all transition."""
    d = SECONDS.get(kind, 0.0)
    return min(d, 0.7 * before, 0.7 * after) if d else 0.0


def zoomed(img: np.ndarray, z: float) -> np.ndarray:
    H, W = img.shape[:2]
    m = np.array([[z, 0, (1 - z) * W / 2], [0, z, (1 - z) * H / 2]], dtype=np.float64)
    return cv2.warpAffine(img, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def zoom_blur(img: np.ndarray, z: float, strength: float) -> np.ndarray:
    """A radial zoom blur: the mean of a few copies scaled progressively further about the centre."""
    if strength < 0.004:
        return zoomed(img, z)
    acc = np.zeros(img.shape, np.uint16)
    n = 5
    for j in range(n):
        acc += zoomed(img, z * (1 + strength * j / (n - 1)))
    return (acc // n).astype(np.uint8)


def blend(kind: str, u: float, a: Lazy, b: Lazy, d: float, fps: float, direction: int = 1) -> np.ndarray:
    """The frame at progress u of a transition from shot a to shot b."""
    if kind == "crossfade":
        w = ease_in_out(u)
        return cv2.addWeighted(a(), 1 - w, b(), w, 0)
    if kind == "dip_black":
        if u < 0.45:
            return cv2.convertScaleAbs(a(), alpha=1 - ease_in_out(u / 0.45))
        if u < 0.55:
            img = b()
            return np.zeros_like(img)
        return cv2.convertScaleAbs(b(), alpha=ease_in_out((u - 0.55) / 0.45))
    if kind == "flash":  # a quick white-out, then the new shot develops out of the white
        peak = 0.28
        if u < peak:
            img, w = a(), (u / peak) ** 1.6
        else:
            img, w = b(), (1 - (u - peak) / (1 - peak)) ** 1.7
        hot = cv2.convertScaleAbs(img, alpha=1 + 1.4 * w)
        return cv2.addWeighted(hot, 1 - w ** 1.5, np.full_like(img, 255), w ** 1.5, 0)
    if kind == "whip":
        return _whip(u, a(), b(), d, fps, direction)
    if kind == "zoom_through":
        if u < 0.5:
            q = u / 0.5
            out = zoom_blur(a(), 1 + 0.85 * q * q, 0.10 * q)
        else:
            q = (u - 0.5) / 0.5
            out = zoom_blur(b(), 1 + 0.55 * (1 - q) ** 2, 0.10 * (1 - q) ** 1.5)
        if 0.4 < u < 0.6:  # a few frames of mix hide the switch inside the blur
            m = 0.5 * (1 - abs(u - 0.5) / 0.1)
            other = zoom_blur(b(), 1.55, 0.1) if u < 0.5 else zoom_blur(a(), 1.85, 0.1)
            out = cv2.addWeighted(out, 1 - m, other, m, 0)
        return out
    return b() if u >= 0.5 else a()


def _whip(u: float, A: np.ndarray, B: np.ndarray, d: float, fps: float, direction: int) -> np.ndarray:
    """A whip pan: both shots slide as one strip, blurred along the motion in proportion to its speed."""
    W = A.shape[1]
    sh = int(round(ease_in_out(u) * W))
    out = np.empty_like(A)
    if direction >= 0:  # content flies left; the next shot arrives from the right
        out[:, :W - sh] = A[:, sh:]
        out[:, W - sh:] = B[:, :sh]
    else:
        out[:, sh:] = A[:, :W - sh]
        out[:, :sh] = B[:, W - sh:]
    speed = 0.5 * math.pi * math.sin(math.pi * u) * W / max(1e-3, d * fps)  # px per frame
    k = int(min(W * 0.5, speed * 1.2))
    if k >= 3:
        out = cv2.blur(out, (k, 1))
        out = cv2.blur(out, (max(3, k // 2), 1))
    return out
