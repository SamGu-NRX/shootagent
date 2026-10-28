"""Generative fills through an OpenAI-compatible `POST /v1/images/edits` (gpt-image-2 by default).

The endpoint re-renders the whole image it is given, drifts colour slightly, and only accepts a few
sizes. So the edit is confined three ways: only a context crop around the mask is sent; the returned
crop is colour-matched back to the original using the pixels outside the mask; and the result is
composited through the mask, so pixels outside `affected_region(mask, MASK_GROW_PX, feather_px)` stay
byte-identical.

Cost: the endpoint returns a token `usage` block. Prices per 1M tokens are OpenAI's published
gpt-image-2 rates as reported by third parties in October 2026 (text in $5, image in $8, image out
$30; e.g. https://fal.ai/models/openai/gpt-image-2 and https://crepal.ai/blog/aiimage/image-gpt-image-2-pricing/).
The local gateway's own billing is unknown, so `GenResult.usd` is an estimate.
"""
from __future__ import annotations

import base64
import io
import math
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image

from ..config import get_settings
from ._composite import composite, grow, shadow_pixels

SIZES: tuple[tuple[int, int], ...] = ((1024, 1024), (1536, 1024), (1024, 1536))  # (w, h) the endpoint accepts
CONTEXT = 0.5  # pad on each side of the mask box, as a fraction of the box's long side
MIN_CONTEXT_FRAC = 0.08  # ...and at least this fraction of the photo's long edge
MIN_CROP_FRAC = 0.25  # crops are at least this fraction of the photo's long edge, for scene context
MASK_GROW_PX = 6  # grow the editable area at full resolution to cover anti-aliased object edges
QUALITY = "high"  # the gateway defaults to "low" when this is omitted

# USD per 1M tokens: (text input, image input, image output). Estimates; see module docstring.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-image-2": (5.0, 8.0, 30.0),
    "gpt-image-2.5": (5.0, 8.0, 30.0),
}

PROMPT_WRAPPER = """You are retouching a professional real-estate listing photograph.
The first image is a crop of the photo. Edit ONLY its transparent (masked) area: {instruction}
Rules:
- Everything outside the masked area must stay exactly as it is: same objects, geometry, colours and framing.
- The edit must be invisible: match the photo's perspective and lens, vertical lines, lighting direction, shadows, white balance, exposure, noise and sharpness.
- Continue walls, floors, trim, landscaping and textures naturally through the area; keep materials and patterns aligned.
- Do not add people, animals, text, logos or watermarks, and do not add new objects unless the instruction asks for them.
- Photorealistic, like an unedited photograph. No stylisation, no HDR glow, no colour grading."""

REFERENCE_NOTE = "\nImages after the first are references only, not to be edited: {labels}."


@dataclass
class GenResult:
    image: np.ndarray  # full-size RGB uint8, same shape as the input
    mask: np.ndarray  # bool, the area composited through; outside grow(mask, feather_px) is unchanged
    usd: float | None  # estimate; see PRICES. None when the endpoint reported no usage
    ms: int  # wall time of the endpoint call
    crop_box: tuple[int, int, int, int]  # (x0, y0, x1, y1) px of the crop that was sent
    raw_size: tuple[int, int]  # (w, h) the model returned
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    color_fit: list[tuple[float, float]] = field(default_factory=list)  # per-channel (gain, offset) applied
    raw_crop: np.ndarray | None = None  # model output resized to the crop, before colour match (for debugging)


# ---- pure helpers (unit tested) ---------------------------------------------------------------------------


def pick_size(w: int, h: int) -> tuple[int, int]:
    """Supported (w, h) closest to the aspect ratio w:h, in log space."""
    r = math.log(w / h)
    return min(SIZES, key=lambda s: abs(math.log(s[0] / s[1]) - r))


