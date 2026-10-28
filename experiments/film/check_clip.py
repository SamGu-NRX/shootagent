"""Check a generated walkthrough clip against the photo it was made from.

An image-to-video model asked to move the camera through a room sometimes invents a door or window,
drops a fixture or bends a window frame. This script compares the clip with its start photo (and end
photo, if the clip was generated between two) and fails it with specific reasons.

Signals, all measured on frames sampled every 0.25 s and resized to 960 px on the long edge:
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
  edge left under their tracked points are vanished lines.
- judge: Claude looks at one contact sheet (photo plus 6 frames) and answers one question: did a
  door, window, fixture or large furniture appear, disappear or change shape?
Motion, appearance and line findings count only when they persist across two consecutive samples
(0.5 s), so a single bad decode does not fail a clip.

Usage:
    python experiments/film/check_clip.py clip.mp4 --start photo.jpg [--end photo.jpg]
    python experiments/film/check_clip.py --manifest manifest.json      # [{"clip", "start", "end"?}, ...]
    python experiments/film/check_clip.py --dir folder                  # folder/clips.json, or <stem>.json
                                                                       # sidecars {"start", "end"?}
Each clip gets <out>/<stem>.check.json and <out>/<stem>.sheet.jpg; with several clips, summary.json.
Thresholds (LIMITS) were set on synthetic clips and checked on depth-parallax clips; none has been
checked on a real generated clip yet.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import median_filter
from skimage.metrics import structural_similarity

from clipio import contact_sheet, load_rgb, pick_evenly, read_samples, save_jpg

HERE = Path(__file__).resolve().parent
WORK_LONG = 960  # analysis resolution (long edge, px); every px figure in the output is at this scale
STEP_S = 0.25
CELL = 64  # motion residual grid cell, px
EDGE_MARGIN = 8  # px; line points closer than this to the frame border are not used
SHEET_FRAMES = 6


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

    def forward(self, k: int, pts0: np.ndarray, iters: int = 4) -> np.ndarray:
        """Frame-k positions of frame-0 points: invert the map by Newton steps from the homography guess."""
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
        return x

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
        tracked = chain.forward(k, pts.reshape(-1, 2)).reshape(pts.shape) if len(pts) else pts
        b = EDGE_MARGIN  # the map is least reliable at the frame border, where content enters and leaves
        inside = (tracked[..., 0] >= b) & (tracked[..., 0] <= w - 1 - b) & (tracked[..., 1] >= b) & (tracked[..., 1] <= h - 1 - b) & on_edge
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
        bend_where.append(box_frac(*tracked[worst_i].min(0), *tracked[worst_i].max(0), w, h) if worst_i >= 0 else None)
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

JUDGE_PROMPT = """The contact sheet shows a real photo of a room ({photos}) and six frames, in time order, from a short video that an AI model generated from {that}. The model was asked only to move the camera slowly. Camera motion is expected: framing, zoom, perspective and parallax change, and objects may leave the frame or enter at its edges as the camera moves. Small lighting or exposure shifts are expected too. None of those count. Some photos contain faint see-through ghost images from the photo's own editing; they are part of the photo.

Question: compared with the photo, does any door, window, built-in fixture (light, fan, cabinet, sink, mirror, vent, shelf, trim) or large piece of furniture appear, disappear, duplicate or change shape in any frame while it is in view? Edges that are straight in the photo but bent or wavy in a frame count as a change of shape.

List each change you can see: the object, the kind of change, the label of the first frame that shows it, and where in the frame it is. Set `changed` to true if you list any; otherwise return an empty list and false."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "object": {"type": "string"},
                    "change": {"type": "string", "enum": ["appeared", "disappeared", "duplicated", "changed_shape"]},
                    "first_frame": {"type": "string"},
                    "where": {"type": "string"},
                },
                "required": ["object", "change", "first_frame", "where"],
                "additionalProperties": False,
            },
        },
        "changed": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["changes", "changed", "confidence"],
    "additionalProperties": False,
}


