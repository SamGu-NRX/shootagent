"""Register the bracket reference onto the finished photo.

AutoHDR's finished photos are cropped and lens/perspective corrected, so a homography alone leaves
residual warps of several pixels near the edges. Registration is a SIFT homography (exposure-
normalised features) followed by a dense DIS optical flow on CLAHE-equalised luma that is smoothed
heavily (sigma = 1/12 of the width). The smoothing keeps lens-style low-frequency warps and refuses
object-sized ones, so a removed lamp stays a difference instead of being warped away.

`Registration.warp` applies the result at any resolution, so analysis can register at ~1600 px and a
fix can warp a 4k reference onto the full-size finished photo.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import features as F
from .color import equalized_gray

FLOW_LONG_EDGE = 640
FLOW_SMOOTH_FRAC = 1 / 12


@dataclass
class Registration:
    H: np.ndarray  # 3x3, src pixel -> dst pixel at (src_wh, dst_wh)
    src_wh: tuple[int, int]
    dst_wh: tuple[int, int]
    flow: np.ndarray | None  # (h, w, 2) residual flow in units of dst width/height, at low resolution
    inliers: int
    corner_shift: float
    ncc_before: float  # gradient-magnitude correlation after the homography only
    ncc_after: float  # ... after the residual flow (equal to ncc_before when flow was rejected)

    @property
    def ok(self) -> bool:
        return self.inliers >= 20

    def to_dict(self) -> dict:
        return {"inliers": self.inliers, "corner_shift": round(self.corner_shift, 4),
                "ncc_homography": round(self.ncc_before, 3), "ncc_final": round(self.ncc_after, 3),
                "flow_used": self.flow is not None,
                "flow_max_px": round(self.flow_max_px(), 1) if self.flow is not None else 0.0}

    def flow_max_px(self) -> float:
        if self.flow is None:
            return 0.0
        return float(np.abs(self.flow * np.array(self.dst_wh, np.float32)).max())

    def warp(self, src: np.ndarray, dst_wh: tuple[int, int] | None = None, interp=cv2.INTER_LINEAR
             ) -> tuple[np.ndarray, np.ndarray]:
        """Warp `src` (any resolution of the registered source) into the dst frame at `dst_wh`
        (default: the registered dst size). Returns (warped, valid bool mask)."""
        dst_wh = dst_wh or self.dst_wh
        H = np.diag([dst_wh[0] / self.dst_wh[0], dst_wh[1] / self.dst_wh[1], 1.0]) @ self.H @ np.diag(
            [self.src_wh[0] / src.shape[1], self.src_wh[1] / src.shape[0], 1.0])
        is_bool = src.dtype == bool
        s = src.astype(np.uint8) * 255 if is_bool else src
        ones = np.full(s.shape[:2], 255, np.uint8)
        if self.flow is None:
            warped = cv2.warpPerspective(s, H, dst_wh, flags=interp, borderMode=cv2.BORDER_CONSTANT)
            valid = cv2.warpPerspective(ones, H, dst_wh, flags=cv2.INTER_NEAREST) > 0
        else:
            # Compose homography and flow into one remap: dst x -> x + flow(x) in homography space -> src.
            w, h = dst_wh
            fl = cv2.resize(self.flow, (w, h), interpolation=cv2.INTER_LINEAR)
            gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
            px = gx + fl[..., 0] * w
            py = gy + fl[..., 1] * h
            Hi = np.linalg.inv(H)
            den = Hi[2, 0] * px + Hi[2, 1] * py + Hi[2, 2]
            mx = ((Hi[0, 0] * px + Hi[0, 1] * py + Hi[0, 2]) / den).astype(np.float32)
            my = ((Hi[1, 0] * px + Hi[1, 1] * py + Hi[1, 2]) / den).astype(np.float32)
            warped = cv2.remap(s, mx, my, interp, borderMode=cv2.BORDER_CONSTANT)
            valid = cv2.remap(ones, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT) > 0
        if is_bool:
            warped = warped > 127
        return warped, valid


def grad_ncc(a_u8: np.ndarray, b_u8: np.ndarray, valid: np.ndarray | None = None) -> float:
    """Correlation of gradient magnitudes of two equalised grayscale images (alignment quality)."""
    def gm(x):
        x = cv2.GaussianBlur(x.astype(np.float32), (0, 0), 1.0)
        return cv2.magnitude(cv2.Sobel(x, cv2.CV_32F, 1, 0), cv2.Sobel(x, cv2.CV_32F, 0, 1))
    ga, gb = gm(a_u8), gm(b_u8)
    if valid is not None:
        ga, gb = ga[valid], gb[valid]
    ga = ga - ga.mean()
    gb = gb - gb.mean()
    den = float(np.sqrt((ga * ga).sum() * (gb * gb).sum()))
    return float((ga * gb).sum() / den) if den > 0 else 0.0


def register(src: np.ndarray, dst: np.ndarray, use_flow: bool = True,
             src_feats: F.Features | None = None, dst_feats: F.Features | None = None) -> Registration:
    """Register `src` (e.g. the bracket fusion) onto `dst` (the finished photo). Raises ValueError when
    no consistent homography exists (different scenes)."""
    fa = src_feats or F.detect(src)
    fb = dst_feats or F.detect(dst)
    m = F.match(fa, fb)
    if m.H is None or m.inliers < 20:
        raise ValueError(f"cannot register: only {m.inliers} consistent feature matches between the images")
    H = F.scale_homography(m.H, fa.scale_for(src.shape[1]), fb.scale_for(dst.shape[1]))
    reg = Registration(H, (src.shape[1], src.shape[0]), (dst.shape[1], dst.shape[0]), None,
                       m.inliers, m.corner_shift, 0.0, 0.0)

    # Residual flow at low resolution.
    s = min(1.0, FLOW_LONG_EDGE / max(dst.shape[:2]))
    lw, lh = round(dst.shape[1] * s), round(dst.shape[0] * s)
    dst_small = cv2.resize(dst, (lw, lh), interpolation=cv2.INTER_AREA)
    src_small = cv2.resize(src, (round(src.shape[1] * s), round(src.shape[0] * s)), interpolation=cv2.INTER_AREA)
    warped, valid = reg.warp(src_small, (lw, lh))
    gd, gw = equalized_gray(dst_small), equalized_gray(warped)
    reg.ncc_before = reg.ncc_after = grad_ncc(gd, gw, valid)
    if not use_flow:
        return reg
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    flow = dis.calc(gd, gw, None)  # dst(x) ~ warped(x + flow(x))
    flow[~valid] = 0
    sigma = max(2.0, lw * FLOW_SMOOTH_FRAC)
    wgt = cv2.GaussianBlur(valid.astype(np.float32), (0, 0), sigma) + 1e-6
    flow = np.dstack([cv2.GaussianBlur(flow[..., c] * valid, (0, 0), sigma) / wgt for c in range(2)])
    flow_norm = (flow / np.array([lw, lh], np.float32)).astype(np.float32)
    candidate = Registration(H, reg.src_wh, reg.dst_wh, flow_norm, m.inliers, m.corner_shift, reg.ncc_before, 0.0)
    warped2, valid2 = candidate.warp(src_small, (lw, lh))
    candidate.ncc_after = grad_ncc(gd, equalized_gray(warped2), valid2 & valid)
    if candidate.ncc_after > reg.ncc_before + 0.005:
        return candidate
    return reg
