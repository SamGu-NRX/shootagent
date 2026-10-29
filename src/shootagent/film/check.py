"""Check a generated clip against the photo(s) it was made from: pass or fail, with reasons.

Ported from experiments/film/check_clip.py on branch explore/film-lab, judge v2 (commit 7abe82e). The signal
code, LIMITS and judge prompts are unchanged. Three things differ from the experiment:
- the interface: `check_clip` takes the clip path and the photos as arrays and returns a `ClipCheck`, and
  the judge is a parameter (`InventoryJudge`, caching each photo's inventory under a folder it is given);
- a "missing" finding fails a clip only at high confidence. The experiment's four false alarms on good
  clips were all medium-confidence "missing" findings, and no ground-truth catch on the four Grok clips was
  a medium-confidence "missing" one (its commit message; the rule was found there but not applied);
- the judge returns its token use and cost per call (inventory and check), so the trace shows what each
  check cost.

An image-to-video model asked to move the camera through a room sometimes invents a door or window,
drops a fixture or bends a window frame. Signals, all measured on frames sampled every 0.25 s and resized
to 960 px on the long edge:
- endpoint: SIFT homography from the photo onto the first (or last) frame; inlier count, then SSIM of
  the two edge maps, overall and per tile. A first frame that is not the photo fails here.
- motion: corners from frame 0 tracked through the samples (Lucas-Kanade, re-anchored on frame 0). Any
  rigid camera, whatever the depth, moves them consistently with one fundamental matrix; the per-cell
  median of their distance from it (Sampson) is the motion no rigid camera explains: a morph.
- map: a homography fitted to those tracks plus DIS optical flow for the remainder gives, for every
  pixel of frame k, where it came from in frame 0 (see Chain).
- appearance: frame 0 pulled through that map predicts frame k. Where the local structure (the
  contrast-structure term of SSIM, which ignores brightness shifts) disagrees, something appeared
  that frame 0 does not contain. Flow alone misses this: a door that fades in does not move.
- lines: long straight segments (LSD) stay straight under any rigid camera, whatever the depth. Points
  along each frame-0 segment are tracked; their deviation from a straight line is the bend. Long
  segments in later frames with no edge under them in frame 0 are new lines; frame-0 segments with no
  edge left under their tracked points are vanished lines. Points whose map inversion does not converge
  are dropped.
- judge: Claude first lists the checkable items in each photo (doors, openings and what lies beyond
  them, windows and the view through each, ceiling lights, wall plates, built-ins, furniture, decor;
  cached per photo), then accounts for every visible item in 8 frames at the clip's own resolution
  ("census"), reporting anything new, missing or changed.
Motion, appearance and line findings count only when they persist across two consecutive samples
(0.5 s). They need frame-0 content to stay in view: for a clip with an end photo whose last frame shares
less than half its area with the start photo (a room-to-room move), they are reported as not checkable,
and only the endpoint checks and the judge apply.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy.ndimage import median_filter
from skimage.metrics import structural_similarity

WORK_LONG = 960  # analysis resolution (long edge, px); every px figure in the output is at this scale
STEP_S = 0.25
CELL = 64  # motion residual grid cell, px
EDGE_MARGIN = 8  # px; line points closer than this to the frame border are not used
SHEET_FRAMES = 6
JUDGE_FRAMES = 8  # sent to the judge one by one at the clip's own resolution, so a switch plate is visible


# ---- clip I/O (from experiments/film/clipio.py) -----------------------------------------------------------

@dataclass
class Samples:
    times: list[float]  # seconds
    frames: list[np.ndarray]  # RGB uint8 at native resolution
    fps: float
    n_frames: int  # frames decoded


def read_samples(path: str | Path, step_s: float) -> Samples:
    """Frames at t = 0, step, 2*step, ... plus the last frame. Decodes sequentially and keeps only those,
    so a long clip never sits in memory."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video {path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        raise ValueError(f"{path} reports no frame rate")
    every = max(1, round(step_s * fps))
    times, frames, last, i = [], [], None, 0
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        if i % every == 0:
            times.append(i / fps)
            frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        last = bgr
        i += 1
    cap.release()
    if i == 0:
        raise ValueError(f"{path} decoded no frames")
    if (i - 1) % every != 0:  # always end on the true last frame
        times.append((i - 1) / fps)
        frames.append(cv2.cvtColor(last, cv2.COLOR_BGR2RGB))
    return Samples(times, frames, fps, i)


