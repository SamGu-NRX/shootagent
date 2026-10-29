"""A camera move from one photo that invents nothing: depth parallax, the fallback for a rejected clip.

Ported from experiments/film/parallax.py on branch explore/film-lab (commit 9bc0cd6); the rendering code is
unchanged apart from the clip length (a parameter, 6 s by default to match a Grok clip) and the device and
GPU lock, which come from models/device.py like every other local model.

Depth Anything V2 Small (Apache-2.0) estimates relative depth; every source pixel is then reprojected
for a slowly moving camera and splatted into the 1080x1920 frame, nearer pixels winning where several
land together. Where the move uncovers background the photo never saw, the frame has a hole; holes are
counted (the disoccluded fraction) and filled from the background side of the hole only, so a
foreground object never smears into what it uncovered.

Moves (eased):
- dolly: the camera moves forward;
- truck: the camera moves sideways while the 9:16 window travels the same way across the photo.
Landscape photos are shown through a 9:16 window; portrait photos fill the frame.

The size of a move is set as parallax, not distance: the largest shift, in output pixels, between the
nearest and farthest points of the photo by the end of the move. Depth is only known up to scale, so this
is the quantity that decides how much background gets uncovered. In the experiment 24 px passed the clip
checker on 6 of 6 clips and 40 px on 5 of 10.
"""
from __future__ import annotations

import math
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

from ..models.device import get_device, gpu_lock
from .video import VideoWriter

MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
OUT_W, OUT_H = 1080, 1920
FPS = 30
SRC_DENSITY = 1.25  # source px per output px along each axis, so a 1.1x magnification still leaves no cracks
NEAR_FAR = 6.0  # assumed ratio of farthest to nearest depth; only the shape of the depth curve depends on it
SOFTMAX_BETA = 60.0  # splat weight exp(beta * normalised inverse depth): 0.05 nearer counts e^3 = 20x more
HOLE_COVERAGE = 0.35  # output pixels receiving less splat weight than this are holes
TRUCK_TRAVEL = 0.35  # landscape truck: fraction of the free horizontal range the window travels


def ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, t)))


class Depth:
    def __init__(self, device: str):
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        self.device = device
        self.proc = AutoImageProcessor.from_pretrained(MODEL)
        self.model = AutoModelForDepthEstimation.from_pretrained(MODEL).to(device).eval()

    @torch.inference_mode()
    def __call__(self, rgb: np.ndarray, size: tuple[int, int]) -> np.ndarray:
        """Relative inverse depth (larger is nearer) at `size` (w, h), normalised to 0..1 by the
        2nd and 98th percentiles."""
        inputs = self.proc(images=Image.fromarray(rgb), return_tensors="pt").to(self.device)
        pred = self.model(**inputs).predicted_depth[:, None]
        disp = torch.nn.functional.interpolate(pred, size=(size[1], size[0]), mode="bicubic", align_corners=False)[0, 0]
        disp = disp.float().cpu().numpy()
        lo, hi = np.percentile(disp, [2, 98])
        return np.clip((disp - lo) / max(hi - lo, 1e-6), 0, 1).astype(np.float32)


def sharpen_depth(d: np.ndarray, k: int) -> np.ndarray:
    """Snap each pixel on a depth ramp to the nearer of its neighbourhood's min and max. The network's
    depth edges are soft; left soft, an object's edge stretches across what it uncovers like a rubber
    sheet instead of leaving a hole that can be counted and filled from the background."""
    d = cv2.medianBlur(d, 5)
    kernel = np.ones((k, k), np.uint8)
    lo, hi = cv2.erode(d, kernel), cv2.dilate(d, kernel)
    snapped = np.where(d - lo < hi - d, lo, hi)
    return np.where(hi - lo > 0.08, snapped, d).astype(np.float32)