def choose_crop(mask: np.ndarray) -> tuple[tuple[int, int, int, int], tuple[int, int]]:
    """Context crop (x0, y0, x1, y1) around the mask with the aspect of a supported size, and that size.

    Pads the mask box by CONTEXT of its long side (at least MIN_CONTEXT_FRAC of the photo), enforces
    MIN_CROP_FRAC, then widens the short side to the target aspect, shifting inside the frame. If the
    photo itself is too narrow for that aspect the crop is clipped and resized with mild distortion.
    """
    m = np.asarray(mask, dtype=bool)
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        raise ValueError("generative_edit got an empty mask")
    H, W = m.shape
    long_edge = max(H, W)
    bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    pad = max(CONTEXT * max(bx1 - bx0, by1 - by0), MIN_CONTEXT_FRAC * long_edge)
    cw = max(bx1 - bx0 + 2 * pad, MIN_CROP_FRAC * long_edge)
    ch = max(by1 - by0 + 2 * pad, MIN_CROP_FRAC * long_edge)
    size = pick_size(int(cw), int(ch))
    aspect = size[0] / size[1]
    if cw / ch < aspect:
        cw = ch * aspect
    else:
        ch = cw / aspect
    cw, ch = min(round(cw), W), min(round(ch), H)
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    x0 = int(np.clip(round(cx - cw / 2), 0, W - cw))
    y0 = int(np.clip(round(cy - ch / 2), 0, H - ch))
    return (x0, y0, x0 + cw, y0 + ch), size