def _fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """img scaled to fit (w, h), centred on a dark cell."""
    s = min(w / img.shape[1], h / img.shape[0])
    nw, nh = max(1, round(img.shape[1] * s)), max(1, round(img.shape[0] * s))
    small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    cell = np.full((h, w, 3), 24, np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    cell[y0:y0 + nh, x0:x0 + nw] = small
    return cell


def contact_sheet(tiles: list[tuple[str, np.ndarray]], long_edge: int = 1568) -> np.ndarray:
    """Labelled tiles in a grid no larger than `long_edge` (Claude's vision input size). Cells take the
    aspect of the last tile (a video frame); 3 columns for landscape frames, 4 for portrait."""
    fh, fw = tiles[-1][1].shape[:2]
    cols = 3 if fw >= fh else 4
    rows = -(-len(tiles) // cols)
    gap = 6
    cell_w = (long_edge - gap * (cols - 1)) // cols
    cell_h = round(cell_w * fh / fw)
    if rows * cell_h + gap * (rows - 1) > long_edge:
        cell_h = (long_edge - gap * (rows - 1)) // rows
        cell_w = round(cell_h * fw / fh)
    sheet = np.full((rows * cell_h + gap * (rows - 1), cols * cell_w + gap * (cols - 1), 3), 255, np.uint8)
    for i, (label, img) in enumerate(tiles):
        r, c = divmod(i, cols)
        y, x = r * (cell_h + gap), c * (cell_w + gap)
        cell = _fit(img, cell_w, cell_h)
        scale = max(0.5, cell_w / 700)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        cv2.rectangle(cell, (0, 0), (tw + 12, th + 14), (0, 0, 0), -1)
        cv2.putText(cell, label, (6, th + 6), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)
        sheet[y:y + cell_h, x:x + cell_w] = cell
    return sheet


def pick_evenly(n_available: int, n: int) -> list[int]:
    """n indices spread from the first to the last item inclusive."""
    if n_available <= n:
        return list(range(n_available))
    return [round(i * (n_available - 1) / (n - 1)) for i in range(n)]


@dataclass(frozen=True)
class Limits:
    # Values set on the synthetic clips of photos 0054 and 0055 only (12 clips), before the other photos
    # and the parallax clips were checked, and not changed since; never checked on a real generated clip.
    # Good-clip maxima there: first frame 336 inliers, edge SSIM 0.96, worst tile 0.91; motion 0.99 px;
    # appearance 0; bend 0.31 px; new and vanished lines 0. The signal code (not these values) changed
    # after the held-out and parallax clips exposed failures; see the commit message.
    endpoint_min_inliers: int = 30
    endpoint_min_edge_ssim: float = 0.5
    endpoint_min_tile_ssim: float = 0.4
    motion_max_px: float = 2.0  # per-cell median track residual
    appear_max_frac: float = 0.0008  # largest blob of new structure, fraction of the frame (~23x23 px at 960x540)
    line_max_bend_px: float = 1.5
    line_max_new: float = 0.05  # summed length of new or vanished long lines, in frame diagonals
    out_of_photo_max: float = 0.2  # fraction of a frame showing area outside the start photo, when no end photo
    # With an end photo, motion, appearance and lines need the start photo to stay in view: below this
    # share of the last frame covered by the start photo (homography from feature matches), they are
    # reported as not checkable. Room-to-room clips T1 and T2 lost every frame-0 track by mid-clip.
    min_shared_for_tracking: float = 0.5


LIMITS = Limits()


# ---- helpers ----------------------------------------------------------------------------------------------

def to_work(rgb: np.ndarray) -> np.ndarray:
    s = WORK_LONG / max(rgb.shape[:2])
    return cv2.resize(rgb, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)


def gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def edge_map(g: np.ndarray) -> np.ndarray:
    b = cv2.GaussianBlur(g.astype(np.float32), (0, 0), 1.0)
    return cv2.magnitude(cv2.Sobel(b, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(b, cv2.CV_32F, 0, 1, ksize=3))


def sample(img: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Bilinear samples of img (H, W[, C]) at float points (N, 2) as x, y."""
    m = pts.astype(np.float32).reshape(1, -1, 2)
    out = cv2.remap(img, m[..., 0], m[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return out.reshape(len(pts), *img.shape[2:])


def box_frac(x0, y0, x1, y1, w, h) -> list[float]:
    return [round(float(x0) / w, 3), round(float(y0) / h, 3), round(float(x1) / w, 3), round(float(y1) / h, 3)]


def persistent_peak(series: list[float]) -> tuple[float, int]:
    """Largest value held over two consecutive samples, and the index where it starts."""
    if len(series) < 2:
        return 0.0, 0
    pair = [min(a, b) for a, b in zip(series, series[1:])]
    i = int(np.argmax(pair))
    return float(pair[i]), i


# ---- endpoint ---------------------------------------------------------------------------------------------

def register(photo: np.ndarray, frame_w: np.ndarray) -> dict:
    """Homography from `photo` (full size RGB) onto `frame_w` (work-size RGB) and edge agreement."""
    ps = 1600 / max(photo.shape[:2])
    small = cv2.resize(photo, None, fx=ps, fy=ps, interpolation=cv2.INTER_AREA)
    sift = cv2.SIFT_create(nfeatures=6000)
    k1, d1 = sift.detectAndCompute(gray(small), None)
    k2, d2 = sift.detectAndCompute(gray(frame_w), None)
    out = {"matches": 0, "inliers": 0, "inlier_ratio": 0.0, "edge_ssim": 0.0, "min_tile_ssim": 0.0, "worst_tile": None, "H": None}
    if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
        return out
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d1, d2, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    out["matches"] = len(good)
    if len(good) < 8:
        return out
    src = np.float32([k1[m.queryIdx].pt for m in good])
    dst = np.float32([k2[m.trainIdx].pt for m in good])
    H, mask = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 3.0)
    if H is None:
        return out
    out["inliers"] = int(mask.sum())
    out["inlier_ratio"] = round(float(mask.mean()), 3)
    h, w = frame_w.shape[:2]
    warped = cv2.warpPerspective(small, H, (w, h), flags=cv2.INTER_AREA)
    valid = cv2.warpPerspective(np.ones(small.shape[:2], np.uint8), H, (w, h), flags=cv2.INTER_NEAREST)
    valid = cv2.erode(valid, np.ones((9, 9), np.uint8)).astype(bool)
    if valid.mean() < 0.2:
        return out
    ea, eb = edge_map(gray(frame_w)), edge_map(gray(warped))
    ea /= max(np.percentile(ea[valid], 99), 1e-6)
    eb /= max(np.percentile(eb[valid], 99), 1e-6)
    _, smap = structural_similarity(np.clip(ea, 0, 1), np.clip(eb, 0, 1), data_range=1.0, gaussian_weights=True, sigma=1.5, full=True)
    out["edge_ssim"] = round(float(smap[valid].mean()), 3)
    nx, ny = (6, 4) if w >= h else (4, 6)
    worst, worst_box = 1.0, None
    for j in range(ny):
        for i in range(nx):
            ys, xs = slice(j * h // ny, (j + 1) * h // ny), slice(i * w // nx, (i + 1) * w // nx)
            v = valid[ys, xs]
            if v.mean() < 0.5:
                continue
            m = float(smap[ys, xs][v].mean())
            if m < worst:
                worst, worst_box = m, box_frac(xs.start, ys.start, xs.stop, ys.stop, w, h)
    out["min_tile_ssim"], out["worst_tile"] = round(worst, 3), worst_box
    out["coverage"] = round(float(valid.mean()), 3)
    # photo (full resolution) -> frame (work) for the out-of-photo measure
    out["H"] = (H @ np.diag([ps, ps, 1.0])).tolist()
    return out


def endpoint_ok(r: dict) -> bool:
    return (r["inliers"] >= LIMITS.endpoint_min_inliers and r["edge_ssim"] >= LIMITS.endpoint_min_edge_ssim
            and r["min_tile_ssim"] >= LIMITS.endpoint_min_tile_ssim)


# ---- flow chain -------------------------------------------------------------------------------------------

LK = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01))
FB_MAX = 0.75  # px, forward-backward disagreement above which a track is dropped


def lk(a: np.ndarray, b: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pyramidal Lucas-Kanade from image a to b with a forward-backward check; returns (points in b, ok)."""
    p = pts.reshape(-1, 1, 2).astype(np.float32)
    q, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p, None, **LK)
    back, st2, _ = cv2.calcOpticalFlowPyrLK(b, a, q, None, **LK)
    ok = (st.ravel() == 1) & (st2.ravel() == 1) & (np.linalg.norm((back - p).reshape(-1, 2), axis=1) < FB_MAX)
    return q.reshape(-1, 2), ok


def lk_seeded(a: np.ndarray, b: np.ndarray, pts: np.ndarray, guess: np.ndarray, tol: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    """Lucas-Kanade from a to b started at `guess`; kept only where it lands within `tol` px of the guess.
    (A backward check seeded the same way would start at the answer and always agree.)"""
    p = pts.reshape(-1, 1, 2).astype(np.float32)
    q = guess.reshape(-1, 1, 2).astype(np.float32).copy()
    q, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p, q, flags=cv2.OPTFLOW_USE_INITIAL_FLOW, **LK)
    q = q.reshape(-1, 2)
    return q, (st.ravel() == 1) & (np.linalg.norm(q - guess.reshape(-1, 2), axis=1) < tol)


class Chain:
    """Correspondences from each sampled frame k back to frame 0.

    Corners found in frame 0 are tracked frame to frame with Lucas-Kanade and then re-tracked directly
    from frame 0 (seeded with the chained position) so drift does not build up. A homography fitted to
    those tracks (or chained frame-to-frame homographies when too few tracks survive) carries frame k
    onto frame 0; DIS flow between frame k and that warped prediction adds the small rest (parallax,
    morphs). Chaining dense flow alone drifted by 10 px or more in flat walls and sky by the last frame
    of a 6 s clip, which made every check fire on good clips."""

    def __init__(self, grays: list[np.ndarray]):
        self.g = grays
        h, w = grays[0].shape
        self.shape = (h, w)
        self.ident = np.dstack(np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32)))
        dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        p0 = cv2.goodFeaturesToTrack(grays[0], maxCorners=3000, qualityLevel=0.01, minDistance=8)
        self.p0 = np.zeros((0, 2), np.float32) if p0 is None else p0.reshape(-1, 2)
        cur, alive = self.p0.copy(), np.ones(len(self.p0), bool)
        self.tracks = [(self.p0.copy(), alive.copy())]  # per frame: (positions, alive)
        self.H = [np.eye(3)]  # frame k -> frame 0
        self.maps = [self.ident.copy()]
        chained = np.eye(3)
        for k in range(1, len(grays)):
            a, b = grays[k - 1], grays[k]
            # frame-to-frame homography from fresh corners, the fallback when long tracks thin out
            q = cv2.goodFeaturesToTrack(a, maxCorners=1500, qualityLevel=0.01, minDistance=10)
            if q is not None and len(q) >= 8:
                q = q.reshape(-1, 2)
                qk, ok = lk(a, b, q)
                if ok.sum() >= 8:
                    hk, _ = cv2.findHomography(qk[ok], q[ok], cv2.USAC_MAGSAC, 1.0)
                    if hk is not None:
                        chained = chained @ hk
            if alive.any():
                nxt, ok = lk(a, b, cur[alive])
                cur[alive] = nxt
                alive[np.flatnonzero(alive)[~ok]] = False
            if alive.any():
                idx = np.flatnonzero(alive)
                direct, ok = lk_seeded(grays[0], b, self.p0[idx], cur[idx])
                cur[idx[ok]] = direct[ok]
            self.tracks.append((cur.copy(), alive.copy()))
            Hk = None
            if alive.sum() >= 40:
                Hk, _ = cv2.findHomography(cur[alive], self.p0[alive], cv2.USAC_MAGSAC, 1.0)
            if Hk is None:
                Hk = chained
            self.H.append(Hk)
            base = cv2.perspectiveTransform(self.ident.reshape(-1, 1, 2), Hk).reshape(h, w, 2)
            pred = cv2.remap(grays[0], base, None, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            r = dis.calc(b, pred, None)  # b(x) ~ pred(x + r(x))
            self.maps.append(cv2.remap(base, self.ident + r, None, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))

    def forward(self, k: int, pts0: np.ndarray, iters: int = 4) -> tuple[np.ndarray, np.ndarray]:
        """Frame-k positions of frame-0 points, by Newton steps on the map from the homography guess, and
        whether each converged (maps back to within 1 px). Unconverged points are where the map has no
        answer, such as content that left the frame; using them produced bends of hundreds of px with
        boxes far outside the frame on real generated clips."""
        x = cv2.perspectiveTransform(pts0.reshape(-1, 1, 2).astype(np.float64), np.linalg.inv(self.H[k])).reshape(-1, 2).astype(np.float32)
        m = self.maps[k]
        for _ in range(iters):
            f = sample(m, x)
            jx = (sample(m, x + [1, 0]) - sample(m, x - [1, 0])) / 2
            jy = (sample(m, x + [0, 1]) - sample(m, x - [0, 1])) / 2
            J = np.stack([jx, jy], -1)  # J[n, i, j] = d map_i / d x_j
            det = J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0]
            det = np.where(np.abs(det) < 1e-6, 1e-6, det)
            e = f - pts0
            dx = (J[:, 1, 1] * e[:, 0] - J[:, 0, 1] * e[:, 1]) / det
            dy = (-J[:, 1, 0] * e[:, 0] + J[:, 0, 0] * e[:, 1]) / det
            x = x - np.stack([dx, dy], 1).astype(np.float32)
        ok = np.isfinite(x).all(1) & (np.linalg.norm(sample(m, x) - pts0, axis=1) < 1.0)
        return x, ok

    def inside(self, k: int, margin: float = 0.0) -> np.ndarray:
        h, w = self.shape
        m = self.maps[k]
        return (m[..., 0] >= margin) & (m[..., 0] <= w - 1 - margin) & (m[..., 1] >= margin) & (m[..., 1] <= h - 1 - margin)


# ---- motion -----------------------------------------------------------------------------------------------

def motion_step(chain: Chain, k: int) -> dict:
    """Residual of the corner tracks (frame k -> frame 0) under the best rigid-camera model."""
    h, w = chain.shape
    cur, alive = chain.tracks[k]
    pts, src0 = cur[alive], chain.p0[alive]
    empty = {"score": 0.0, "model": None, "cell": None, "H": chain.H[k], "h_inlier_frac": None, "tracks": int(alive.sum())}
    if len(pts) < 40:
        return empty
    H = chain.H[k]
    proj = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), H).reshape(-1, 2)
    res = np.linalg.norm(proj - src0, axis=1)
    h_frac = float((res < 1.5).mean())
    model = "H"
    # The fundamental matrix (any rigid camera, any depth) judges the tracks; the homography only when F
    # cannot be estimated. Choosing H whenever it explained most tracks (tried at 90% and 97%) read a near
    # chair's real parallax in a dolly-in, 3% of the tracks, as a 2 px morph. The cost: on a flat scene F
    # is underdetermined and can absorb a morph along one direction, which the line check then has to catch.
    F, _ = cv2.findFundamentalMat(pts, src0, cv2.USAC_MAGSAC, 1.0, 0.999)
    if F is not None and F.shape == (3, 3):
        a = np.c_[pts, np.ones(len(pts))]
        b = np.c_[src0, np.ones(len(pts))]
        Fa, Ftb = a @ F.T, b @ F
        num = np.einsum("ij,ij->i", b, Fa) ** 2
        res = np.sqrt(num / (Fa[:, 0] ** 2 + Fa[:, 1] ** 2 + Ftb[:, 0] ** 2 + Ftb[:, 1] ** 2 + 1e-12))  # Sampson
        model = "F"
    cx, cy = (pts[:, 0] // CELL).astype(int), (pts[:, 1] // CELL).astype(int)
    best, best_cell = 0.0, None
    for key in set(zip(cx.tolist(), cy.tolist())):
        sel = (cx == key[0]) & (cy == key[1])
        if sel.sum() < 6:
            continue
        med = float(np.median(res[sel]))
        if med > best:
            best, best_cell = med, box_frac(key[0] * CELL, key[1] * CELL, min(w, (key[0] + 1) * CELL), min(h, (key[1] + 1) * CELL), w, h)
    return {"score": best, "model": model, "cell": best_cell, "H": H, "h_inlier_frac": round(h_frac, 3), "tracks": len(pts)}


# ---- appearance -------------------------------------------------------------------------------------------

def appearance_step(chain: Chain, k: int) -> dict:
    """Largest connected region of frame k whose local structure frame 0 does not predict."""
    C2 = (0.03 * 255) ** 2
    a = cv2.GaussianBlur(chain.g[k].astype(np.float32), (0, 0), 1.0)
    p = cv2.remap(cv2.GaussianBlur(chain.g[0].astype(np.float32), (0, 0), 1.0), chain.maps[k], None, cv2.INTER_LINEAR)

    def G(x):
        return cv2.GaussianBlur(x, (0, 0), 2.5)

    ma, mp = G(a), G(p)
    saa, spp, sap = G(a * a) - ma * ma, G(p * p) - mp * mp, G(a * p) - ma * mp
    cs = (2 * sap + C2) / (np.maximum(saa, 0) + np.maximum(spp, 0) + C2)
    inside = cv2.erode(chain.inside(k).astype(np.uint8), np.ones((13, 13), np.uint8)).astype(bool)
    low = ((cs < 0.3) & inside).astype(np.uint8)
    low = cv2.morphologyEx(low, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    low = cv2.morphologyEx(low, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(low)
    h, w = chain.shape
    if n <= 1:
        return {"score": 0.0, "box": None}
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh, area = stats[i]
    return {"score": float(area) / (h * w), "box": box_frac(x, y, x + bw, y + bh, w, h)}


# ---- lines ------------------------------------------------------------------------------------------------

def merge_collinear(segs: np.ndarray, angle_deg: float = 1.5, offset_px: float = 2.0, gap_px: float = 40.0) -> np.ndarray:
    """Join segments that lie on one line. LSD cuts an edge wherever something crosses it (a window's
    meeting rail across its mullion, a chandelier in front), and a short piece of a bowed edge looks
    straight, so bends are measured on the joined line."""
    n = len(segs)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    d = (segs[:, 2:] - segs[:, :2]).astype(np.float64)
    length = np.hypot(d[:, 0], d[:, 1])
    u = d / length[:, None]
    nrm = np.stack([-u[:, 1], u[:, 0]], 1)
    a, b = segs[:, :2].astype(np.float64), segs[:, 2:].astype(np.float64)
    # pairwise, in segment i's frame: angle, perpendicular offset of j's ends, gap along i's direction
    parallel = np.abs(u[:, None, 0] * u[None, :, 1] - u[:, None, 1] * u[None, :, 0]) <= np.sin(np.radians(angle_deg))
    ra, rb = a[None] - a[:, None], b[None] - a[:, None]  # [i, j] = j's ends relative to i's start
    off = np.maximum(np.abs(np.einsum("ijk,ik->ij", ra, nrm)), np.abs(np.einsum("ijk,ik->ij", rb, nrm)))
    ta, tb = np.einsum("ijk,ik->ij", ra, u), np.einsum("ijk,ik->ij", rb, u)
    gap = np.maximum(0.0, np.minimum(ta, tb)) - np.minimum(length[:, None], np.maximum(ta, tb))
    near = parallel & (off <= offset_px) & (gap <= gap_px)
    for i, j in zip(*np.nonzero(np.triu(near | near.T, 1))):
        parent[root(j)] = root(i)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(i)
    out = []
    for members in groups.values():
        pts = np.vstack([segs[members, :2], segs[members, 2:]])
        c = pts.mean(0)
        _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
        t = (pts - c) @ vt[0]
        out.append([*(c + t.min() * vt[0]), *(c + t.max() * vt[0])])
    return np.array(out, np.float32).reshape(-1, 4)


def detect_segments(g: np.ndarray, min_len: float, limit: int = 80) -> np.ndarray:
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    lines = lsd.detect(g)[0]
    if lines is None:
        return np.zeros((0, 4), np.float32)
    lines = lines.reshape(-1, 4)
    length = np.hypot(lines[:, 2] - lines[:, 0], lines[:, 3] - lines[:, 1])
    lines = merge_collinear(lines[length >= min_len / 4])
    length = np.hypot(lines[:, 2] - lines[:, 0], lines[:, 3] - lines[:, 1])
    lines = lines[length >= min_len]
    order = np.argsort(-np.hypot(lines[:, 2] - lines[:, 0], lines[:, 3] - lines[:, 1]))
    return lines[order[:limit]]


def seg_points(segs: np.ndarray, n: int = 24) -> np.ndarray:
    s = np.linspace(0.05, 0.95, n)[None, :, None]
    p0, p1 = segs[:, None, :2], segs[:, None, 2:]
    return (p0 + s * (p1 - p0)).astype(np.float32)  # (S, n, 2)


def bend(points: np.ndarray) -> float:
    """Largest distance of the points (in order along the line) from their total-least-squares line,
    after a 5-point running median: a bend moves a run of neighbouring points, while one or two points
    snagged by something crossing the edge do not count."""
    if len(points) < 5:
        return 0.0
    c = points - points.mean(0)
    _, _, vt = np.linalg.svd(c, full_matrices=False)
    return float(np.abs(median_filter(c @ vt[1], size=5, mode="nearest")).max())


def edge_supported(e_ref_dilated: np.ndarray, pts_ref: np.ndarray, e_own: np.ndarray, pts_own: np.ndarray) -> float:
    """Fraction of points whose reference-frame edge strength (max over 5x5) is at least 35% of their own."""
    return float((sample(e_ref_dilated, pts_ref) >= 0.35 * sample(e_own, pts_own)).mean())


def line_signals(chain: Chain) -> dict:
    h, w = chain.shape
    diag = float(np.hypot(h, w))
    min_len = 0.06 * diag
    edges = [edge_map(g) for g in chain.g]
    dil = [cv2.dilate(e, np.ones((5, 5), np.uint8)) for e in edges]
    segs0 = detect_segments(chain.g[0], min_len)
    pts = seg_points(segs0)
    len0 = np.hypot(segs0[:, 2] - segs0[:, 0], segs0[:, 3] - segs0[:, 1]) / diag
    # Joined segments span gaps where something crossed the edge; track only points on the edge itself:
    # strong gradient pointing across the line (a chandelier bead in front of a door jamb has edges too).
    if len(pts):
        b0 = cv2.GaussianBlur(chain.g[0].astype(np.float32), (0, 0), 1.0)
        grad = np.dstack([cv2.Sobel(b0, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(b0, cv2.CV_32F, 0, 1, ksize=3)])
        gp = sample(grad, pts.reshape(-1, 2)).reshape(*pts.shape[:2], 2)
        mag = np.linalg.norm(gp, axis=-1)
        d = segs0[:, 2:] - segs0[:, :2]
        normal = np.stack([-d[:, 1], d[:, 0]], 1) / np.linalg.norm(d, axis=1, keepdims=True)
        across = np.abs(np.einsum("snk,sk->sn", gp, normal)) >= 0.9 * mag
        on_edge = across & (mag >= 0.3 * np.median(mag, axis=1, keepdims=True))
    else:
        on_edge = np.zeros((0, 0), bool)
    base = np.array([bend(p[m]) for p, m in zip(pts, on_edge)]) if len(pts) else np.zeros(0)
    bend_series, new_series, gone_series = [0.0], [0.0], [0.0]
    bend_where, new_where, gone_where = [None], [None], [None]
    for k in range(1, len(chain.g)):
        if len(pts):
            tracked, conv = chain.forward(k, pts.reshape(-1, 2))
            tracked, conv = tracked.reshape(pts.shape), conv.reshape(pts.shape[:2])
        else:
            tracked, conv = pts, on_edge
        b = EDGE_MARGIN  # the map is least reliable at the frame border, where content enters and leaves
        inside = (tracked[..., 0] >= b) & (tracked[..., 0] <= w - 1 - b) & (tracked[..., 1] >= b) & (tracked[..., 1] <= h - 1 - b) & on_edge & conv
        worst, worst_i, gone_len, gone_box = 0.0, -1, 0.0, None
        for i in range(len(tracked)):
            if inside[i].sum() < 12:
                continue
            bi = bend(tracked[i][inside[i]]) - base[i]
            if bi > worst:
                worst, worst_i = bi, i
            if edge_supported(dil[k], tracked[i][inside[i]], edges[0], pts[i][inside[i]]) < 0.5:
                gone_len += len0[i]
                gone_box = box_frac(*tracked[i][inside[i]].min(0), *tracked[i][inside[i]].max(0), w, h)
        bend_series.append(worst)
        bend_where.append(box_frac(*tracked[worst_i][inside[worst_i]].min(0), *tracked[worst_i][inside[worst_i]].max(0), w, h)
                          if worst_i >= 0 else None)
        gone_series.append(gone_len)
        gone_where.append(gone_box)
        segs_k = detect_segments(chain.g[k], min_len)
        new_len, new_box = 0.0, None
        if len(segs_k):
            pk = seg_points(segs_k)
            m = EDGE_MARGIN
            in_k = (pk[..., 0] >= m) & (pk[..., 0] <= w - 1 - m) & (pk[..., 1] >= m) & (pk[..., 1] <= h - 1 - m)
            back = sample(chain.maps[k], pk.reshape(-1, 2)).reshape(pk.shape)
            b = EDGE_MARGIN
            ok = in_k & (back[..., 0] >= b) & (back[..., 0] <= w - 1 - b) & (back[..., 1] >= b) & (back[..., 1] <= h - 1 - b)
            for i in range(len(pk)):
                if ok[i].sum() < 12:
                    continue
                if edge_supported(dil[0], back[i][ok[i]], edges[k], pk[i][ok[i]]) < 0.5:
                    new_len += float(np.hypot(*(segs_k[i, 2:] - segs_k[i, :2]))) / diag
                    new_box = box_frac(*pk[i].min(0), *pk[i].max(0), w, h)
        new_series.append(new_len)
        new_where.append(new_box)
    return {"segments": len(segs0), "bend": bend_series, "bend_where": bend_where, "new": new_series,
            "new_where": new_where, "gone": gone_series, "gone_where": gone_where}



# ---- judge ------------------------------------------------------------------------------------------------

CATEGORIES = ["door", "opening", "window", "ceiling_light", "wall_plate", "built_in", "furniture", "decor"]
JUDGE_VERSION = "inventory-v1"  # part of the inventory cache key; bump when the inventory prompt changes

JUDGE_SYSTEM = """You check AI-generated real-estate walkthrough videos against the real photos of the house. A buyer must never be shown a door, window, light, wall plate or fixture the house does not have, or a view through a window or doorway that is not there. Be thorough and literal, and report only what you can point to in an image."""

INVENTORY_PROMPT = """This is a real photo of a room in a house for sale. List every countable, checkable item that someone could verify in a video of this room, so a later check can spot anything added, removed or changed:
- door: each door leaf, open or closed, with its panel style;
- opening: each doorway, passage or arch, and what is visible beyond it (which room, hallway, fixtures);
- window: each window unit, its pane layout and blinds, and what is visible through it (sky, field, houses, deck, railing, trees);
- ceiling_light: each pendant, chandelier, recessed light or fan, with counts;
- wall_plate: every switch plate, outlet, thermostat and vent, with the number of switches on each plate;
- built_in: cabinets, counters, sinks, shelves, mirrors, fireplace, notable trim;
- furniture: large pieces with counts (beds, tables, chairs, sofas, benches, nightstands, stools);
- decor: large art, plants, rugs, lamps and table centrepieces.
Give each item an id starting with "{prefix}" ({prefix}1, {prefix}2, ...), its category, a short description (shape, count, colour), where it is in the photo (left, centre or right; top, middle or bottom; what it is next to) and, for doors, openings and windows, what is visible through or beyond it (an empty string for other items). Some photos contain faint see-through ghost images left by their editing; do not list those. Finally say what is cut off at each edge of the photo."""

INVENTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "beyond": {"type": "string"},
                },
                "required": ["id", "category", "description", "location", "beyond"],
                "additionalProperties": False,
            },
        },
        "edges": {"type": "string"},
    },
    "required": ["items", "edges"],
    "additionalProperties": False,
}

