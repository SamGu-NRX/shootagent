"""Exposure brackets: relative EV, exposure-invariant alignment, Mertens fusion, ghost map.

All functions work on one coherent group of brackets (one camera position). Use `grouping.py` first
when the input may mix views.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import features as F
from .color import srgb_to_linear


# ---- EV ------------------------------------------------------------------------------------------------
def _pair_ev(lin_a: np.ndarray, lin_b: np.ndarray) -> tuple[float, int]:
    """log2(exposure_b / exposure_a) from pixels well exposed in both. Returns (ev, n_valid)."""
    ok = (lin_a > 0.01) & (lin_a < 0.8) & (lin_b > 0.01) & (lin_b < 0.8)
    n = int(ok.sum())
    if n < 50:
        return float("nan"), n
    return float(np.median(np.log2(lin_b[ok] / lin_a[ok]))), n


def estimate_ev(imgs: list[np.ndarray]) -> list[float]:
    """Relative exposure of each bracket in stops; 0 is the bracket with the median brightness.

    Works on blurred 256 px copies (tolerates small misalignment). Brackets are sorted by brightness and
    EVs are chained between neighbours, so a pair with no mutually well-exposed pixels never has to be
    compared directly. A pair with too few valid pixels falls back to the ratio of mean luminance.
    """
    if not imgs:
        return []
    lins = []
    for im in imgs:
        small = cv2.resize(im, (256, max(1, round(256 * im.shape[0] / im.shape[1]))), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), 2)
        lin = srgb_to_linear(small) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        lins.append(lin)
    means = [float(l.mean()) for l in lins]
    order = np.argsort(means)
    ev_sorted = [0.0]
    for k in range(1, len(order)):
        a, b = lins[order[k - 1]], lins[order[k]]
        if a.shape != b.shape:
            b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
        ev, _ = _pair_ev(a, b)
        if not np.isfinite(ev):
            ev = float(np.log2(max(means[order[k]], 1e-4) / max(means[order[k - 1]], 1e-4)))
        ev_sorted.append(ev_sorted[-1] + max(ev, 0.0))
    evs = np.zeros(len(imgs))
    evs[order] = ev_sorted
    evs -= evs[order[len(order) // 2]]
    return [round(float(e), 2) for e in evs]


# ---- alignment -----------------------------------------------------------------------------------------
@dataclass
class BracketAlignment:
    index: int  # position in the input list
    H: np.ndarray  # 3x3 mapping this bracket's pixels -> reference bracket's pixels (working resolution)
    inliers: int
    residual_px: float  # median reprojection error of RANSAC inliers, in working-resolution px
    ok: bool  # False when matching failed and identity was used

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "inliers": self.inliers,
            "residual_px": round(self.residual_px, 2) if np.isfinite(self.residual_px) else None,
            "ok": self.ok,
            "shift_px": round(float(np.hypot(self.H[0, 2], self.H[1, 2])), 1),
        }


def pick_reference(evs: list[float]) -> int:
    """The bracket nearest EV 0 (the median exposure) anchors alignment."""
    return int(np.argmin(np.abs(np.asarray(evs, dtype=float))))


def align_brackets(
    imgs: list[np.ndarray],
    ref: int,
    feats: list[F.Features] | None = None,
    matches: dict[tuple[int, int], F.PairMatch] | None = None,
) -> list[BracketAlignment]:
    """Homography of every bracket onto bracket `ref`, from SIFT on CLAHE-equalised luma.

    `imgs` are the brackets at working resolution (sizes may differ slightly). Precomputed features/matches
    (from grouping) are reused when given. A bracket that cannot be matched gets identity and ok=False;
    callers should treat it as unaligned (tripod brackets are usually close to identity anyway).
    """
    feats = feats or [F.detect(im) for im in imgs]
    w = imgs[ref].shape[1]
    out = []
    for i, im in enumerate(imgs):
        if i == ref:
            out.append(BracketAlignment(i, np.eye(3), 0, 0.0, True))
            continue
        m = (matches or {}).get((i, ref)) or F.match(feats[i], feats[ref])
        if m.H is None or m.inliers < 12 or m.corner_shift > 0.15:
            out.append(BracketAlignment(i, np.eye(3), m.inliers, float("inf"), False))
            continue
        s_src, s_dst = feats[i].scale_for(im.shape[1]), feats[ref].scale_for(w)
        H = F.scale_homography(m.H, s_src, s_dst)
        out.append(BracketAlignment(i, H, m.inliers, m.reproj_px / s_dst, True))
    return out


def warp_to(img: np.ndarray, H: np.ndarray, size_wh: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Warp `img` by H into an image of size (W, H). Returns (warped, valid bool mask)."""
    warped = cv2.warpPerspective(img, H, size_wh, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    valid = cv2.warpPerspective(np.full(img.shape[:2], 255, np.uint8), H, size_wh, flags=cv2.INTER_NEAREST) > 0
    return warped, valid


def apply_alignment(imgs: list[np.ndarray], aligns: list[BracketAlignment], ref: int) -> tuple[list[np.ndarray], np.ndarray]:
    """Warp every bracket onto the reference bracket. Pixels a bracket does not cover are filled from the
    reference bracket so fusion never sees black borders. Returns (aligned, coverage) where coverage is
    the bool mask covered by every bracket."""
    h, w = imgs[ref].shape[:2]
    out, cover = [], np.ones((h, w), bool)
    for im, al in zip(imgs, aligns):
        if al.index == ref:
            out.append(im)
            continue
        warped, valid = warp_to(im, al.H, (w, h))
        warped[~valid] = imgs[ref][~valid]
        out.append(warped)
        cover &= valid
    return out, cover


# ---- fusion --------------------------------------------------------------------------------------------
def fuse_mertens(aligned: list[np.ndarray]) -> np.ndarray:
    """Mertens exposure fusion (contrast, saturation and well-exposedness weights) -> RGB uint8."""
    if len(aligned) == 1:
        return aligned[0].copy()
    merge = cv2.createMergeMertens(1.0, 1.0, 1.0)
    fused = merge.process([np.ascontiguousarray(a) for a in aligned])
    # Mertens output drifts slightly outside 0..1 and is flat; stretch the 0.05/99.95 percentiles.
    lo, hi = np.percentile(fused, [0.05, 99.95])
    fused = (fused - lo) / max(hi - lo, 1e-6)
    return np.clip(np.round(fused * 255), 0, 255).astype(np.uint8)


# ---- ghosting ------------------------------------------------------------------------------------------
def _luma_lut(src: np.ndarray, dst: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """Monotone 256-entry LUT mapping src luma to dst luma (comparametric fit on valid pixels)."""
    s, d = src[ok].astype(np.int32), dst[ok].astype(np.float32)
    lut = np.full(256, np.nan, np.float32)
    counts = np.bincount(s, minlength=256)
    sums = np.bincount(s, weights=d, minlength=256)
    has = counts >= 5
    lut[has] = sums[has] / counts[has]
    idx = np.arange(256)
    if has.sum() < 2:
        return idx.astype(np.float32)
    lut = np.interp(idx, idx[has], lut[has]).astype(np.float32)
    return np.maximum.accumulate(lut)


def ghost_map(aligned: list[np.ndarray], ref: int, cover: np.ndarray | None = None, tol: float = 28.0
              ) -> tuple[np.ndarray, float]:
    """Where aligned brackets disagree with the reference bracket after exposure normalisation.

    Each bracket's luma is mapped onto the reference's luma with a monotone comparametric LUT; the map
    is the max absolute residual over brackets, counted only where both frames are neither crushed nor
    clipped in any channel. Returns (map float32 0..1 at working resolution, score = fraction of comparable pixels
    whose residual exceeds `tol` luma levels after a 5 px opening that drops noise and thin edges).
    `cover` (from `apply_alignment`) excludes border pixels that were filled from the reference bracket.
    """
    ref_y = cv2.cvtColor(aligned[ref], cv2.COLOR_RGB2GRAY)
    ref_b = cv2.GaussianBlur(ref_y, (0, 0), 1.5)
    # Luma is only a function of exposure while no channel clips (a red that clips in R changes hue).
    ref_unclipped = cv2.erode((aligned[ref].max(axis=2) < 245).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    resid = np.zeros(ref_y.shape, np.float32)
    comparable = np.zeros(ref_y.shape, bool)
    for i, im in enumerate(aligned):
        if i == ref:
            continue
        y = cv2.GaussianBlur(cv2.cvtColor(im, cv2.COLOR_RGB2GRAY), (0, 0), 1.5)
        unclipped = cv2.erode((im.max(axis=2) < 245).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        ok = (y > 12) & (ref_b > 12) & unclipped & ref_unclipped
        if cover is not None:
            ok &= cover
        if ok.mean() < 0.05:
            continue
        lut = _luma_lut(y, ref_b, ok)
        r = np.abs(lut[y] - ref_b.astype(np.float32))
        r[~ok] = 0
        resid = np.maximum(resid, r)
        comparable |= ok
    hit = (resid > tol).astype(np.uint8)
    hit = cv2.morphologyEx(hit, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    score = float(hit[comparable].mean()) if comparable.any() else 0.0
    return np.clip(resid / (2 * tol), 0, 1).astype(np.float32), score
