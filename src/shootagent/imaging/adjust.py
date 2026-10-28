"""Global or masked tone and colour adjustments, recolouring, and colour sampling.

Exposure and white balance operate in linear light (what a camera raw converter does); recolour works
in CIE Lab so the surface keeps its luminance texture.
"""
from __future__ import annotations

import numpy as np

from .color import hex_to_lab, lab_to_rgb, linear_to_srgb, rgb_to_hex, rgb_to_lab, srgb_to_linear
from .composite import composite

_LUM = np.array([0.2126, 0.7152, 0.0722], np.float32)


def _land(img: np.ndarray, edited: np.ndarray, mask: np.ndarray | None, feather_px: int) -> np.ndarray:
    return edited if mask is None else composite(img, edited, mask, feather_px)


def exposure(img: np.ndarray, ev: float, mask: np.ndarray | None = None, feather_px: int = 0) -> np.ndarray:
    """Multiply linear light by 2**ev. Highlights clip at white, as in any exposure change."""
    if not -5 <= ev <= 5:
        raise ValueError(f"ev {ev} out of range [-5, 5]")
    out = linear_to_srgb(srgb_to_linear(img) * (2.0 ** ev))
    return _land(img, out, mask, feather_px)


def white_balance(img: np.ndarray, temp: float = 0.0, tint: float = 0.0, mask: np.ndarray | None = None,
                  feather_px: int = 0) -> np.ndarray:
    """temp/tint in -100..100 (Lightroom-like direction: +temp warmer, +tint more magenta).

    Applied as per-channel gains in linear light (+100 temp = +-0.5 stop on red/blue), renormalised so
    luminance is unchanged."""
    if not (-100 <= temp <= 100 and -100 <= tint <= 100):
        raise ValueError(f"temp {temp} / tint {tint} must be within -100..100")
    lin = srgb_to_linear(img)
    t, g = temp / 100 * 0.5, tint / 100 * 0.5
    gains = np.array([2 ** t, 2 ** -g, 2 ** -t], np.float32)
    gains /= float(gains @ _LUM)
    out = linear_to_srgb(lin * gains)
    return _land(img, out, mask, feather_px)


def recolor(img: np.ndarray, mask: np.ndarray, target_hex: str, match_lightness: bool = False,
            strength: float = 1.0, feather_px: int = 3) -> np.ndarray:
    """Shift the masked surface's colour to `target_hex`, keeping its texture and shading.

    In Lab, every masked pixel's a/b is moved by (target - mean); with `match_lightness` the L channel is
    shifted the same way, otherwise shading and lightness stay as photographed. `strength` 0..1 blends.
    The mean colour inside the mask lands on the target (within gamut clipping)."""
    m = mask.astype(bool)
    if not m.any():
        raise ValueError("recolor mask is empty")
    if not 0 <= strength <= 1:
        raise ValueError(f"strength {strength} must be within 0..1")
    lab = rgb_to_lab(img)
    target = hex_to_lab(target_hex)
    src = lab[m]
    goal = src.mean(axis=0) + (target - src.mean(axis=0)) * strength
    if not match_lightness:
        goal[0] = src[:, 0].mean()
    # Shift, then correct the residual twice: pixels pushed out of the sRGB gamut get clipped, which
    # moves the mean off target (saturated reds miss by ~3 dE after one pass).
    shift = goal - src.mean(axis=0)
    for _ in range(3):
        px = lab_to_rgb((src + shift).reshape(-1, 1, 3))
        got = rgb_to_lab(px).reshape(-1, 3).mean(axis=0)
        shift += goal - got
        if not match_lightness:
            shift[0] = 0.0
    out = img.copy()
    out[m] = px.reshape(-1, 3)
    return composite(img, out, m, feather_px)


def sample_hex(img: np.ndarray, mask: np.ndarray) -> str:
    """Median colour inside the mask (median in Lab, robust to highlights and texture)."""
    m = mask.astype(bool)
    if not m.any():
        raise ValueError("sample mask is empty")
    med = np.median(rgb_to_lab(img)[m], axis=0)
    return rgb_to_hex(lab_to_rgb(med.reshape(1, 1, 3))[0, 0])


def sample_lab(img: np.ndarray, mask: np.ndarray) -> list[float]:
    m = mask.astype(bool)
    if not m.any():
        raise ValueError("sample mask is empty")
    return [round(float(v), 2) for v in np.median(rgb_to_lab(img)[m], axis=0)]


def sample_bracket_hex(aligned_brackets: list[np.ndarray], evs: list[float], mask: np.ndarray,
                       target_ev: float = 0.0) -> str:
    """Exposure-normalised colour of a surface from the brackets.

    Each aligned bracket's linear pixels inside the mask are scaled to `target_ev`; pixels that are clipped
    or crushed in a bracket are ignored, and the per-pixel median across brackets is taken, then the
    median over the mask. This is the surface's colour as the camera saw it, free of the edit."""
    m = mask.astype(bool)
    if not m.any():
        raise ValueError("sample mask is empty")
    if len(aligned_brackets) != len(evs):
        raise ValueError("need one EV per bracket")
    vals = []
    for im, ev in zip(aligned_brackets, evs):
        px = im[m].astype(np.int16)
        ok = (px.max(axis=1) < 250) & (px.min(axis=1) > 5)
        lin = srgb_to_linear(im[m]) * (2.0 ** (target_ev - ev))
        lin[~ok] = np.nan
        vals.append(lin)
    stack = np.stack(vals)  # (n_brackets, n_px, 3)
    per_px = np.nanmedian(stack, axis=0)
    good = ~np.isnan(per_px).any(axis=1)
    if not good.any():
        raise ValueError("every bracket is clipped or crushed inside the mask")
    lin = np.median(per_px[good], axis=0)
    return rgb_to_hex(linear_to_srgb(lin.reshape(1, 1, 3))[0, 0])
