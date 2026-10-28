"""Put an image on a TV or monitor: quad from the screen mask, perspective warp, glass sheen."""
from __future__ import annotations

import cv2
import numpy as np

from .composite import composite


def order_quad(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s, d = pts.sum(axis=1), pts[:, 0] - pts[:, 1]
    return np.array([pts[np.argmin(s)], pts[np.argmax(d)], pts[np.argmax(s)], pts[np.argmin(d)]], np.float32)


def quad_from_mask(mask: np.ndarray) -> np.ndarray:
    """Corners (TL, TR, BR, BL) in pixels of the largest blob in `mask`.

    Tries polygon simplification to exactly 4 vertices; if the outline never simplifies to 4 (rounded
    bezels, partial occlusion) it falls back to the hull's extreme points along the diagonals."""
    m = mask.astype(np.uint8)
    if m.sum() == 0:
        raise ValueError("screen mask is empty")
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(cnts, key=cv2.contourArea)
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return order_quad(approx.reshape(4, 2))
    return order_quad(hull.reshape(-1, 2)[[
        np.argmin(hull[:, 0, 0] + hull[:, 0, 1]), np.argmax(hull[:, 0, 0] - hull[:, 0, 1]),
        np.argmax(hull[:, 0, 0] + hull[:, 0, 1]), np.argmin(hull[:, 0, 0] - hull[:, 0, 1])]])


def _cover(content: np.ndarray, aspect: float) -> np.ndarray:
    """Centre-crop `content` to width/height = aspect."""
    h, w = content.shape[:2]
    if w / h > aspect:
        nw = int(round(h * aspect))
        x0 = (w - nw) // 2
        return content[:, x0:x0 + nw]
    nh = int(round(w / aspect))
    y0 = (h - nh) // 2
    return content[y0:y0 + nh]


def place_on_screen(img: np.ndarray, mask: np.ndarray, content: np.ndarray, sheen: float = 0.07,
                    quad: np.ndarray | None = None, feather_px: int = 1) -> np.ndarray:
    """Warp `content` onto the screen quad and composite it through `mask`.

    The content is centre-cropped to the screen's aspect (from the quad's side lengths), warped by
    homography, given a faint diagonal glass sheen (`sheen` = peak white opacity) and a slight edge
    falloff, then composited so pixels outside the mask's feather support stay byte-identical."""
    if not 0 <= sheen <= 0.3:
        raise ValueError(f"sheen {sheen} must be within 0..0.3")
    m = mask.astype(bool)
    q = order_quad(quad) if quad is not None else quad_from_mask(m)
    h, w = img.shape[:2]
    top, bot = np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])
    left, right = np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])
    aspect = max(1e-3, (top + bot) / 2) / max(1e-3, (left + right) / 2)
    src = _cover(content, aspect)
    sh, sw = src.shape[:2]
    # Supersample: warp from a source at least as large as the quad on screen.
    target_w = int(max(top, bot) * 1.5)
    if sw < target_w:
        src = cv2.resize(src, (target_w, max(1, round(target_w / aspect))), interpolation=cv2.INTER_CUBIC)
        sh, sw = src.shape[:2]
    S = np.array([[0, 0], [sw, 0], [sw, sh], [0, sh]], np.float32)
    Hm = cv2.getPerspectiveTransform(S, q)
    warped = cv2.warpPerspective(src, Hm, (w, h), flags=cv2.INTER_AREA if sw > target_w else cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
    # Glass: a soft diagonal highlight in screen coordinates plus a gentle darkening toward the edges.
    u, v = np.meshgrid(np.linspace(0, 1, sw, dtype=np.float32), np.linspace(0, 1, sh, dtype=np.float32))
    band = np.clip(1 - np.abs((u * 0.8 + v * 1.2) - 0.55) / 0.45, 0, 1) ** 2 * sheen
    edge = 1 - 0.06 * (np.abs(u - 0.5) * 2) ** 3 - 0.06 * (np.abs(v - 0.5) * 2) ** 3
    gl = cv2.warpPerspective(np.dstack([band, edge]), Hm, (w, h), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
    out = warped * gl[..., 1:2]
    out = out * (1 - gl[..., 0:1]) + 255.0 * gl[..., 0:1]
    out = np.clip(np.round(out), 0, 255).astype(np.uint8)
    return composite(img, out, m, feather_px)


place = place_on_screen  # name used by tools/_ops.py