CHECK_PROMPT = """{photos_intro} The FRAME images are, in time order, from a video that an AI model generated to move the camera {path}. This is the inventory of checkable items in the photo{s}:

{inventory}

Go through every frame. Compare what is in view with the inventory and the photo{s}, item by item: doors and door leaves, openings and what lies beyond each, windows and what is visible through each, ceiling lights, wall plates (switches, outlets, thermostats, vents), built-ins, large furniture and decor. Look closely at small items such as switch plates and outlets.

Report each problem once, at the first frame that shows it:
- new: an item of those kinds that is not in the inventory. Set in_photo_view to true when the place where it appears is visible in a photo, so the video invented it; false when it lies entirely outside what the photos show (beyond their edges or hidden behind something), where it cannot be verified.
- missing: an inventory item that should be in view in that frame but is gone.
- changed: an inventory item whose shape, count, size or position differs, or a window, door or opening through which something different is visible than in the photo.

Not problems: camera motion (framing, zoom, perspective, parallax), items leaving the frame, lighting or exposure changes, motion blur, and the video dropping faint see-through ghost images that the photo's editing left.

Set confidence to high when you can see the problem clearly, medium when fairly sure, low when unsure. Return an empty list if you find nothing."""

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "frame": {"type": "string"},
                    "kind": {"type": "string", "enum": ["new", "missing", "changed"]},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "item_id": {"type": "string"},
                    "description": {"type": "string"},
                    "where": {"type": "string"},
                    "in_photo_view": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["frame", "kind", "category", "item_id", "description", "where", "in_photo_view", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings"],
    "additionalProperties": False,
}

