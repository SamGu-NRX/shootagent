"""A camera move from one photo that invents nothing: depth parallax.

Depth Anything V2 Small (Apache-2.0) estimates relative depth; every source pixel is then reprojected
for a slowly moving camera and splatted into the 1080x1920 frame, nearer pixels winning where several
land together. Where the move uncovers background the photo never saw, the frame has a hole; holes are
counted (the disoccluded fraction) and filled from the background side of the hole only, so a
foreground object never smears into what it uncovered.

Moves (5 s, 30 fps, eased):
- dolly: the camera moves forward;
- truck: the camera moves sideways while the 9:16 window travels the same way across the photo.
Landscape photos are shown through a 9:16 window; portrait photos fill the frame.

The size of a move is set as parallax, not distance: the largest shift, in output pixels, between the
nearest and farthest points of the photo by the end of the move (--parallax). Depth is only known up
to scale, so this is the quantity that decides how much background gets uncovered.

    python experiments/film/parallax.py photo.jpg [photo2.jpg ...] --move dolly truck --out experiments/film/out/parallax [--device cpu]
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

from clipio import VideoWriter, load_rgb

MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
OUT_W, OUT_H = 1080, 1920
FPS = 30
SECONDS = 5.0
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


def render(photo: np.ndarray, disp: np.ndarray, move: str, parallax_out: float, path: Path, device: str) -> dict:
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
    n = round(SECONDS * FPS)
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
            frame, hole, zbuf = splat_frame(rgb_t[ys0:ys1, xs0:xs1], w_t[ys0:ys1, xs0:xs1], d_t[ys0:ys1, xs0:xs1],
                                            xs0, ys0, cx0, cy0, tz * e, sx * e, wx, wy, p["scale"])
            holes.append(float(hole.mean()))
            pending.append(pool.submit(fill_holes, frame, hole, zbuf))
            while len(pending) > 4 or (i == n - 1 and pending):
                vw.write(pending.popleft().result())
    render_s = time.perf_counter() - t0
    return {"move": move, "parallax_px": parallax_out, "tz": round(tz, 4), "focal_x_baseline_px": round(sx, 2),
            "window_travel_out_px": round((p["x1"] - p["x0"]) * p["scale"], 1),
            "disoccluded_mean": round(float(np.mean(holes)), 5), "disoccluded_max": round(float(np.max(holes)), 5),
            "frames": n, "render_s": round(render_s, 2)}


def splat_frame(rgb, w, d, xs0, ys0, cx0, cy0, tz, sx, wx, wy, scale):
    """Forward-project the source block and splat it bilinearly with softmax depth weights."""
    dev = rgb.device
    h, wd = w.shape
    v, u = torch.meshgrid(torch.arange(h, device=dev, dtype=torch.float32) + ys0,
                          torch.arange(wd, device=dev, dtype=torch.float32) + xs0, indexing="ij")
    den = 1 - tz * w
    up = cx0 + ((u - cx0) - sx * w) / den
    vp = cy0 + (v - cy0) / den
    ox = ((up - wx) * scale).reshape(-1)
    oy = ((vp - wy) * scale).reshape(-1)
    zw = torch.exp(SOFTMAX_BETA * (d.reshape(-1) - 1.0))
    # per source pixel: colour, 1 and inverse depth, all to be weighted, then the plain bilinear weight
    payload = torch.cat([rgb.reshape(-1, 3), torch.ones_like(zw)[:, None], w.reshape(-1, 1)], 1) * zw[:, None]
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photos", nargs="+", type=Path)
    ap.add_argument("--move", nargs="+", default=["dolly", "truck"], choices=["dolly", "truck"])
    ap.add_argument("--parallax", type=float, default=40.0, help="near/far shift by the end of the move, output px")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out" / "parallax")
    ap.add_argument("--tag", default="", help="suffix for output names, e.g. _cpu")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    t = time.perf_counter()
    depth = Depth(args.device)
    load_s = time.perf_counter() - t
    env = {"device": args.device, "torch": torch.__version__, "threads": torch.get_num_threads(), "cpu": platform.processor(),
           "gpu": torch.cuda.get_device_name(0) if args.device == "cuda" else None, "model_load_s": round(load_s, 2)}
    print(json.dumps(env), flush=True)
    for photo_path in args.photos:
        photo = load_rgb(photo_path)
        work = work_image(photo)
        if args.device == "cuda":
            torch.cuda.synchronize()
        t = time.perf_counter()
        disp = depth(photo, (work.shape[1], work.shape[0]))
        if args.device == "cuda":
            torch.cuda.synchronize()
        depth_s = time.perf_counter() - t
        disp = sharpen_depth(disp, 2 * math.ceil(work.shape[0] / 518) + 1)
        stem = photo_path.parent.name[-4:] if photo_path.parent.name.startswith("defect_train_") else photo_path.stem
        cv2.imwrite(str(args.out / f"{stem}_depth{args.tag}.jpg"), (disp * 255).astype(np.uint8))
        for move in args.move:
            clip = args.out / f"{stem}_{move}{args.tag}.mp4"
            stats = render(work, disp, move, args.parallax, clip, args.device)
            stats.update({"photo": str(photo_path), "clip": str(clip.resolve()), "depth_s": round(depth_s, 2),
                          "work_size": [work.shape[1], work.shape[0]], **env})
            (args.out / f"{stem}_{move}{args.tag}.json").write_text(json.dumps(stats, indent=2))
            print(json.dumps({k: stats[k] for k in ("clip", "disoccluded_mean", "disoccluded_max", "depth_s", "render_s", "tz",
                                                    "focal_x_baseline_px", "window_travel_out_px")}), flush=True)


if __name__ == "__main__":
    main()