def load_env(explicit: Path | None) -> None:
    """Make the gateway settings visible to shootagent.config. A worktree has no .env of its own, so
    fall back to the main checkout's (the parent of git's common dir)."""
    from dotenv import load_dotenv

    if explicit:
        if not explicit.is_file():
            raise SystemExit(f"--env-file {explicit} does not exist")
        load_dotenv(explicit)
        return
    if Path(".env").is_file():
        return
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=HERE,
                            capture_output=True, text=True)
    cand = Path(common.stdout.strip()).parent / ".env" if common.returncode == 0 else None
    if cand is None or not cand.is_file():
        raise SystemExit("no .env found for the judge: pass --env-file, run from the repo root, or use --no-judge")
    load_dotenv(cand)


def run_judge(sheet: np.ndarray, has_end: bool) -> dict:
    from shootagent.models.vision import ask_json

    photos = "START PHOTO, and END PHOTO where the video should end" if has_end else "START PHOTO"
    prompt = JUDGE_PROMPT.format(photos=photos, that="them" if has_end else "it")
    answer, usage = ask_json([("contact sheet: photo tiles, then video frames labelled with their time", sheet)], prompt,
                             JUDGE_SCHEMA, effort="medium")
    changes = answer.get("changes", [])
    return {"pass": not (answer["changed"] or changes), "changed": answer["changed"], "changes": changes,
            "confidence": answer["confidence"], "model": usage.model, "usd": round(usage.usd, 4)}


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


def check_clip(clip: Path, start: Path, end: Path | None, out_dir: Path, judge: bool) -> dict:
    t0 = time.perf_counter()
    smp = read_samples(clip, STEP_S)
    photo = load_rgb(start)
    end_photo = load_rgb(end) if end else None
    work = [to_work(f) for f in smp.frames]
    grays = [gray(f) for f in work]
    timing = {"decode": time.perf_counter() - t0}

    t = time.perf_counter()
    reg0 = register(photo, work[0])
    reg1 = register(end_photo, work[-1]) if end_photo is not None else None
    timing["endpoint"] = time.perf_counter() - t

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
        vals = [out_of_photo(H_photo, s["H"], photo.shape[:2], chain.shape) for s in steps if s["H"] is not None]
        oop = round(max(vals), 3) if vals else None

    stem = clip.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    tiles = [("START PHOTO", photo)] + ([("END PHOTO", end_photo)] if end_photo is not None else [])
    tiles += [(f"t={smp.times[i]:.1f}s", smp.frames[i]) for i in pick_evenly(len(smp.frames), SHEET_FRAMES)]
    sheet = contact_sheet(tiles)
    sheet_path = out_dir / f"{stem}.sheet.jpg"
    save_jpg(sheet_path, sheet)

    ts = smp.times
    signals: dict = {
        "endpoint_start": {"pass": endpoint_ok(reg0), **{k: v for k, v in reg0.items() if k != "H"}},
        "endpoint_end": ({"pass": endpoint_ok(reg1), **{k: v for k, v in reg1.items() if k != "H"}} if reg1 else None),
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
                  "bend_px": round(b_peak, 2), "bend_at_s": round(ts[b_i], 2), "bend_box": lines["bend_where"][b_i + 1] if b_i + 1 < len(ts) else None,
                  "new_diag": round(n_peak, 3), "new_at_s": round(ts[n_i], 2), "new_box": lines["new_where"][n_i + 1] if n_i + 1 < len(ts) else None,
                  "gone_diag": round(g_peak, 3), "gone_at_s": round(ts[g_i], 2), "gone_box": lines["gone_where"][g_i + 1] if g_i + 1 < len(ts) else None},
        "coverage": {"pass": end is not None or oop is None or oop <= LIMITS.out_of_photo_max, "max_out_of_photo": oop},
    }
    if judge:
        t = time.perf_counter()
        signals["judge"] = run_judge(sheet, end_photo is not None)
        timing["judge"] = time.perf_counter() - t

    reasons = []
    for name in ("endpoint_start", "endpoint_end"):
        r = signals[name]
        if r and not r["pass"]:
            which = "first frame vs start photo" if name == "endpoint_start" else "last frame vs end photo"
            reasons.append(f"{which} do not match: {r['inliers']} homography inliers, edge SSIM {r['edge_ssim']}, "
                           f"worst tile {r['min_tile_ssim']} at {r['worst_tile']}")
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
    if not signals["coverage"]["pass"]:
        reasons.append(f"{oop * 100:.0f}% of a frame shows area outside the start photo, which cannot be checked")
    if judge and not signals["judge"]["pass"]:
        items = "; ".join(f"{c['object']} {c['change']} at {c['first_frame']} ({c['where']})" for c in signals["judge"]["changes"])
        reasons.append(f"judge ({signals['judge']['confidence']} confidence): {items or 'changed'}")

    timing["total"] = time.perf_counter() - t0
    result = {
        "clip": str(clip), "start": str(start), "end": str(end) if end else None,
        "verdict": "fail" if reasons else "pass", "reasons": reasons, "signals": signals,
        "frames": {"fps": round(smp.fps, 3), "decoded": smp.n_frames, "sampled": len(smp.frames), "size": list(smp.frames[0].shape[1::-1])},
        "units": f"px at {WORK_LONG} px long edge; boxes are [x0, y0, x1, y1] fractions of the frame",
        "limits": asdict(LIMITS), "sheet": str(sheet_path.resolve()),
        "timing_s": {k: round(v, 2) for k, v in timing.items()},
    }
    (out_dir / f"{stem}.check.json").write_text(json.dumps(result, indent=2))
    return result


