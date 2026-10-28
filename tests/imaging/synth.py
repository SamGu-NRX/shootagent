"""Synthetic scenes with one correct answer: textured "rooms" rendered as exposure brackets."""
from __future__ import annotations

import cv2
import numpy as np

from shootagent.imaging.color import linear_to_srgb, srgb_to_linear


def scene(seed: int, w: int = 960, h: int = 640) -> np.ndarray:
    """Linear-light RGB float32 scene, values ~0.02..0.6, with plenty of corners for feature matching."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.float32)
    img[:] = rng.uniform(0.15, 0.35, 3)
    for _ in range(60):
        x0, y0 = int(rng.integers(0, w - 40)), int(rng.integers(0, h - 40))
        x1, y1 = x0 + int(rng.integers(20, 200)), y0 + int(rng.integers(20, 160))
        cv2.rectangle(img, (x0, y0), (x1, y1), rng.uniform(0.02, 0.6, 3).tolist(), -1)
    for _ in range(25):
        c = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        cv2.circle(img, c, int(rng.integers(5, 40)), rng.uniform(0.02, 0.6, 3).tolist(), -1)
    noise = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.5)
    img *= (1 + 0.08 * noise)[..., None]
    return np.clip(img, 0.005, 1.0)


def expose(lin: np.ndarray, ev: float) -> np.ndarray:
    return linear_to_srgb(lin * (2.0 ** ev))


def shift(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def grade(img_u8: np.ndarray, gamma: float = 0.8, warm: float = 1.06) -> np.ndarray:
    """A global 'editor look': brighter midtones and a slight warm white balance."""
    lin = srgb_to_linear(img_u8) ** gamma
    lin = lin * np.array([warm, 1.0, 1 / warm], np.float32)
    return linear_to_srgb(lin)
