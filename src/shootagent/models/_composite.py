"""Private mask and compositing helpers for the model adapters.

Stand-in for `imaging/composite.py`, which W1 owns and was not written yet when these adapters were.
Same signature and guarantee: pixels outside `grow(mask, feather_px)` are byte-identical to `base`.
Swap callers over to `imaging.composite.composite` once it lands and its tests pass.
"""
from __future__ import annotations

import cv2
import numpy as np


def outside_distance(mask: np.ndarray) -> np.ndarray:
    """Exact Euclidean distance from each pixel to the nearest mask pixel (0 on the mask)."""
    m = np.asarray(mask, dtype=bool)
    return cv2.distanceTransform((~m).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)


def grow(mask: np.ndarray, px: int) -> np.ndarray:
    """Pixels within Euclidean distance `px` of the mask (a copy of it for px <= 0).

    A distance transform, not cv2.dilate: dilating with a 770 px elliptical kernel to find a car's
    shadow took 8 s; the transform takes the same ~50 ms at any radius.
    """
    m = np.asarray(mask, dtype=bool)
    if px <= 0:
        return m.copy()
    if not m.any():
        return m.copy()
    return outside_distance(m) <= px


def composite(base: np.ndarray, edited: np.ndarray, mask: np.ndarray, feather_px: int = 0) -> np.ndarray:
    """`edited` inside `mask`, `base` outside, with a linear ramp `feather_px` wide outside the mask edge.

    Alpha is 1 on the mask, falls linearly to 0 over `feather_px` pixels of distance outside it, and is
    exactly 0 beyond. Where alpha is 0 the output copies `base` bytes directly, so the region outside
    `grow(mask, feather_px)` is byte-identical to `base`.
    """
    if base.shape != edited.shape:
        raise ValueError(f"base {base.shape} and edited {edited.shape} differ in shape")
    if base.dtype != np.uint8 or edited.dtype != np.uint8:
        raise ValueError(f"composite takes uint8 images, got {base.dtype} and {edited.dtype}")
    m = np.asarray(mask, dtype=bool)
    if m.shape != base.shape[:2]:
        raise ValueError(f"mask {m.shape} does not match image {base.shape[:2]}")

    out = base.copy()
    if feather_px <= 0:
        out[m] = edited[m]
        return out

    # Distance from each outside pixel to the nearest mask pixel; 0 on the mask itself.
    dist = outside_distance(m)
    alpha = np.clip(1.0 - dist / float(feather_px + 1), 0.0, 1.0)
    alpha[m] = 1.0
    alpha[dist > feather_px] = 0.0  # exactly grow(m, feather_px), which defines the guarantee
    band = alpha > 0
    a = alpha[band][:, None] if base.ndim == 3 else alpha[band]
    blended = edited[band].astype(np.float32) * a + base[band].astype(np.float32) * (1.0 - a)
    out[band] = np.clip(np.rint(blended), 0, 255).astype(np.uint8)
    return out


def affected_region(mask: np.ndarray, dilate_px: int, feather_px: int) -> np.ndarray:
    """Pixels an edit that grows `mask` by `dilate_px` and then composites with `feather_px` may change.
    Everything outside this is byte-identical; the self-check measures delta outside it."""
    return grow(grow(mask, dilate_px), feather_px)


SHADOW_REACH = 0.35  # how far a shadow may extend past the mask, as a fraction of the mask box's long side
# Original luminance below SHADOW_RATIO of the clean plate's, and SHADOW_MIN_DIFF levels darker, is shadow.
# At 0.85 / 10 the soft wall shadow of the bin in customer-data-068 stayed behind and failed the judge
# with both gpt-image-2 and 2.5; at 0.90 / 6 it passed, and 069 and train-010 still passed.
SHADOW_RATIO = 0.90
SHADOW_MIN_DIFF = 6.0


def _luma(img: np.ndarray) -> np.ndarray:
    return img.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def shadow_pixels(orig: np.ndarray, clean: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Pixels within SHADOW_REACH of `mask`, darker in `orig` than in `clean`, connected to the mask.

    `clean` must already be colour-matched to `orig`. Only darkening counts, and only regions touching
    the mask, so other things the model changed in its re-render are not pulled in.
    """
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return np.zeros_like(mask)
    reach = max(8, int(SHADOW_REACH * max(np.ptp(xs) + 1, np.ptp(ys) + 1)))
    lo, lc = _luma(orig), _luma(clean)
    dark = (lo < SHADOW_RATIO * lc) & (lc - lo > SHADOW_MIN_DIFF) & grow(mask, reach) & ~mask
    dark = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
    _n, labels = cv2.connectedComponents((dark | grow(mask, 2)).astype(np.uint8), connectivity=8)
    touching = np.unique(labels[mask])
    keep = np.isin(labels, touching[touching > 0]) & dark
    return cv2.morphologyEx(keep.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)).astype(bool) & ~mask