CENSUS_PROMPT = """{photos_intro} The FRAME images are, in time order, from a video that an AI model generated to move the camera {path}. This is the inventory of checkable items in the photo{s}:

{inventory}

Check every frame against the inventory, one frame at a time. For each frame, list every item of these kinds that you can see in it: doors and door leaves, openings, windows, ceiling lights, wall plates (switches, outlets, thermostats, vents), built-ins, large furniture and decor. Look closely at small items such as switch plates and outlets. For each item give:
- item_id: the inventory id it is, or "NEW" if it is none of them;
- beyond: for windows, doors and openings, what you can see through or beyond it in this frame (empty for other items);
- matches_photo: false if its shape, count, size or position differs from the photo, or if what is visible through or beyond it differs from what that same window, door or opening shows in the photo, even when the other photo makes the new view plausible; true otherwise (NEW items: false);
- in_photo_view: for NEW items, true when the wall, ceiling or floor area where it appears is visible in a photo, even if the photo shows nothing there; false only when that area lies beyond the edges of, or hidden behind something in, every photo. True for inventory items;
- note: what differs, when matches_photo is false;
- confidence: high, medium or low that the item is as you describe.
Then list the inventory items that should be in view in this frame, given the camera position, but are not there.

Not differences: camera motion (framing, zoom, perspective, parallax), items leaving the frame, lighting or exposure changes, motion blur, and the video dropping faint see-through ghost images that the photo's editing left."""

