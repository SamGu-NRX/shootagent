"""Image loading and resizing.

Every function in `imaging/` takes and returns RGB uint8 arrays (H, W, 3) and boolean masks (H, W).
Loading applies the EXIF orientation and converts an embedded ICC profile to sRGB, so pixel values
from different cameras are comparable.
"""
from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps

Image.MAX_IMAGE_PIXELS = 120_000_000  # 10k x 10k brackets are legitimate here

_SRGB = ImageCms.createProfile("sRGB")


def _to_srgb(im: Image.Image) -> Image.Image:
    icc = im.info.get("icc_profile")
    if not icc:
        return im.convert("RGB")
    src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    desc = (ImageCms.getProfileDescription(src) or "").lower()
    if "srgb" in desc or "iec61966" in desc:
        return im.convert("RGB")
    mode = im.mode if im.mode in ("RGB", "CMYK", "L") else "RGB"
    return ImageCms.profileToProfile(im.convert(mode), src, _SRGB, outputMode="RGB")


def load_rgb(path: str | Path, max_long: int | None = None) -> np.ndarray:
    """Load an image as sRGB uint8 with EXIF orientation applied.

    `max_long` downsizes so the long edge is at most that many pixels. For JPEGs it uses the decoder's
    DCT scaling first (much faster than decoding 8k pixels and resizing).
    """
    with Image.open(path) as im:
        if max_long and im.format == "JPEG":
            w, h = im.size
            scale = max(w, h) / max_long
            if scale >= 2:
                im.draft("RGB", (int(w / scale) + 1, int(h / scale) + 1))
        im.load()
        im = ImageOps.exif_transpose(im)
        im = _to_srgb(im)
        arr = np.asarray(im)
    if max_long:
        arr = resize_long(arr, max_long)
    return np.ascontiguousarray(arr)


def load_bytes_rgb(data: bytes, max_long: int | None = None) -> np.ndarray:
    return load_rgb(io.BytesIO(data), max_long)


def image_size(path: str | Path) -> tuple[int, int]:
    """(width, height) after EXIF orientation, without decoding pixels."""
    with Image.open(path) as im:
        w, h = im.size
        orient = im.getexif().get(274, 1)
    return (h, w) if orient in (5, 6, 7, 8) else (w, h)


def resize_long(img: np.ndarray, long_edge: int) -> np.ndarray:
    """Downscale (never upscale) so max(H, W) <= long_edge. Uses INTER_AREA."""
    h, w = img.shape[:2]
    if max(h, w) <= long_edge:
        return img
    s = long_edge / max(h, w)
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def resize_to(img: np.ndarray, size_wh: tuple[int, int], interp: int | None = None) -> np.ndarray:
    """Resize to exactly (W, H). Bool masks are resized with nearest-neighbour and stay bool."""
    if img.dtype == bool:
        out = cv2.resize(img.astype(np.uint8) * 255, size_wh, interpolation=cv2.INTER_LINEAR)
        return out > 127
    if interp is None:
        interp = cv2.INTER_AREA if size_wh[0] < img.shape[1] else cv2.INTER_LINEAR
    return cv2.resize(img, size_wh, interpolation=interp)


def encode_jpeg(img: np.ndarray, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=quality)
    return buf.getvalue()
