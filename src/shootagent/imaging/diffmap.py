"""Where the finished photo disagrees with the registered bracket reference.

1. `fit_style` maps the reference into the finished photo's look: a monotone L curve plus a robust
   affine model of a/b (Huber IRLS on [a, b, L, 1]), then a low-frequency L correction that absorbs
   local tone mapping. The fit is global for colour on purpose: an editor's global white balance
   is absorbed, a wall that alone turned from blue to grey is not.
2. Structural difference from local statistics of L in 9 px windows: texture present in the reference
   but flat in the finished photo is "missing" (false removal, sky painted over); the reverse is
   "added" (fire, artifacts, leftover edits); both textured but uncorrelated is "changed".
   Large flat changes (a removed lamp's interior) come from the colour difference after the fit.
3. Chroma drift: the finished a/b minus the fitted reference a/b, smoothed. A blue wall rendered grey
   shows as a chroma drop with a consistent hue.

Every region carries a normalised bbox, area fraction, kind, score and a bool mask at the analysis
resolution, plus mean colours of reference and finished inside it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .color import lab_to_rgb, rgb_to_hex, rgb_to_lab

FIRE_MIN_PX_FRAC = 0.000035  # ~60 flame pixels at 1600x1067


@dataclass
class StyleFit:
    l_lut: np.ndarray  # 1001 entries: L*10 -> L
    ab_coef: np.ndarray  # [g, ua, ub, va, vb]: ab = g*ab_ref + u*L + v*L^2/100
    lowfreq_sigma: float

    def to_dict(self) -> dict:
        return {"ab_coef": np.round(self.ab_coef, 3).tolist(), "lowfreq_frac": self.lowfreq_sigma}


@dataclass
class DiffRegion:
    kind: str  # "missing" | "added" | "changed" | "chroma" | "fire"
    bbox: list[float]  # normalised [x0, y0, x1, y1]
    area: float  # fraction of the frame
    score: float  # mean strength inside the region (map units, roughly 0..1)
    peak: float
    mask: np.ndarray = field(repr=False)  # bool, analysis resolution
    stats: dict = field(default_factory=dict)  # reference/finished hex, mean L, chroma, hue, position

    def to_dict(self) -> dict:
        return {"kind": self.kind, "bbox": [round(v, 4) for v in self.bbox], "area": round(self.area, 5),
                "score": round(self.score, 3), "peak": round(self.peak, 3), **self.stats}


@dataclass
class DiffResult:
    fitted: np.ndarray  # reference restyled toward the finished photo, RGB uint8
    missing: np.ndarray  # float32 maps 0..1
    added: np.ndarray
    changed: np.ndarray
    chroma: np.ndarray
    regions: list[DiffRegion]
    fit: StyleFit
    global_stats: dict


# ---- style fit -----------------------------------------------------------------------------------------
def _huber_lstsq(X: np.ndarray, Y: np.ndarray, delta: float, iters: int = 6) -> np.ndarray:
    w = np.ones(len(X), np.float32)
    coef = None
    for _ in range(iters):
        Xw = X * w[:, None]
        coef, *_ = np.linalg.lstsq(Xw.T @ X, Xw.T @ Y, rcond=None)
        r = np.linalg.norm(Y - X @ coef, axis=1)
        w = np.where(r <= delta, 1.0, delta / np.maximum(r, 1e-6)).astype(np.float32)
    return coef


def _fit_ab(r: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Finished a/b from reference Lab as g*[a, b] + u*L + v*L^2/100 with one scalar chroma gain g.

    A full 2x2 matrix can learn "halve the blue" from a photo dominated by one blue wall and so hide the
    very defect we look for; a scalar gain cannot. White balance is multiplicative in linear RGB, so its
    a/b shift vanishes at black: it is modelled by the L terms and there is no constant offset (a
    constant tints shadows, e.g. turns a dark fireplace blue). g is clamped to [0.85, 1.6]: editors
    boost saturation, they rarely desaturate a whole photo."""
    n = len(r)
    z, L = np.zeros(n), r[:, 0]
    L2 = L * L / 100.0
    Xa = np.column_stack([r[:, 1], L, z, L2, z])
    Xb = np.column_stack([r[:, 2], z, L, z, L2])
    X = np.vstack([Xa, Xb]).astype(np.float32)
    Y = np.concatenate([f[:, 1], f[:, 2]]).astype(np.float32)[:, None]
    p = _huber_lstsq(X, Y, delta=4.0)[:, 0]
    g = float(np.clip(p[0], 0.85, 1.6))
    if g != p[0]:
        rest = _huber_lstsq(X[:, 1:], Y - g * X[:, :1], delta=4.0)[:, 0]
        p = np.concatenate([[g], rest])
    return p.astype(np.float32)