CENSUS_SCHEMA = {
    "type": "object",
    "properties": {
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "frame": {"type": "string"},
                    "visible": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_id": {"type": "string"},
                                "category": {"type": "string", "enum": CATEGORIES},
                                "beyond": {"type": "string"},
                                "matches_photo": {"type": "boolean"},
                                "in_photo_view": {"type": "boolean"},
                                "note": {"type": "string"},
                                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                            },
                            "required": ["item_id", "category", "beyond", "matches_photo", "in_photo_view", "note", "confidence"],
                            "additionalProperties": False,
                        },
                    },
                    "missing": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_id": {"type": "string"},
                                "note": {"type": "string"},
                                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                            },
                            "required": ["item_id", "note", "confidence"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["frame", "visible", "missing"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["frames"],
    "additionalProperties": False,
}


def census_findings(answer: dict) -> list[dict]:
    """Findings in the CHECK_SCHEMA shape from a census answer, each item reported at its first frame."""
    out, seen = [], set()
    for fr in answer["frames"]:
        for v in fr["visible"]:
            if v["item_id"] != "NEW" and v["matches_photo"]:
                continue
            kind = "new" if v["item_id"] == "NEW" else "changed"
            # The same new item comes back in later frames under other wording; keep the first per category.
            key = (kind, v["item_id"] if kind == "changed" else v["category"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"frame": fr["frame"], "kind": kind, "category": v["category"], "item_id": v["item_id"],
                        "description": v["note"] or v["beyond"], "where": v["beyond"], "in_photo_view": v["in_photo_view"],
                        "confidence": v["confidence"]})
        for m in fr["missing"]:
            if ("missing", m["item_id"]) in seen:
                continue
            seen.add(("missing", m["item_id"]))
            out.append({"frame": fr["frame"], "kind": "missing", "category": "other", "item_id": m["item_id"],
                        "description": m["note"], "where": "", "in_photo_view": True, "confidence": m["confidence"]})
    return out


def format_inventory(label: str, inv: dict) -> str:
    lines = [f"{label}:"]
    for it in inv["items"]:
        beyond = f"; beyond it: {it['beyond']}" if it["beyond"] else ""
        lines.append(f"- {it['id']} {it['category']}: {it['description']}; at {it['location']}{beyond}")
    lines.append(f"Cut off at the edges: {inv['edges']}")
    return "\n".join(lines)


def counts_against(f: dict, strict: bool) -> bool:
    """Whether one judge finding fails the clip. Low confidence never does. "missing" needs high confidence:
    all four false alarms on good clips in the experiment were medium-confidence "missing" findings. A new
    item where no photo shows the spot fails only in strict mode, because nothing shows the house has it."""
    if f["confidence"] == "low":
        return False
    if f["kind"] == "missing":
        return f["confidence"] == "high"
    if f["kind"] == "new" and not f["in_photo_view"]:
        return strict
    return True


_CHANGE_WORD = {"new": "appeared", "missing": "disappeared", "changed": "changed_shape"}

Judge = Callable[[np.ndarray, "np.ndarray | None", "list[tuple[float, np.ndarray]]"], dict[str, Any]]


class InventoryJudge:
    """Judge v2: an inventory of each photo (cached in `cache_dir` by pixel content), then one census call."""

    def __init__(self, cache_dir: Path, effort: str = "medium", strict: bool = True, model: str | None = None):
        self.cache_dir, self.effort, self.strict, self.model = Path(cache_dir), effort, strict, model

    def inventory(self, photo: np.ndarray, prefix: str) -> tuple[dict, dict | None]:
        """(inventory, usage of the call that made it, or None when it came from the cache)."""
        from ..models.vision import ask_json

        h = hashlib.sha1(f"{photo.shape}{JUDGE_VERSION}:{prefix}".encode())
        h.update(np.ascontiguousarray(photo).tobytes())
        cached = self.cache_dir / f"inventory-{prefix}-{h.hexdigest()[:16]}.json"
        if cached.is_file():
            return json.loads(cached.read_text(encoding="utf-8")), None
        answer, usage = ask_json([("PHOTO", photo)], INVENTORY_PROMPT.format(prefix=prefix), INVENTORY_SCHEMA,
                                 model=self.model, effort=self.effort, max_tokens=16000, system=JUDGE_SYSTEM)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".part")
        tmp.write_text(json.dumps(answer, indent=1), encoding="utf-8")
        tmp.replace(cached)
        return answer, _usage("judge_clip_inventory", usage)

    def __call__(self, photo: np.ndarray, end_photo: np.ndarray | None,
                 frames: list[tuple[float, np.ndarray]]) -> dict[str, Any]:
        from ..models.vision import ask_json

        usages = []
        inv_s, u = self.inventory(photo, "S")
        usages += [u] if u else []
        blocks, images = [format_inventory("START PHOTO", inv_s)], [("START PHOTO", photo)]
        n_items = len(inv_s["items"])
        if end_photo is not None:
            inv_e, u = self.inventory(end_photo, "E")
            usages += [u] if u else []
            blocks.append(format_inventory("END PHOTO", inv_e))
            images.append(("END PHOTO", end_photo))
            n_items += len(inv_e["items"])
            intro = ("The first two images are real photos of the house: START PHOTO is where the video begins and "
                     "END PHOTO where it ends.")
            path = "from the start photo to the end photo"
        else:
            intro = "The first image, START PHOTO, is a real photo of the house and the frame the video begins on."
            path = "through the room shown in the start photo"
        images += [(f"FRAME {i + 1} at t={t:.1f}s", f) for i, (t, f) in enumerate(frames)]
        prompt = CENSUS_PROMPT.format(photos_intro=intro, path=path, s="s" if end_photo is not None else "",
                                      inventory="\n\n".join(blocks))
        answer, usage = ask_json(images, prompt, CENSUS_SCHEMA, model=self.model, effort=self.effort,
                                 max_tokens=32000, system=JUDGE_SYSTEM)
        usages.append(_usage("judge_clip", usage))
        findings = census_findings(answer)
        failing = [f for f in findings if counts_against(f, self.strict)]
        return {
            "pass": not failing,
            "failing": failing,
            "not_counted": [f for f in findings if f not in failing],
            # The film's v1 shape, so the storyboard and disclosure keep reading `changes`.
            "changes": [{"object": f"{f['category']}: {f['description']}", "change": _CHANGE_WORD[f["kind"]],
                         "first_frame": f["frame"], "where": f["where"]} for f in failing],
            "confidence": max((f["confidence"] for f in failing), key=["low", "medium", "high"].index, default="none"),
            "inventory_items": n_items,
            "frames_checked": len(frames),
            "form": "census",
            "strict": self.strict,
            "model": usage.model,
            "usages": usages,
            "usd": round(sum(x["usd"] for x in usages), 4),
        }