def jobs_from_dir(folder: Path) -> list[dict]:
    manifest = folder / "clips.json"
    if manifest.is_file():
        return [{**j, "clip": str((folder / j["clip"]).resolve())} for j in json.loads(manifest.read_text())]
    jobs, missing = [], []
    for clip in sorted(folder.glob("*.mp4")):
        side = clip.with_suffix(".json")
        if not side.is_file():
            missing.append(clip.name)
            continue
        meta = json.loads(side.read_text())
        jobs.append({"clip": str(clip), "start": str((folder / meta["start"]).resolve()),
                     "end": str((folder / meta["end"]).resolve()) if meta.get("end") else None})
    if missing:
        raise SystemExit(f"{folder} has no clips.json and these clips have no <stem>.json sidecar naming their start photo: {missing}")
    return jobs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip", nargs="?", type=Path)
    ap.add_argument("--start", type=Path)
    ap.add_argument("--end", type=Path)
    ap.add_argument("--manifest", type=Path, help="JSON list of {clip, start, end?}")
    ap.add_argument("--dir", type=Path, help="folder of clips with clips.json or <stem>.json sidecars")
    ap.add_argument("--out", type=Path, default=HERE / "out" / "checks")
    ap.add_argument("--no-judge", action="store_true", help="skip the Claude vision judge")
    ap.add_argument("--env-file", type=Path, help=".env with the model gateway settings")
    args = ap.parse_args()

    if args.manifest:
        jobs = json.loads(args.manifest.read_text())
    elif args.dir:
        jobs = jobs_from_dir(args.dir)
    elif args.clip and args.start:
        jobs = [{"clip": str(args.clip), "start": str(args.start), "end": str(args.end) if args.end else None}]
    else:
        ap.error("give a clip with --start, or --manifest, or --dir")
    if not args.no_judge:
        load_env(args.env_file)

    results = []
    for job in jobs:
        r = check_clip(Path(job["clip"]), Path(job["start"]), Path(job["end"]) if job.get("end") else None, args.out, not args.no_judge)
        for k in ("expected", "case", "move", "variant", "corruption"):
            if k in job:
                r[k] = job[k]
        (args.out / f"{Path(job['clip']).stem}.check.json").write_text(json.dumps(r, indent=2))
        results.append(r)
        s = r["signals"]
        flags = " ".join(n for n in ("endpoint_start", "endpoint_end", "motion", "appearance", "lines", "coverage", "judge")
                         if s.get(n) and not s[n]["pass"])
        exp = f" expected={job['expected']}" if "expected" in job else ""
        print(f"{r['verdict'].upper():4} {Path(job['clip']).name}{exp}  failed: {flags or '-'}  ({r['timing_s']['total']} s)", flush=True)
    if len(results) > 1:
        (args.out / "summary.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