def mask_png(editable: np.ndarray) -> bytes:
    """RGBA PNG for the endpoint: alpha 0 (transparent) where the model may edit, 255 elsewhere."""
    h, w = editable.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 3] = np.where(editable, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


def png_bytes(img: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG")
    return buf.getvalue()


def fit_color(src: np.ndarray, ref: np.ndarray, where: np.ndarray, gain_range=(0.75, 1.33)) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Per-channel least-squares gain/offset mapping `src` onto `ref` over the pixels in `where`.

    This is the whole-crop tint and exposure drift the edit model adds; fitting it on unedited pixels and
    applying it everywhere cancels it. Gains are clamped so a bad fit cannot wreck the fill.
    """
    if where.sum() < 64:
        return src.copy(), [(1.0, 0.0)] * 3
    out = np.empty(src.shape, np.float32)
    fit = []
    for c in range(3):
        x = src[..., c][where].astype(np.float64)
        y = ref[..., c][where].astype(np.float64)
        vx = x.var()
        gain = float(np.clip(((x - x.mean()) * (y - y.mean())).mean() / vx, *gain_range)) if vx > 1e-6 else 1.0
        offset = float(y.mean() - gain * x.mean())
        out[..., c] = src[..., c].astype(np.float32) * gain + offset
        fit.append((gain, offset))
    return np.clip(np.rint(out), 0, 255).astype(np.uint8), fit


def estimate_usd(model: str, usage: dict[str, Any]) -> float:
    if model not in PRICES:
        raise KeyError(f"no price for image model {model!r}; add it to genedit.PRICES (known: {sorted(PRICES)})")
    text_in, image_in, image_out = PRICES[model]
    details = usage.get("input_tokens_details") or {}
    img_tokens = details.get("image_tokens", 0)
    text_tokens = details.get("text_tokens", max(0, usage.get("input_tokens", 0) - img_tokens))
    out_tokens = usage.get("output_tokens", 0)
    return (text_tokens * text_in + img_tokens * image_in + out_tokens * image_out) / 1e6


def _mad_detail(y: np.ndarray, where: np.ndarray, sigma: float) -> float:
    """Robust size of the detail finer than `sigma`: 1.4826 x the median absolute high-pass residual.
    The median ignores edges and texture boundaries, which a plain std counts as noise."""
    res = (y - cv2.GaussianBlur(y, (0, 0), sigma))[where]
    return 1.4826 * float(np.median(np.abs(res - np.median(res))))


MAX_SHARPEN = 1.0  # unsharp amount cap
MAX_GRAIN = 4.0  # added noise std cap, 8-bit levels


def match_detail(gen: np.ndarray, orig: np.ndarray, mask: np.ndarray, seed: int = 0) -> np.ndarray:
    """Make the fill inside `mask` as sharp and as grainy as the photo just around it.

    The model's output is upscaled into the crop (1536 px into 2800 px on customer-train-010), so a fill
    is softer and cleaner than the photo; the judge called one driveway "smooth and smeared". An earlier
    version measured grain as the plain std of the high-pass residual over the whole crop, which counted
    edges and floor texture as noise and painted a grainy patch on a smooth wall (shoot 6de3da631b).
    Now both measurements are medians: the photo on a ring just outside the mask, the fill inside it.
    Softer fill gets an unsharp mask; then Gaussian luminance noise makes up the missing grain. Seeded.
    """
    ys, xs = np.nonzero(mask)
    if len(xs) < 256:
        return gen
    size = max(np.ptp(xs), np.ptp(ys)) + 1
    ring = grow(mask, max(24, int(0.3 * size))) & ~grow(mask, 4)
    inside = mask & ~grow(~mask, 3)
    if ring.sum() < 256 or inside.sum() < 256:
        return gen
    weights = np.array([0.299, 0.587, 0.114], np.float32)
    yo = orig.astype(np.float32) @ weights
    g = gen.astype(np.float32)

    detail_o, detail_g = _mad_detail(yo, ring, 2.0), _mad_detail(g @ weights, inside, 2.0)
    if detail_g > 1e-3 and detail_o > 1.15 * detail_g:
        amount = min(MAX_SHARPEN, detail_o / detail_g - 1.0)
        g = g + amount * (g - cv2.GaussianBlur(g, (0, 0), 2.0))

    noise_o, noise_g = _mad_detail(yo, ring, 1.0), _mad_detail(g @ weights, inside, 1.0)
    missing = min(MAX_GRAIN, float(np.sqrt(max(0.0, noise_o**2 - noise_g**2))))
    if missing > 0.5:
        noise = np.random.default_rng(seed).normal(0.0, 1.0, g.shape[:2]).astype(np.float32)
        noise = cv2.GaussianBlur(noise, (0, 0), 0.6)
        g = g + (noise * (missing / max(float(noise.std()), 1e-6)))[..., None]
    return np.clip(np.rint(g), 0, 255).astype(np.uint8)


def boundary_correct(gen: np.ndarray, orig: np.ndarray, mask: np.ndarray, ring_px: int = 12, max_diff: float = 20.0) -> np.ndarray:
    """Remove the local colour offset left after the global fit, so the fill meets the photo without a seam.

    Measures orig - gen on a ring just outside the mask (skipping pixels that differ by more than
    `max_diff`, which are content changes, not tint), spreads it smoothly over the mask by normalised
    Gaussian interpolation on a downscaled grid, and adds it to `gen`. Only the mask and the feather band
    use the result, so outside pixels are unaffected.
    """
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return gen
    h, w = mask.shape
    diff = orig.astype(np.float32) - gen.astype(np.float32)
    ring = grow(mask, ring_px) & ~mask & (np.abs(diff).max(axis=2) <= max_diff)
    if ring.sum() < 32:
        return gen
    s = min(1.0, 512 / max(h, w))
    size = (max(1, round(w * s)), max(1, round(h * s)))
    wgt = cv2.resize(ring.astype(np.float32), size, interpolation=cv2.INTER_AREA)
    num = cv2.resize(diff * ring[..., None], size, interpolation=cv2.INTER_AREA)
    sigma = max(2.0, 0.08 * max(np.ptp(xs) + 1, np.ptp(ys) + 1) * s)
    wb = cv2.GaussianBlur(wgt, (0, 0), sigma)
    nb = cv2.GaussianBlur(num, (0, 0), sigma)
    offset = nb / np.maximum(wb, 1e-3)[..., None]
    offset = cv2.resize(offset, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(np.rint(gen.astype(np.float32) + offset), 0, 255).astype(np.uint8)


def _resize(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Resize to (w, h): area filter when shrinking, Lanczos when enlarging."""
    h, w = img.shape[:2]
    shrinking = size[0] * size[1] < w * h
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_LANCZOS4)


# ---- endpoint ----------------------------------------------------------------------------------------------


def _client():
    import openai

    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; generative fills need the image edit endpoint (see .env.example)")
    return openai.OpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url, timeout=180, max_retries=2)


def _call_edit(model: str, images: list[bytes], mask: bytes, prompt: str, size: tuple[int, int], quality: str) -> dict[str, Any]:
    """One images.edit call. Returns the response as a dict. Patched in tests."""
    files = [(f"image{i}.png", b, "image/png") for i, b in enumerate(images)]
    resp = _client().images.edit(
        model=model,
        image=files if len(files) > 1 else files[0],
        mask=("mask.png", mask, "image/png"),
        prompt=prompt,
        size=f"{size[0]}x{size[1]}",
        quality=quality,
        n=1,
    )
    return resp.model_dump()