def _usage(purpose: str, usage) -> dict:
    return {"purpose": purpose, "model": usage.model, "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens, "usd": round(usage.usd, 6)}


# ---- the check --------------------------------------------------------------------------------------------

def out_of_photo(H_photo: np.ndarray, H_k: np.ndarray, photo_shape, frame_shape) -> float:
    """Fraction of frame k that maps outside the photo: frame k -> frame 0 (H_k) -> photo (H_photo^-1)."""
    h, w = frame_shape
    ph, pw = photo_shape
    ys, xs = np.mgrid[0:h:8, 0:w:8]
    p = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32).reshape(-1, 1, 2)
    q = cv2.perspectiveTransform(p, np.linalg.inv(H_photo) @ H_k).reshape(-1, 2)
    inside = (q[:, 0] >= 0) & (q[:, 0] < pw) & (q[:, 1] >= 0) & (q[:, 1] < ph)
    return float(1 - inside.mean())


@dataclass
class ClipCheck:
    passed: bool
    reasons: list[str]  # one plain sentence per failed signal; empty when passed
    signals: dict[str, Any]
    sheet: np.ndarray  # contact sheet for people (start photo, end photo if any, six frames)
    timing_s: dict[str, float] = field(default_factory=dict)

    @property
    def judge(self) -> dict[str, Any] | None:
        return self.signals.get("judge")

    def summary(self) -> dict[str, Any]:
        """JSON-safe record without the sheet pixels."""
        return {"passed": self.passed, "reasons": self.reasons, "signals": self.signals, "timing_s": self.timing_s,
                "limits": asdict(LIMITS), "units": f"px at {WORK_LONG} px long edge; boxes are [x0, y0, x1, y1] fractions"}


