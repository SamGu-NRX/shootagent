"""SIFT features on exposure-normalised images, pairwise matching and robust homographies.

Brackets of one scene differ by several stops, so features are computed on CLAHE-equalised luma
(`color.equalized_gray`), which makes a dark and a bright frame of the same view share most keypoints.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .color import equalized_gray

MATCH_LONG_EDGE = 1024  # features are computed at this long edge; homographies are returned in these coords


@dataclass
class Features:
    keypoints: np.ndarray  # (N, 2) float32 xy at feature resolution
    descriptors: np.ndarray | None  # (N, 128) float32
    scale: float  # feature px = original px * scale
    size_wh: tuple[int, int]  # feature image (W, H)

    def scale_for(self, width: int) -> float:
        """Feature px per px of an image of this content that is `width` pixels wide."""
        return self.size_wh[0] / width


@dataclass
class PairMatch:
    good: int  # matches passing the ratio test
    inliers: int  # RANSAC homography inliers
    inlier_ratio: float  # inliers / good
    H: np.ndarray | None  # 3x3, maps points of image A to image B, in feature coordinates
    corner_shift: float  # mean corner displacement of H as a fraction of the image diagonal
    reproj_px: float  # median reprojection error of inliers, feature px
    pts_a: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), np.float32), repr=False)
    pts_b: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), np.float32), repr=False)

    def to_dict(self) -> dict:
        return {
            "good": self.good,
            "inliers": self.inliers,
            "inlier_ratio": round(self.inlier_ratio, 3),
            "corner_shift": round(self.corner_shift, 4) if np.isfinite(self.corner_shift) else None,
            "reproj_px": round(self.reproj_px, 2) if np.isfinite(self.reproj_px) else None,
        }


_sift = None


def _get_sift():
    global _sift
    if _sift is None:
        # 2500 keypoints: brute-force matching is quadratic, and 15-bracket sets spent 9.5 s grouping at 4000.
        _sift = cv2.SIFT_create(nfeatures=2500, contrastThreshold=0.02)
    return _sift


def detect(img: np.ndarray, long_edge: int = MATCH_LONG_EDGE) -> Features:
    h, w = img.shape[:2]
    scale = min(1.0, long_edge / max(h, w))
    small = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else img
    gray = equalized_gray(small)
    kps, desc = _get_sift().detectAndCompute(gray, None)
    pts = np.array([k.pt for k in kps], dtype=np.float32).reshape(-1, 2)
    if len(pts):
        # SIFT runs multi-threaded and returns keypoints in varying order; sort so matching and RANSAC
        # sampling are reproducible.
        sizes = np.array([k.size for k in kps], dtype=np.float32)
        order = np.lexsort((sizes, pts[:, 0], pts[:, 1]))
        pts, desc = pts[order], desc[order]
    return Features(pts, desc, scale, (small.shape[1], small.shape[0]))


def _corner_shift(H: np.ndarray, size_wh: tuple[int, int]) -> float:
    w, h = size_wh
    c = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32).reshape(-1, 1, 2)
    m = cv2.perspectiveTransform(c, H).reshape(-1, 2)
    return float(np.linalg.norm(m - c.reshape(-1, 2), axis=1).mean() / np.hypot(w, h))


_bf = cv2.BFMatcher(cv2.NORM_L2)


def match(a: Features, b: Features, ratio: float = 0.75, ransac_px: float = 3.0) -> PairMatch:
    """Ratio-test SIFT matches A->B, then a RANSAC homography."""
    if a.descriptors is None or b.descriptors is None or len(a.keypoints) < 8 or len(b.keypoints) < 8:
        return PairMatch(0, 0, 0.0, None, float("inf"), float("inf"))
    knn = _bf.knnMatch(a.descriptors, b.descriptors, k=2)
    good = [m[0] for m in knn if len(m) == 2 and m[0].distance < ratio * m[1].distance]
    if len(good) < 8:
        return PairMatch(len(good), 0, 0.0, None, float("inf"), float("inf"))
    pa = a.keypoints[[m.queryIdx for m in good]]
    pb = b.keypoints[[m.trainIdx for m in good]]
    # Seeded RANSAC: USAC_MAGSAC gave different homographies run to run on the same input (it samples in
    # parallel), which flipped borderline QC decisions between identical runs.
    cv2.setRNGSeed(1234)
    H, inl = cv2.findHomography(pa, pb, cv2.RANSAC, ransac_px, maxIters=5000, confidence=0.999)
    if H is None or inl is None or not _plausible(H):
        return PairMatch(len(good), 0, 0.0, None, float("inf"), float("inf"))
    inl = inl.ravel().astype(bool)
    n = int(inl.sum())
    if n < 4:
        return PairMatch(len(good), n, n / len(good), None, float("inf"), float("inf"))
    proj = cv2.perspectiveTransform(pa[inl].reshape(-1, 1, 2), H).reshape(-1, 2)
    err = float(np.median(np.linalg.norm(proj - pb[inl], axis=1))) if n else float("inf")
    return PairMatch(len(good), n, n / len(good), H, _corner_shift(H, a.size_wh), err, pa[inl], pb[inl])


def _plausible(H: np.ndarray) -> bool:
    """Reject degenerate fits (collapsed, mirrored or exploding) that RANSAC returns on junk matches."""
    det = float(np.linalg.det(H[:2, :2]))
    return np.isfinite(H).all() and 0.05 < det < 20.0 and abs(float(np.linalg.det(H))) > 1e-9


def scale_homography(H: np.ndarray, scale_src: float, scale_dst: float) -> np.ndarray:
    """Convert H from feature coords (src*scale_src -> dst*scale_dst) to original pixel coords."""
    S_src = np.diag([scale_src, scale_src, 1.0])
    S_dst_inv = np.diag([1.0 / scale_dst, 1.0 / scale_dst, 1.0])
    return S_dst_inv @ H @ S_src