def generative_edit(
    img: np.ndarray,
    mask: np.ndarray,
    prompt: str,
    references: list[tuple[str, np.ndarray]] | None = None,
    model: str | None = None,
    feather_px: int = 6,
    quality: str = QUALITY,
    absorb_shadows: bool = False,
) -> GenResult:
    """Apply `prompt` inside `mask` with the image edit model and composite the result back.

    `references` are (label, RGB image) pairs sent after the crop, e.g. ("bracket at +2 EV", arr) or
    ("colour swatch #A3B1C2", arr).

    `absorb_shadows` is for removals: the model re-renders the whole crop, so pixels next to the mask
    that are clearly darker in the original than in its clean plate are the removed object's shadow,
    and they join the composited area (see `shadow_pixels`).

    Pixels outside `grow(result.mask, feather_px)` are byte-identical to `img`; `result.mask` is the
    composited area (the input mask grown by MASK_GROW_PX, plus absorbed shadows). Raises on endpoint
    errors or a missing image in the response.
    """
    if not (img.dtype == np.uint8 and img.ndim == 3 and img.shape[2] == 3):
        raise ValueError(f"expected RGB uint8 (H, W, 3), got {img.dtype} {img.shape}")
    m = np.asarray(mask, dtype=bool)
    if m.shape != img.shape[:2]:
        raise ValueError(f"mask {m.shape} does not match image {img.shape[:2]}")
    if not prompt.strip():
        raise ValueError("generative_edit needs a non-empty prompt")
    model = model or get_settings().image_edit_model
    if model not in PRICES:
        raise KeyError(f"no price for image model {model!r}; add it to genedit.PRICES before spending money on it")

    editable = grow(m, MASK_GROW_PX)
    (x0, y0, x1, y1), size = choose_crop(editable)
    crop = np.ascontiguousarray(img[y0:y1, x0:x1])
    crop_editable = editable[y0:y1, x0:x1]
    sent = _resize(crop, size)
    # Area-resize the mask and keep anything it touches, so the editable area never shrinks.
    sent_editable = cv2.resize(crop_editable.astype(np.float32), size, interpolation=cv2.INTER_AREA) > 0

    images = [png_bytes(sent)]
    full_prompt = PROMPT_WRAPPER.format(instruction=prompt.strip())
    if references:
        for _label, ref in references:
            rh, rw = ref.shape[:2]
            k = min(1.0, 1536 / max(rh, rw))
            ref_small = _resize(ref, (max(1, round(rw * k)), max(1, round(rh * k))))
            images.append(png_bytes(np.ascontiguousarray(ref_small)))
        labels = "; ".join(f"image {i + 2}: {label}" for i, (label, _r) in enumerate(references))
        full_prompt += REFERENCE_NOTE.format(labels=labels)

    started = time.perf_counter()
    resp = _call_edit(model, images, mask_png(sent_editable), full_prompt, size, quality)
    ms = int((time.perf_counter() - started) * 1000)
    data = resp.get("data") or []
    if not data or not data[0].get("b64_json"):
        raise RuntimeError(f"image edit returned no image (keys: {sorted(resp)}); nothing was composited")
    with Image.open(io.BytesIO(base64.b64decode(data[0]["b64_json"]))) as im:
        raw = np.asarray(im.convert("RGB"))
    raw_size = (raw.shape[1], raw.shape[0])

    gen_crop = _resize(raw, (crop.shape[1], crop.shape[0]))
    # Fit only on pixels well away from the edit, so the fill itself does not skew the fit.
    known = ~grow(crop_editable, max(8, feather_px * 2))
    matched, fit = fit_color(gen_crop, crop, known)
    if absorb_shadows:
        crop_editable = crop_editable | shadow_pixels(crop, matched, crop_editable)
    matched = boundary_correct(matched, crop, crop_editable)
    matched = match_detail(matched, crop, crop_editable)
    out = img.copy()
    out[y0:y1, x0:x1] = composite(crop, matched, crop_editable, feather_px)
    final_mask = np.zeros(m.shape, bool)
    final_mask[y0:y1, x0:x1] = crop_editable
    usage = resp.get("usage") or {}
    return GenResult(
        image=out,
        mask=final_mask,
        # None when the gateway omits usage: the cost is unknown, not zero. NaN broke JSON in the trace.
        usd=estimate_usd(model, usage) if usage else None,
        ms=ms,
        crop_box=(x0, y0, x1, y1),
        raw_size=raw_size,
        model=model,
        usage=usage,
        color_fit=fit,
        raw_crop=gen_crop,
    )