def check_clip(clip: Path, start: np.ndarray, end: np.ndarray | None = None, judge: Judge | None = None) -> ClipCheck:
    """Compare `clip` with its start photo (and end photo, for a clip generated between two photos).

    `start` and `end` are RGB uint8 arrays at full resolution. `judge=None` skips the vision judge, and the
    deterministic signals alone decide. A judge that raises fails the clip: an unchecked generated clip
    must not play."""
    t0 = time.perf_counter()
    smp = read_samples(clip, STEP_S)
    photo, end_photo = start, end
    work = [to_work(f) for f in smp.frames]
    grays = [gray(f) for f in work]
    timing = {"decode": time.perf_counter() - t0}

    t = time.perf_counter()
    reg0 = register(photo, work[0])
    reg1 = register(end_photo, work[-1]) if end_photo is not None else None
    shared = None
    if end_photo is not None:
        r = register(photo, work[-1])
        shared = r.get("coverage", 0.0) if r["inliers"] >= LIMITS.endpoint_min_inliers else 0.0
    trackable = shared is None or shared >= LIMITS.min_shared_for_tracking
    timing["endpoint"] = time.perf_counter() - t

    signals: dict = {
        "endpoint_start": {"pass": endpoint_ok(reg0), **{k: v for k, v in reg0.items() if k != "H"}},
        "endpoint_end": ({"pass": endpoint_ok(reg1), **{k: v for k, v in reg1.items() if k != "H"}} if reg1 else None),
    }
    oop = None
    if trackable:
        signals.update(tracking_signals(grays, smp.times, reg0, photo.shape[:2], timing))
        oop = signals.pop("_oop")
    else:
        why = {"checkable": False, "pass": None, "shared_with_start": round(shared, 3),
               "why": f"the start photo covers {shared * 100:.0f}% of the last frame (needs "
                      f"{LIMITS.min_shared_for_tracking * 100:.0f}%), so frame-0 tracks cannot follow the clip"}
        signals.update({"motion": why, "appearance": why, "lines": why})
    signals["coverage"] = {"pass": end is not None or oop is None or oop <= LIMITS.out_of_photo_max,
                           "max_out_of_photo": oop, "shared_start_last": None if shared is None else round(shared, 3)}

    tiles = [("START PHOTO", photo)] + ([("END PHOTO", end_photo)] if end_photo is not None else [])
    tiles += [(f"t={smp.times[i]:.1f}s", smp.frames[i]) for i in pick_evenly(len(smp.frames), SHEET_FRAMES)]
    sheet = contact_sheet(tiles)

    if judge is not None:
        t = time.perf_counter()
        idx = pick_evenly(len(smp.frames), JUDGE_FRAMES)
        try:
            signals["judge"] = judge(photo, end_photo, [(smp.times[i], smp.frames[i]) for i in idx])
        except Exception as e:  # noqa: BLE001 - an unchecked clip fails, and the reason says why
            signals["judge"] = {"pass": False, "error": f"{type(e).__name__}: {e}", "failing": [], "changes": [],
                                "usages": [], "usd": 0.0}
        timing["judge"] = time.perf_counter() - t

    reasons = []
    for name in ("endpoint_start", "endpoint_end"):
        r = signals[name]
        if r and not r["pass"]:
            which = "first frame vs start photo" if name == "endpoint_start" else "last frame vs end photo"
            reasons.append(f"{which} do not match: {r['inliers']} homography inliers, edge SSIM {r['edge_ssim']}, "
                           f"worst tile {r['min_tile_ssim']} at {r['worst_tile']}")
    if trackable:
        reasons += tracking_reasons(signals)
    if not signals["coverage"]["pass"]:
        reasons.append(f"{oop * 100:.0f}% of a frame shows area outside the start photo, which cannot be checked")
    jd = signals.get("judge")
    if jd is not None and not jd["pass"]:
        if jd.get("error"):
            reasons.append(f"the vision judge could not run ({jd['error']}), so the clip is unchecked")
        else:
            reasons.append("judge: " + "; ".join(f"{f['category']} {f['kind']} at {f['frame']} ({f['confidence']}): "
                                                  f"{f['description']}" for f in jd["failing"]))

    timing["total"] = time.perf_counter() - t0
    return ClipCheck(passed=not reasons, reasons=reasons, signals=signals, sheet=sheet,
                     timing_s={k: round(v, 2) for k, v in timing.items()})


