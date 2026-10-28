"""Colour-space helpers shared by the imaging modules."""
from __future__ import annotations

import cv2
import numpy as np


def srgb_to_linear(img_u8: np.ndarray) -> np.ndarray:
    x = img_u8.astype(np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(lin: np.ndarray) -> np.ndarray:
    x = np.clip(lin, 0.0, 1.0)
    y = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.clip(np.round(y * 255.0), 0, 255).astype(np.uint8)


def luma(img_u8: np.ndarray) -> np.ndarray:
    """Rec.601 luma as float32 0..255 (what most JPEG pipelines call Y)."""
    return cv2.cvtColor(img_u8, cv2.COLOR_RGB2GRAY).astype(np.float32)


def rgb_to_lab(img_u8: np.ndarray) -> np.ndarray:
    """CIE Lab float32: L 0..100, a/b roughly -128..127 (D65, sRGB input)."""
    return cv2.cvtColor(img_u8.astype(np.float32) / 255.0, cv2.COLOR_RGB2Lab)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_Lab2RGB)
    return np.clip(np.round(rgb * 255.0), 0, 255).astype(np.uint8)


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    s = hex_str.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"not a hex colour: {hex_str!r} (expected #rrggbb)")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(np.clip(round(float(c)), 0, 255)) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_lab(hex_str: str) -> np.ndarray:
    px = np.array([[hex_to_rgb(hex_str)]], dtype=np.uint8)
    return rgb_to_lab(px)[0, 0]


def delta_e(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIE76 colour difference."""
    return np.linalg.norm(np.asarray(lab1, np.float32) - np.asarray(lab2, np.float32), axis=-1)


def equalized_gray(img_u8: np.ndarray, clip: float = 3.0, tiles: int = 8) -> np.ndarray:
    """Exposure-normalised grayscale for feature matching: CLAHE on luma. Makes a dark and a bright
    bracket of the same scene look alike enough for SIFT."""
    g = cv2.cvtColor(img_u8, cv2.COLOR_RGB2GRAY)
    return cv2.createCLAHE(clipLimit=clip, tileGridSize=(tiles, tiles)).apply(g)