def fit_style(ref_lab: np.ndarray, fin_lab: np.ndarray, valid: np.ndarray, lowfreq_frac: float = 0.04) -> StyleFit:
    ok = valid & (ref_lab[..., 0] > 2) & (ref_lab[..., 0] < 98)
    idx = np.flatnonzero(ok)
    if len(idx) < 500:
        raise ValueError("style fit needs at least 500 overlapping pixels between reference and finished photo")
    rng = np.random.default_rng(0)
    if len(idx) > 200_000:
        idx = rng.choice(idx, 200_000, replace=False)
    r = ref_lab.reshape(-1, 3)[idx]
    f = fin_lab.reshape(-1, 3)[idx]
    # Monotone L curve: binned means of finished L given reference L, then cumulative max.
    bins = np.clip(np.round(r[:, 0] * 10).astype(int), 0, 1000)
    cnt = np.bincount(bins, minlength=1001)
    sm = np.bincount(bins, weights=f[:, 0], minlength=1001)
    has = cnt >= 3
    grid = np.arange(1001)
    lut = np.interp(grid, grid[has], sm[has] / cnt[has]) if has.sum() >= 2 else grid / 10.0
    lut = cv2.GaussianBlur(lut.astype(np.float32).reshape(1, -1), (0, 0), 15).ravel()
    lut = np.maximum.accumulate(lut)
    coef = _fit_ab(r, f)
    return StyleFit(lut.astype(np.float32), coef.astype(np.float32), lowfreq_frac)


def apply_style(ref_lab: np.ndarray, fit: StyleFit, fin_lab: np.ndarray | None = None,
                valid: np.ndarray | None = None) -> np.ndarray:
    """Reference Lab -> predicted finished Lab. With `fin_lab` the low-frequency L residual is added too."""
    out = np.empty_like(ref_lab)
    L = ref_lab[..., 0]
    out[..., 0] = fit.l_lut[np.clip(np.round(L * 10).astype(int), 0, 1000)]
    g, ua, ub, va, vb = (float(v) for v in fit.ab_coef)
    L2 = L * L / 100.0
    out[..., 1] = g * ref_lab[..., 1] + ua * L + va * L2
    out[..., 2] = g * ref_lab[..., 2] + ub * L + vb * L2
    if fin_lab is not None:
        h, w = L.shape
        sigma = max(3.0, fit.lowfreq_sigma * max(h, w))
        v = (valid if valid is not None else np.ones((h, w), bool)).astype(np.float32)
        resid = (fin_lab[..., 0] - out[..., 0]) * v
        # Robust: clip big residuals (actual content changes) before smoothing so they do not leak.
        resid = np.clip(resid, -12, 12)
        low = cv2.GaussianBlur(resid, (0, 0), sigma) / (cv2.GaussianBlur(v, (0, 0), sigma) + 1e-6)
        out[..., 0] += low
    return out


# ---- maps ----------------------------------------------------------------------------------------------
def _local_stats(x: np.ndarray, y: np.ndarray, k: int):
    box = lambda z: cv2.boxFilter(z, cv2.CV_32F, (k, k), borderType=cv2.BORDER_REFLECT)
    mx, my = box(x), box(y)
    vx = np.maximum(box(x * x) - mx * mx, 0)
    vy = np.maximum(box(y * y) - my * my, 0)
    cov = box(x * y) - mx * my
    return np.sqrt(vx), np.sqrt(vy), cov


