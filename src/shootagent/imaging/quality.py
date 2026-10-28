"""Per-frame quality metrics (a port of BaseScanning's FrameQuality).

sharpness: variance of the 4-neighbour Laplacian of luma (higher = sharper)
mean_luma: mean luma 0..255
clipped:   fraction of pixels with luma >= 250
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass
class FrameQuality:
    sharpness: float
    mean_luma: float
    clipped: float
    dark: float  # fraction of pixels with luma <= 5

    def to_dict(self) -> dict:
        return asdict(self)


_LAPLACE4 = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)


def frame_quality(img: np.ndarray) -> FrameQuality:
    if img.ndim != 3 or img.shape[2] != 3 or img.dtype != np.uint8:
        raise ValueError(f"frame_quality expects RGB uint8 (H, W, 3), got {img.dtype} {img.shape}")
    y = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    lap = cv2.filter2D(y.astype(np.float32), cv2.CV_32F, _LAPLACE4, borderType=cv2.BORDER_REPLICATE)
    return FrameQuality(
        sharpness=float(lap.var()),
        mean_luma=float(y.mean()),
        clipped=float((y >= 250).mean()),
        dark=float((y <= 5).mean()),
    )
