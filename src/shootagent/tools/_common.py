"""Helpers shared by the agent's tools: loading versions, masks, and the small images shown to the model.

The images built here (contact sheets, crops, before/after pairs, mask overlays) are presentation only;
they never become a version. Pixel edits go through `tools/_ops.py`, which calls `imaging/` and `models/`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import cv2
import numpy as np

from ..store import Shoot, View
from .base import ToolContext

MODEL_LONG_EDGE = 1024  # long edge of images sent to the model; ~1.4k tokens each
MASK_COLORS = [(255, 64, 64), (64, 200, 255), (255, 210, 0), (120, 255, 120), (255, 80, 255), (255, 150, 40)]


class ToolError(Exception):
    """A tool refused its input. The message is shown to the model as-is, so say what to do instead."""


@dataclass
class Loaded:
    shoot: Shoot
    view: View


def load_view(ctx: ToolContext, view_id: str) -> Loaded:
    shoot = ctx.store.load(ctx.shoot_id)
    try:
        return Loaded(shoot, shoot.view(view_id))
    except KeyError:
        ids = ", ".join(v.id for v in shoot.views)
        raise ToolError(f"No view {view_id!r} in this shoot. Views are: {ids}.") from None


def version_image(ctx: ToolContext, view: View, version: str | None = None) -> tuple[np.ndarray, str]:
    """RGB image of `version` (default: head) and its version id."""
    if view.head is None:
        raise ToolError(f"View {view.id} has no versions yet; it was not ingested correctly.")
    vid = version or view.head
    try:
        v = view.version(vid)
    except KeyError:
        ids = ", ".join(x.id for x in view.versions)
        raise ToolError(f"View {view.id} has no version {vid!r}. Versions are: {ids}.") from None
    return ctx.store.get_image(ctx.shoot_id, v.blob), vid


def load_mask(ctx: ToolContext, shoot: Shoot, view: View, mask_id: str, shape: tuple[int, int]) -> np.ndarray:
    """Boolean mask resized to `shape` (H, W). Masks from another view are refused."""
    rec = shoot.masks.get(mask_id)
    if rec is None:
        mine = [m for m, r in shoot.masks.items() if r.view == view.id]
        raise ToolError(
            f"No mask {mask_id!r}. Masks on {view.id}: {', '.join(mine) or 'none'}; make one with "
            "find_regions or mask_from_box."
        )
    if rec.view != view.id:
        raise ToolError(
            f"Mask {mask_id} belongs to view {rec.view}, not {view.id}. Make a mask on {view.id}."
        )
    m = ctx.store.get_image(ctx.shoot_id, rec.blob) > 127
    if m.shape != shape:
        m = cv2.resize(m.astype(np.uint8) * 255, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR) > 127
    if not m.any():
        raise ToolError(f"Mask {mask_id} is empty.")
    return m


def box_to_mask(box: list[float], shape: tuple[int, int]) -> np.ndarray:
    x0, y0, x1, y1 = norm_box(box)
    h, w = shape
    m = np.zeros((h, w), bool)
    m[
        int(y0 * h) : max(int(y0 * h) + 1, int(round(y1 * h))),
        int(x0 * w) : max(int(x0 * w) + 1, int(round(x1 * w))),
    ] = True
    return m


def norm_box(box: list[float]) -> tuple[float, float, float, float]:
    if len(box) != 4:
        raise ToolError("A box is [x0, y0, x1, y1] with each value between 0 and 1.")
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in box)
    if x1 <= x0 or y1 <= y0:
        raise ToolError(f"Box {box} has no area; x1 must exceed x0 and y1 must exceed y0 (values 0..1).")
    return x0, y0, x1, y1


def mask_bbox(mask: np.ndarray) -> list[float]:
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    return [
        round(xs.min() / w, 4),
        round(ys.min() / h, 4),
        round((xs.max() + 1) / w, 4),
        round((ys.max() + 1) / h, 4),
    ]


# ---- images for the model ------------------------------------------------------------------------------


def resize_long(img: np.ndarray, long_edge: int) -> np.ndarray:
    h, w = img.shape[:2]
    if max(h, w) <= long_edge:
        return img
    s = long_edge / max(h, w)
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def put_preview(ctx: ToolContext, img: np.ndarray, long_edge: int = MODEL_LONG_EDGE) -> str:
    """Store a downscaled JPEG for the model and the UI; returns its blob name."""
    return ctx.store.put_image(ctx.shoot_id, np.ascontiguousarray(resize_long(img, long_edge)), fmt="jpg")


def label(img: np.ndarray, text: str, corner: str = "tl", scale: float | None = None) -> np.ndarray:
    """Draw a white-on-black label in a corner (returns a copy)."""
    out = img.copy()
    h, w = out.shape[:2]
    scale = scale or max(0.5, w / 900)
    th = max(1, int(scale * 2))
    (tw, tht), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
    pad = int(6 * scale)
    x = pad if corner in ("tl", "bl") else w - tw - 2 * pad
    y = pad if corner in ("tl", "tr") else h - tht - base - 2 * pad
    cv2.rectangle(out, (x, y), (x + tw + 2 * pad, y + tht + base + 2 * pad), (0, 0, 0), -1)
    cv2.putText(
        out, text, (x + pad, y + pad + tht), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), th, cv2.LINE_AA
    )
    return out


def crop_norm(img: np.ndarray, box: list[float] | tuple[float, ...]) -> np.ndarray:
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    return img[
        int(y0 * h) : max(int(y0 * h) + 1, int(round(y1 * h))),
        int(x0 * w) : max(int(x0 * w) + 1, int(round(x1 * w))),
    ]


def context_box(
    bbox: list[float] | None, pad: float = 0.35, min_side: float = 0.25
) -> tuple[float, float, float, float]:
    """A crop box around `bbox` with padding, at least `min_side` of the frame; whole frame when None."""
    if bbox is None:
        return (0.0, 0.0, 1.0, 1.0)
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    bw = max((x1 - x0) * (1 + 2 * pad), min_side)
    bh = max((y1 - y0) * (1 + 2 * pad), min_side)
    if bw >= 0.9 and bh >= 0.9:
        return (0.0, 0.0, 1.0, 1.0)
    nx0, ny0 = min(max(0.0, cx - bw / 2), max(0.0, 1 - bw)), min(max(0.0, cy - bh / 2), max(0.0, 1 - bh))
    return (nx0, ny0, min(1.0, nx0 + bw), min(1.0, ny0 + bh))


def side_by_side(images: list[np.ndarray], names: list[str], height: int = 640) -> np.ndarray:
    tiles = []
    for img, name in zip(images, names):
        if img.ndim == 2:
            img = np.repeat(img[..., None], 3, axis=2)
        s = height / img.shape[0]
        tile = cv2.resize(img, (max(1, round(img.shape[1] * s)), height), interpolation=cv2.INTER_AREA)
        tiles.append(label(tile, name, scale=0.8))
        tiles.append(np.full((height, 8, 3), 255, np.uint8))
    return np.concatenate(tiles[:-1], axis=1)


def before_after(ctx: ToolContext, before: np.ndarray, after: np.ndarray, bbox: list[float] | None) -> str:
    """Store a 'before | after' crop around `bbox` (whole frame when None); returns its blob."""
    box = context_box(bbox)
    pair = side_by_side([crop_norm(before, box), crop_norm(after, box)], ["before", "after"])
    return put_preview(ctx, pair, long_edge=1400)


def overlay_masks(img: np.ndarray, masks: list[np.ndarray], names: list[str]) -> np.ndarray:
    """Tint each mask in its own colour, outline it, and write its name at its top-left corner."""
    small = resize_long(img, MODEL_LONG_EDGE).astype(np.float32)
    h, w = small.shape[:2]
    out = small.copy()
    for i, (m, name) in enumerate(zip(masks, names)):
        color = np.array(MASK_COLORS[i % len(MASK_COLORS)], np.float32)
        ms = cv2.resize(m.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_LINEAR) > 127
        out[ms] = out[ms] * 0.45 + color * 0.55
        contours, _ = cv2.findContours(ms.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, contours, -1, tuple(float(c) for c in color), 2)
    out = out.clip(0, 255).astype(np.uint8)
    for i, (m, name) in enumerate(zip(masks, names)):
        ys, xs = np.nonzero(cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST))
        if len(xs):
            x, y = int(xs.min()), int(ys.min())
            cv2.putText(
                out, name, (x + 3, max(14, y + 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA
            )
            cv2.putText(
                out,
                name,
                (x + 3, max(14, y + 16)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
    return out


def contact_sheet(tiles: list[tuple[str, np.ndarray]], cols: int = 4, tile_w: int = 360) -> np.ndarray:
    """Grid of labelled thumbnails, one per view."""
    if not tiles:
        return np.full((64, 256, 3), 255, np.uint8)
    cols = min(cols, len(tiles))
    thumbs = []
    for name, img in tiles:
        s = tile_w / img.shape[1]
        t = cv2.resize(img, (tile_w, max(1, round(img.shape[0] * s))), interpolation=cv2.INTER_AREA)
        thumbs.append(label(t, name, scale=0.7))
    th = max(t.shape[0] for t in thumbs)
    gap = 6
    rows = (len(thumbs) + cols - 1) // cols
    sheet = np.full((rows * (th + gap) - gap, cols * (tile_w + gap) - gap, 3), 255, np.uint8)
    for i, t in enumerate(thumbs):
        r, c = divmod(i, cols)
        sheet[
            r * (th + gap) : r * (th + gap) + t.shape[0], c * (tile_w + gap) : c * (tile_w + gap) + tile_w
        ] = t
    return sheet


def image_block(ctx: ToolContext, blob: str) -> dict:
    """Anthropic image content block for a stored blob."""
    data = ctx.store.blob_path(ctx.shoot_id, blob).read_bytes()
    media = "image/png" if blob.endswith(".png") else "image/jpeg"
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media, "data": base64.standard_b64encode(data).decode()},
    }


# ---- colour ---------------------------------------------------------------------------------------------


def parse_hex(value: str) -> tuple[int, int, int]:
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ToolError(f"{value!r} is not a hex colour; use the form #C8C2B7.")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def to_hex(rgb) -> str:
    r, g, b = (int(round(min(255, max(0, float(c))))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def lab_of(rgb: np.ndarray) -> np.ndarray:
    """CIELAB (L 0..100) of an (N, 3) or (3,) uint8/float RGB array."""
    a = np.asarray(rgb, np.float32).reshape(-1, 1, 3) / 255.0
    return cv2.cvtColor(a, cv2.COLOR_RGB2LAB).reshape(-1, 3)


def mean_rgb(img: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
    px = img[mask] if mask is not None else img.reshape(-1, 3)
    return px.reshape(-1, 3).astype(np.float64).mean(axis=0)


def delta_e(rgb_a, rgb_b) -> float:
    la, lb = lab_of(np.asarray(rgb_a)), lab_of(np.asarray(rgb_b))
    return float(np.linalg.norm(la[0] - lb[0]))


def luminance(img: np.ndarray) -> np.ndarray:
    f = img.astype(np.float32) / 255.0
    lin = np.where(f <= 0.04045, f / 12.92, ((f + 0.055) / 1.055) ** 2.4)
    return lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)