def plan(pw: int, ph: int, move: str, parallax_out: float) -> dict:
    """Window and camera path in work-image pixels for a photo already scaled to (pw, ph)."""
    win_h = SRC_DENSITY * OUT_H
    win_w = win_h * OUT_W / OUT_H
    scale = OUT_W / win_w  # work px -> output px
    par = parallax_out / scale  # parallax in work px
    margin = 1.6 * par + 8  # the nearest pixels shift by up to par / (1 - 1/NEAR_FAR) in a truck
    cy = ph / 2
    lo_x, hi_x = win_w / 2 + margin, pw - win_w / 2 - margin
    if lo_x > hi_x:
        raise ValueError(f"photo too narrow for a {parallax_out}px move: {pw}x{ph} work px")
    if move == "dolly":
        x0 = x1 = pw / 2
    elif move == "truck":
        free = hi_x - lo_x
        travel = free * (TRUCK_TRAVEL if pw > ph else 1.0)
        x0, x1 = pw / 2 - travel / 2, pw / 2 + travel / 2
    else:
        raise ValueError(f"unknown move {move!r}")
    return {"win_w": win_w, "win_h": win_h, "scale": scale, "par": par, "cy": cy, "x0": x0, "x1": x1}


def background_layer(photo: np.ndarray, disp: np.ndarray, par_work: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """What lies behind the foreground near each depth edge, for a larger move than holes can be filled for.

    Pixels within the move's reach of a depth edge, on its near side, are removed and LaMa (big-lama, already
    used for removals) paints what is behind them, once per clip. That layer is splatted under the photo at
    the far side's depth, so where the move uncovers background the frame shows LaMa's fill, the same in
    every frame. Without it a 40 px move leaves holes that per-frame TELEA fill smears and flickers.
    Returns (rgb, disparity, mask) at work resolution."""
    from ..models.inpaint import lama_inpaint

    k = 2 * int(math.ceil(1.6 * par_work)) + 1
    far = cv2.erode(disp, np.ones((k, k), np.uint8))
    mask = (disp - far) > 0.08
    if not mask.any():
        return photo, disp, mask
    # LaMa must not see the foreground it is painting behind: a hole that covers only the rim of a chair
    # gets filled from the chair's own interior, which showed as a dark outline wherever the move uncovered
    # the rim. So the hole is every pixel nearer than the background over a four times wider reach.
    kk = 4 * k + 1
    hole = (disp - cv2.erode(disp, np.ones((kk, kk), np.uint8))) > 0.08
    return lama_inpaint(photo, hole | mask, dilate_px=4, feather_px=2), far, mask


def render(photo: np.ndarray, disp: np.ndarray, move: str, parallax_out: float, path: Path, device: str,
           seconds: float = 6.0, bg: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None) -> dict:
    """Render one clip; returns stats. `photo` and `disp` are already at work resolution."""
    ph, pw = disp.shape
    p = plan(pw, ph, move, parallax_out)
    # inverse depth in units of the nearest depth: 1 (nearest) .. 1/NEAR_FAR (farthest)
    w = (1.0 / NEAR_FAR + disp * (1 - 1.0 / NEAR_FAR)).astype(np.float32)
    w_lo, w_hi = np.percentile(w, [1, 99])
    cx0, cy0 = pw / 2, ph / 2
    if move == "dolly":
        # magnification 1/(1 - tz*w); the near/far difference at the window corner equals the parallax
        r = math.hypot(p["win_w"], p["win_h"]) / 2
        tz, sx = p["par"] / (r * (w_hi - w_lo)), 0.0  # first order in tz; the true shift is a few % larger
    else:
        tz, sx = 0.0, p["par"] / (w_hi - w_lo)  # sx = focal * baseline, in work px
    t_dev = lambda a: torch.from_numpy(np.ascontiguousarray(a)).to(device)
    rgb_t, w_t, d_t = t_dev(photo.astype(np.float32)), t_dev(w), t_dev(disp)
    bg_t = None
    if bg is not None and bg[2].any():
        b_rgb, b_disp, b_mask = bg
        b_w = (1.0 / NEAR_FAR + b_disp * (1 - 1.0 / NEAR_FAR)).astype(np.float32)
        bg_t = (t_dev(b_rgb.astype(np.float32)), t_dev(b_w), t_dev(b_disp.astype(np.float32)), t_dev(b_mask))
    n = round(seconds * FPS)
    holes, t0 = [], time.perf_counter()
    # Hole filling (OpenCV, releases the GIL) runs on worker threads while the next frame is splatted;
    # frames are written in order.
    pending: deque = deque()
    with VideoWriter(path, OUT_W, OUT_H, FPS) as vw, ThreadPoolExecutor(max_workers=3) as pool:
        for i in range(n):
            e = ease(i / (n - 1))
            wx = p["x0"] + (p["x1"] - p["x0"]) * e - p["win_w"] / 2
            wy = p["cy"] - p["win_h"] / 2
            m = 1.6 * p["par"] + 8
            xs0, xs1 = max(0, int(wx - m)), min(pw, int(wx + p["win_w"] + m) + 1)
            ys0, ys1 = max(0, int(wy - m)), min(ph, int(wy + p["win_h"] + m) + 1)
            block = (slice(ys0, ys1), slice(xs0, xs1))
            frame, hole, zbuf = splat_frame(rgb_t[block], w_t[block], d_t[block],
                                            xs0, ys0, cx0, cy0, tz * e, sx * e, wx, wy, p["scale"],
                                            bg=tuple(x[block] for x in bg_t) if bg_t is not None else None)
            holes.append(float(hole.mean()))
            pending.append(pool.submit(fill_holes, frame, hole, zbuf))
            while len(pending) > 4 or (i == n - 1 and pending):
                vw.write(pending.popleft().result())
    render_s = time.perf_counter() - t0
    return {"move": move, "parallax_px": parallax_out, "tz": round(tz, 4), "focal_x_baseline_px": round(sx, 2),
            "window_travel_out_px": round((p["x1"] - p["x0"]) * p["scale"], 1),
            "disoccluded_mean": round(float(np.mean(holes)), 5), "disoccluded_max": round(float(np.max(holes)), 5),
            "frames": n, "render_s": round(render_s, 2)}


def splat_frame(rgb, w, d, xs0, ys0, cx0, cy0, tz, sx, wx, wy, scale, bg=None):
    """Forward-project the source block and splat it bilinearly with softmax depth weights. `bg` is an
    optional background layer (rgb, inverse depth, disparity, mask) for the same block; its masked pixels
    are splatted too, at their farther depth, so the photo wins wherever both land."""
    dev = rgb.device
    h, wd = w.shape
    v, u = torch.meshgrid(torch.arange(h, device=dev, dtype=torch.float32) + ys0,
                          torch.arange(wd, device=dev, dtype=torch.float32) + xs0, indexing="ij")
    layers = [(u, v, rgb, w, d)]
    if bg is not None:
        b_rgb, b_w, b_d, b_m = bg
        layers.append((u[b_m], v[b_m], b_rgb[b_m], b_w[b_m], b_d[b_m]))
    oxs, oys, payloads = [], [], []
    for lu, lv, lrgb, lw, ld in layers:
        den = 1 - tz * lw
        up = cx0 + ((lu - cx0) - sx * lw) / den
        vp = cy0 + (lv - cy0) / den
        oxs.append(((up - wx) * scale).reshape(-1))
        oys.append(((vp - wy) * scale).reshape(-1))
        lzw = torch.exp(SOFTMAX_BETA * (ld.reshape(-1) - 1.0))
        # per source pixel: colour, 1 and inverse depth, all to be weighted, then the plain bilinear weight
        payloads.append(torch.cat([lrgb.reshape(-1, 3), torch.ones_like(lzw)[:, None], lw.reshape(-1, 1)], 1) * lzw[:, None])
    ox, oy, payload = torch.cat(oxs), torch.cat(oys), torch.cat(payloads)
    x0, y0 = torch.floor(ox), torch.floor(oy)
    fx, fy = ox - x0, oy - y0
    x0, y0 = x0.long(), y0.long()
    npx = OUT_W * OUT_H
    acc = torch.zeros(npx + 1, 6, device=dev)  # the last row collects everything that lands off-frame
    for dx, dy, bw in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        xi, yi = x0 + dx, y0 + dy
        ok = (xi >= 0) & (xi < OUT_W) & (yi >= 0) & (yi < OUT_H)
        idx = torch.where(ok, yi * OUT_W + xi, npx)
        acc.index_add_(0, idx, torch.cat([payload * bw[:, None], bw[:, None]], 1))
    acc = acc[:npx]
    wsum = acc[:, 3].clamp_min(1e-30)
    out = (acc[:, :3] / wsum[:, None]).clamp(0, 255)
    hole = acc[:, 5] < HOLE_COVERAGE
    z = acc[:, 4] / wsum
    return (out.reshape(OUT_H, OUT_W, 3).round().byte().cpu().numpy(), hole.reshape(OUT_H, OUT_W).cpu().numpy(),
            z.reshape(OUT_H, OUT_W).cpu().numpy())


def fill_holes(frame: np.ndarray, hole: np.ndarray, zbuf: np.ndarray) -> np.ndarray:
    """Inpaint holes from their background side: the nearer pixels bordering a hole (the object that
    uncovered it) are masked too, so the fill draws only on the farther surface."""
    if not hole.any():
        return frame
    hm = hole.astype(np.uint8)
    band = cv2.dilate(hm, np.ones((9, 9), np.uint8)).astype(bool) & ~hole
    z = np.where(hole, 1e9, zbuf).astype(np.float32)
    local_far = cv2.erode(z, np.ones((21, 21), np.uint8))  # smallest inverse depth = farthest nearby
    fg = band & (zbuf > local_far + 0.08)
    mask = (hole | fg).astype(np.uint8)
    filled = cv2.inpaint(frame, mask, 5, cv2.INPAINT_TELEA)
    out = frame.copy()
    out[hole] = filled[hole]
    return out


def work_image(photo: np.ndarray) -> np.ndarray:
    """The photo scaled so the 9:16 window (full height less a margin) is SRC_DENSITY x the output."""
    ph, pw = photo.shape[:2]
    win_h = min(ph * 0.94, pw * 0.94 * OUT_H / OUT_W)
    s = SRC_DENSITY * OUT_H / win_h
    return cv2.resize(photo, (round(pw * s), round(ph * s)), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)


@lru_cache(maxsize=2)
def _depth_model(device: str) -> Depth:
    return Depth(device)


def render_depth_clip(photo: np.ndarray, move: str, parallax_px: float, path: Path, seconds: float = 6.0,
                      fill: str = "telea") -> dict:
    """Render one depth-parallax clip of `photo` (RGB uint8, full size) to `path`; returns its stats.
    fill="lama" adds the LaMa background layer (background_layer) under the photo."""
    device = str(get_device())
    work = work_image(photo)
    with gpu_lock():
        t = time.perf_counter()
        disp = _depth_model(device)(photo, (work.shape[1], work.shape[0]))
        depth_s = time.perf_counter() - t
        disp = sharpen_depth(disp, 2 * math.ceil(work.shape[0] / 518) + 1)
        bg, bg_s = None, 0.0
        if fill == "lama":
            t = time.perf_counter()
            bg = background_layer(work, disp, parallax_px * SRC_DENSITY)
            bg_s = time.perf_counter() - t
        stats = render(work, disp, move, parallax_px, Path(path), device, seconds, bg=bg)
    stats.update({"fill": fill, "background_s": round(bg_s, 2),
                  "background_frac": round(float(bg[2].mean()), 4) if bg is not None else 0.0})
    stats.update({"depth_s": round(depth_s, 2), "device": device, "work_size": [work.shape[1], work.shape[0]]})
    return stats