def _regions(score_map: np.ndarray, kind: str, hi: float, lo: float, min_area: float,
             close_px: int, valid: np.ndarray) -> list[tuple[np.ndarray, float, float]]:
    """Hysteresis threshold -> closing -> connected components. Returns (mask, mean, peak) per region."""
    h, w = score_map.shape
    weak = ((score_map >= lo) & valid).astype(np.uint8)
    if close_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * close_px + 1, 2 * close_px + 1))
        weak = cv2.morphologyEx(weak, cv2.MORPH_CLOSE, k)
        weak = cv2.morphologyEx(weak, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(weak, connectivity=8)
    out = []
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < min_area * h * w:
            continue
        m = lab == i
        vals = score_map[m]
        peak = float(np.percentile(vals, 98))
        if peak < hi:
            continue
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0
        out.append((m, float(vals.mean()), peak))
    return out


def _bbox(m: np.ndarray) -> list[float]:
    ys, xs = np.nonzero(m)
    h, w = m.shape
    return [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]


def _region_stats(m: np.ndarray, fitted_lab: np.ndarray, fin_lab: np.ndarray, fitted_rgb: np.ndarray,
                  fin_rgb: np.ndarray) -> dict:
    fl, rl = fin_lab[m], fitted_lab[m]
    fmed, rmed = np.median(fl, axis=0), np.median(rl, axis=0)
    ys, xs = np.nonzero(m)
    h, w = m.shape
    return {
        "reference_hex": rgb_to_hex(lab_to_rgb(rmed.reshape(1, 1, 3))[0, 0]),
        "finished_hex": rgb_to_hex(lab_to_rgb(fmed.reshape(1, 1, 3))[0, 0]),
        "reference_lab": [round(float(v), 1) for v in rmed],
        "finished_lab": [round(float(v), 1) for v in fmed],
        "reference_chroma": round(float(np.hypot(rmed[1], rmed[2])), 1),
        "finished_chroma": round(float(np.hypot(fmed[1], fmed[2])), 1),
        "reference_hue": round(float(np.degrees(np.arctan2(rmed[2], rmed[1])) % 360), 0),
        "finished_hue": round(float(np.degrees(np.arctan2(fmed[2], fmed[1])) % 360), 0),
        "center": [round(float(xs.mean() / w), 3), round(float(ys.mean() / h), 3)],
    }


def diff(finished: np.ndarray, reference: np.ndarray, valid: np.ndarray,
         exclude: np.ndarray | None = None, min_area: float = 0.0015) -> DiffResult:
    """Compare the registered reference with the finished photo (same size, analysis resolution).

    `valid` marks pixels the reference covers. `exclude` marks pixels where differences are expected and
    should not create regions (e.g. clipped windows the editor legitimately replaces); maps still show them.
    """
    if finished.shape != reference.shape:
        raise ValueError(f"finished {finished.shape} and reference {reference.shape} must match")
    h, w = finished.shape[:2]
    valid = cv2.erode(valid.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    fin_lab, ref_lab = rgb_to_lab(finished), rgb_to_lab(reference)
    fit = fit_style(ref_lab, fin_lab, valid)
    fitted_lab = apply_style(ref_lab, fit, fin_lab, valid)
    fitted_rgb = lab_to_rgb(fitted_lab)

    # Structure from local statistics of L (light blur first so JPEG noise and sharpening do not count).
    Lf = cv2.GaussianBlur(fin_lab[..., 0], (0, 0), 1.2)
    Lr = cv2.GaussianBlur(fitted_lab[..., 0], (0, 0), 1.2)
    k = max(7, round(max(h, w) / 180) | 1)
    sf, sr, cov = _local_stats(Lf, Lr, k)
    floor = 2.5  # L units of local std treated as flat
    missing = np.clip((sr - sf - floor) / (sr + 4.0), 0, 1)
    added = np.clip((sf - sr - floor) / (sf + 4.0), 0, 1)
    ncc = cov / (sf * sr + 1e-3)
    both = np.minimum(sf, sr)
    changed = np.clip((0.5 - ncc) / 0.5, 0, 1) * np.clip((both - floor) / 6.0, 0, 1)

    # Flat appearance change (object interiors): colour difference beyond 15 dE after the fit, scaled.
    de = np.linalg.norm(fin_lab - fitted_lab, axis=2)
    de = cv2.GaussianBlur(de, (0, 0), 2.0)
    flat_change = np.clip((de - 15.0) / 25.0, 0, 1)
    sign = fin_lab[..., 0] - fitted_lab[..., 0]
    # A flat change where the finished photo lost detail counts as missing, otherwise as added.
    missing = np.maximum(missing, flat_change * (sr >= sf))
    added = np.maximum(added, flat_change * (sf > sr))
    del sign

    smooth = lambda z: cv2.GaussianBlur(z.astype(np.float32), (0, 0), max(2.0, k / 2))
    missing, added, changed = smooth(missing), smooth(added), smooth(changed)

    # Chroma drift: the finished photo lost colour the fitted reference has (a blue wall turned grey).
    # Increases are not flagged: editors boost saturation all the time, a neutralised colour is the defect.
    blur = lambda z: cv2.GaussianBlur(z, (0, 0), max(3.0, k))
    fab = np.dstack([blur(fin_lab[..., 1]), blur(fin_lab[..., 2])])
    rab = np.dstack([blur(fitted_lab[..., 1]), blur(fitted_lab[..., 2])])
    dab = fab - rab
    c_ref = np.linalg.norm(rab, axis=2)
    c_fin = np.linalg.norm(fab, axis=2)
    # Colour lost along the reference hue direction.
    along = -(dab * rab).sum(axis=2) / (c_ref + 1e-3)
    lum_ok = (fitted_lab[..., 0] > 15) & (fitted_lab[..., 0] < 97)
    chroma = (np.clip((along - 4.0) / 8.0, 0, 1) * np.clip((c_ref - 6.0) / 6.0, 0, 1)
              * np.clip((c_ref - c_fin) / (0.4 * c_ref + 1e-3), 0, 1) * lum_ok)

    # Flame-like pixels: bright, saturated orange/yellow in the finished photo but not in the reference.
    fC = np.hypot(fin_lab[..., 1], fin_lab[..., 2])
    fH = np.degrees(np.arctan2(fin_lab[..., 2], fin_lab[..., 1])) % 360
    # Thresholds from the AutoHDR fire cases: flames are 60-800 px at 1600 px and these cut clean controls
    # to <= 30 stray pixels (eval/out/qc_defects.json).
    fire_like = ((fin_lab[..., 0] > 55) & (fC > 40) & (fH >= 25) & (fH <= 85)
                 & (fin_lab[..., 0] > fitted_lab[..., 0] + 20)) & valid

    for z in (missing, added, changed, chroma):
        z[~valid] = 0
    region_ok = valid & ~(exclude if exclude is not None else np.zeros_like(valid))
    close = max(3, round(max(h, w) / 160))
    regions: list[DiffRegion] = []
    for kind, mp, hi, lo in (("missing", missing, 0.35, 0.15), ("added", added, 0.35, 0.15),
                             ("changed", changed, 0.45, 0.25), ("chroma", chroma, 0.45, 0.2)):
        for m, mean, peak in _regions(mp, kind, hi, lo, min_area, close, region_ok):
            stats = _region_stats(m, fitted_lab, fin_lab, fitted_rgb, finished)
            stats["chroma_shift"] = round(float(np.linalg.norm(np.median(dab[m], axis=0))), 1)
            stats["chroma_lost"] = round(float(np.median(along[m])), 1)
            stats["fire_frac"] = round(float(fire_like[m].mean()), 3)
            stats["fire_px_frac"] = round(float(fire_like[m].sum()) / (h * w), 6)
            regions.append(DiffRegion(kind, _bbox(m), float(m.mean()), mean, peak, m, stats))
    # Fire gets its own components: flames are too small to survive the area filter of the other maps.
    fire_d = cv2.dilate(fire_like.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    n, lab = cv2.connectedComponents(fire_d, connectivity=8)
    for i in range(1, n):
        m = lab == i
        px = int(fire_like[m].sum())
        if px < FIRE_MIN_PX_FRAC * h * w:
            continue
        stats = _region_stats(m, fitted_lab, fin_lab, fitted_rgb, finished)
        stats.update(fire_px=px, fire_frac=round(px / float(m.sum()), 3), fire_px_frac=round(px / (h * w), 6),
                     chroma_shift=0.0, chroma_lost=0.0)
        strength = float(np.clip(px / (FIRE_MIN_PX_FRAC * h * w * 6), 0.3, 1.0))
        regions.append(DiffRegion("fire", _bbox(m), float(m.mean()), strength, strength, m, stats))
    regions.sort(key=lambda r: -(r.score * np.sqrt(r.area)))
    gstats = {
        "fit": fit.to_dict(),
        "median_de": round(float(np.median(de[valid])), 2),
        "valid_frac": round(float(valid.mean()), 3),
    }
    return DiffResult(fitted_rgb, missing, added, changed, chroma, regions, fit, gstats)


def heatmap(finished: np.ndarray, res: DiffResult, alpha: float = 0.6) -> np.ndarray:
    """Finished photo dimmed to grey with missing (cyan), added (magenta), changed (yellow) and chroma
    (orange) overlaid; region boxes drawn. For the agent and the UI."""
    base = cv2.cvtColor(cv2.cvtColor(finished, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB).astype(np.float32) * 0.55
    layers = [(res.missing, (0, 220, 255)), (res.added, (255, 0, 200)), (res.changed, (255, 230, 0)),
              (res.chroma, (255, 120, 0))]
    out = base
    for mp, col in layers:
        a = (np.clip(mp, 0, 1) * alpha)[..., None]
        out = out * (1 - a) + np.array(col, np.float32) * a
    out = np.clip(out, 0, 255).astype(np.uint8)
    h, w = out.shape[:2]
    for r in res.regions:
        x0, y0, x1, y1 = (int(r.bbox[0] * w), int(r.bbox[1] * h), int(r.bbox[2] * w) - 1, int(r.bbox[3] * h) - 1)
        col = {"missing": (0, 220, 255), "added": (255, 0, 200), "changed": (255, 230, 0), "chroma": (255, 120, 0),
               "fire": (255, 40, 40)}[r.kind]
        cv2.rectangle(out, (x0, y0), (x1, y1), col, max(1, w // 600), cv2.LINE_AA)
    return out