def tracking_signals(grays: list[np.ndarray], ts: list[float], reg0: dict, photo_shape, timing: dict) -> dict:
    """Motion, appearance and line signals from the frame-0 chain, plus "_oop" (largest out-of-photo share)."""
    t = time.perf_counter()
    chain = Chain(grays)
    timing["flow"] = time.perf_counter() - t

    t = time.perf_counter()
    steps = [motion_step(chain, k) for k in range(1, len(grays))]
    motion_series = [0.0] + [s["score"] for s in steps]
    m_peak, m_i = persistent_peak(motion_series)
    app = [{"score": 0.0, "box": None}] + [appearance_step(chain, k) for k in range(1, len(grays))]
    a_peak, a_i = persistent_peak([a["score"] for a in app])
    timing["motion_appearance"] = time.perf_counter() - t

    t = time.perf_counter()
    lines = line_signals(chain)
    b_peak, b_i = persistent_peak(lines["bend"])
    n_peak, n_i = persistent_peak(lines["new"])
    g_peak, g_i = persistent_peak(lines["gone"])
    timing["lines"] = time.perf_counter() - t

    oop = None
    if reg0["H"] is not None:
        H_photo = np.array(reg0["H"])
        vals = [out_of_photo(H_photo, s["H"], photo_shape, chain.shape) for s in steps if s["H"] is not None]
        oop = round(max(vals), 3) if vals else None

    return {
        "motion": {"pass": m_peak <= LIMITS.motion_max_px, "peak_px": round(m_peak, 2), "at_s": round(ts[m_i], 2),
                   "cell": steps[max(m_i - 1, 0)]["cell"] if steps else None,
                   "models": "".join(s["model"] or "-" for s in steps),
                   "h_inlier_frac": [s["h_inlier_frac"] for s in steps],
                   "series_px": [round(v, 2) for v in motion_series]},
        "appearance": {"pass": a_peak <= LIMITS.appear_max_frac, "peak_frac": round(a_peak, 4), "at_s": round(ts[a_i], 2),
                       "box": app[a_i + 1]["box"] if a_i + 1 < len(app) else None,
                       "series_frac": [round(a["score"], 4) for a in app]},
        "lines": {"pass": b_peak <= LIMITS.line_max_bend_px and n_peak <= LIMITS.line_max_new and g_peak <= LIMITS.line_max_new,
                  "segments": lines["segments"],
                  "bend_px": round(b_peak, 2), "bend_at_s": round(ts[b_i], 2),
                  "bend_box": lines["bend_where"][b_i + 1] if b_i + 1 < len(ts) else None,
                  "new_diag": round(n_peak, 3), "new_at_s": round(ts[n_i], 2),
                  "new_box": lines["new_where"][n_i + 1] if n_i + 1 < len(ts) else None,
                  "gone_diag": round(g_peak, 3), "gone_at_s": round(ts[g_i], 2),
                  "gone_box": lines["gone_where"][g_i + 1] if g_i + 1 < len(ts) else None},
        "_oop": oop,
    }


def tracking_reasons(signals: dict) -> list[str]:
    reasons = []
    m = signals["motion"]
    if not m["pass"]:
        reasons.append(f"motion no rigid camera explains: {m['peak_px']} px from t={m['at_s']}s in cell {m['cell']}")
    a = signals["appearance"]
    if not a["pass"]:
        reasons.append(f"structure not in the first frame: {a['peak_frac'] * 100:.2f}% of the frame from t={a['at_s']}s at {a['box']}")
    ln = signals["lines"]
    if ln["bend_px"] > LIMITS.line_max_bend_px:
        reasons.append(f"a straight edge bends by {ln['bend_px']} px from t={ln['bend_at_s']}s at {ln['bend_box']}")
    if ln["new_diag"] > LIMITS.line_max_new:
        reasons.append(f"new straight edges ({ln['new_diag']} frame diagonals) from t={ln['new_at_s']}s at {ln['new_box']}")
    if ln["gone_diag"] > LIMITS.line_max_new:
        reasons.append(f"straight edges vanished ({ln['gone_diag']} frame diagonals) from t={ln['gone_at_s']}s at {ln['gone_box']}")
    return reasons
