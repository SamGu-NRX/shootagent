"""Mask compositing with a hard guarantee, outside-mask metrics, and restoring regions from the brackets.

`composite` is how every local edit lands: pixels farther than `feather_px` outside the mask are copied
from the base untouched, so the self-check's outside-mask delta is exactly 0 there.
"""
from __future__ import annotations

import cv2
import numpy as np

from .color import lab_to_rgb, rgb_to_lab


def _check_pair(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> None:
    if a.shape != b.shape:
        raise ValueError(f"image shapes differ: {a.shape} vs {b.shape}")
    if mask is not None and mask.shape != a.shape[:2]:
        raise ValueError(f"mask shape {mask.shape} does not match image {a.shape[:2]}; resize the mask first")


def _disk(r: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def feather_support(mask: np.ndarray, feather_px: int) -> np.ndarray:
    """The pixels a feathered composite may change: the mask dilated by `feather_px`."""
    m = mask.astype(bool)
    if feather_px <= 0:
        return m
    return cv2.dilate(m.astype(np.uint8), _disk(int(feather_px))) > 0


def feather_alpha(mask: np.ndarray, feather_px: int) -> np.ndarray:
    """Float alpha 0..1: 1 inside the mask, falling smoothly to 0 at `feather_px` outside it, and exactly
    0 beyond the support."""
    m = mask.astype(bool)
    if feather_px <= 0:
        return m.astype(np.float32)
    # Distance outside the mask -> smoothstep ramp. Inside stays 1 so masked edits land fully.
    dist = cv2.distanceTransform((~m).astype(np.uint8), cv2.DIST_L2, 5)
    t = np.clip(1.0 - dist / float(feather_px), 0.0, 1.0)
    alpha = t * t * (3 - 2 * t)
    alpha[~feather_support(m, feather_px)] = 0.0
    alpha[m] = 1.0
    return alpha.astype(np.float32)


def composite(base: np.ndarray, edited: np.ndarray, mask: np.ndarray, feather_px: int = 0) -> np.ndarray:
    """Blend `edited` into `base` through `mask` with a soft edge of `feather_px`.

    Guarantee: every pixel outside `feather_support(mask, feather_px)` is byte-identical to `base`."""
    _check_pair(base, edited, mask)
    alpha = feather_alpha(mask, feather_px)
    out = base.copy()
    sel = alpha > 0
    a = alpha[sel][:, None]
    out[sel] = np.clip(np.round(base[sel].astype(np.float32) * (1 - a) + edited[sel].astype(np.float32) * a), 0, 255
                       ).astype(np.uint8)
    return out


def outside_delta(before: np.ndarray, after: np.ndarray, mask: np.ndarray | None) -> dict:
    """How much changed outside `mask` (pass the feather support to measure the guarantee).

    Returns max and mean absolute channel difference and the fraction of outside pixels that changed."""
    _check_pair(before, after, mask)
    d = np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=-1) if before.ndim == 3 else \
        np.abs(before.astype(np.int16) - after.astype(np.int16))
    outside = ~mask.astype(bool) if mask is not None else np.ones(d.shape, bool)
    if not outside.any():
        return {"max": 0, "mean": 0.0, "changed_frac": 0.0, "outside_px": 0}
    vals = d[outside]
    return {"max": int(vals.max()), "mean": round(float(vals.mean()), 4),
            "changed_frac": round(float((vals > 0).mean()), 6), "outside_px": int(outside.sum())}


def _ring(mask: np.ndarray, inner: int, outer: int) -> np.ndarray:
    m = mask.astype(np.uint8)
    return (cv2.dilate(m, _disk(outer)) > 0) & ~(cv2.dilate(m, _disk(inner)) > 0)


def match_patch_style(patch_src: np.ndarray, src: np.ndarray, dst: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Map colours of `src` (e.g. the bracket reference) to look like `dst` (the finished photo), fitted on
    `ring` pixels where both show the same content, and apply the map to `patch_src`.

    The map is deliberately small: L' = gL*L + oL and ab' = g*ab + o with one chroma gain. A full 3x4
    Lab matrix fitted on a ring of blue siding turned a restored red stop sign grey, because it
    extrapolates to colours the ring does not contain."""
    s, d = rgb_to_lab(src)[ring].astype(np.float64), rgb_to_lab(dst)[ring].astype(np.float64)
    if len(s) < 50:
        raise ValueError(f"only {len(s)} pixels around the mask to learn the photo's colour grade from")
    XL = np.column_stack([s[:, 0], np.ones(len(s))])
    (gL, oL), *_ = np.linalg.lstsq(XL, d[:, 0], rcond=None)
    gL = float(np.clip(gL, 0.5, 2.0))
    oL = float(np.median(d[:, 0] - gL * s[:, 0]))
    sab, dab = s[:, 1:], d[:, 1:]
    sc = sab - sab.mean(axis=0)
    g = float(np.clip((sc * (dab - dab.mean(axis=0))).sum() / max((sc * sc).sum(), 1e-6), 0.8, 1.4))
    o = np.median(dab - g * sab, axis=0)
    p = rgb_to_lab(patch_src)
    out = np.empty_like(p)
    out[..., 0] = gL * p[..., 0] + oL
    out[..., 1:] = g * p[..., 1:] + o.astype(np.float32)
    return lab_to_rgb(out)


def restore_from_reference(finished: np.ndarray, reference_aligned: np.ndarray, mask: np.ndarray,
                           feather_px: int = 6, valid: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Put the brackets' content back inside `mask`, styled like the finished photo.

    1. Fit the finished photo's local grade from a ring around the mask (Lab affine) and apply it to the
       reference.
    2. Remove the remaining seam: the finished-minus-restyled offset measured on the ring is spread into
       the mask by normalised Gaussian interpolation (a cheap membrane fill), so edges meet without
       washing out the restored detail.
    3. Composite with a feathered edge.
    Returns (image, info) with info = {ring_px, seam_before, seam_after} (mean Lab dE on the ring)."""
    _check_pair(finished, reference_aligned, mask)
    m = mask.astype(bool)
    if not m.any():
        raise ValueError("restore mask is empty")
    if valid is not None:
        uncovered = float((m & ~valid).sum() / m.sum())
        if uncovered > 0.05:
            raise ValueError(f"{uncovered:.0%} of the mask is outside the brackets' coverage; cannot restore there")
    size = int(np.sqrt(m.sum()))
    inner = max(2, feather_px)
    outer = inner + max(6, size // 6)
    ring = _ring(m, inner, outer)
    if valid is not None:
        ring &= valid
    # Work on the bounding box of the outer ring only.
    ys, xs = np.nonzero(cv2.dilate(m.astype(np.uint8), _disk(outer + 2)))
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    fin_c, ref_c, ring_c = finished[y0:y1, x0:x1], reference_aligned[y0:y1, x0:x1], ring[y0:y1, x0:x1]
    styled = match_patch_style(ref_c, ref_c, fin_c, ring_c)
    fin_lab, sty_lab = rgb_to_lab(fin_c), rgb_to_lab(styled)
    off = fin_lab - sty_lab
    seam_before = float(np.linalg.norm(off[ring_c], axis=1).mean())
    sigma = max(3.0, size / 4)
    wgt = cv2.GaussianBlur(ring_c.astype(np.float32), (0, 0), sigma) + 1e-6
    fill = np.dstack([cv2.GaussianBlur(np.clip(off[..., c], -15, 15) * ring_c, (0, 0), sigma) / wgt for c in range(3)])
    # The seam is mostly lightness; spreading the ring's colour offset into the patch tints the restored
    # object with its surroundings (white letters on a stop sign turned cyan next to blue siding).
    fill[..., 1:] *= 0.25
    corrected_lab = sty_lab + fill
    corrected = lab_to_rgb(corrected_lab)
    seam_after = float(np.linalg.norm((fin_lab - rgb_to_lab(corrected))[ring_c], axis=1).mean())
    patch = finished.copy()
    patch[y0:y1, x0:x1] = corrected
    out = composite(finished, patch, m, feather_px)
    return out, {"ring_px": int(ring.sum()), "seam_before": round(seam_before, 2), "seam_after": round(seam_after, 2)}
