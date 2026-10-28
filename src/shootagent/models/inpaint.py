"""Local object removal with big-lama (TorchScript), at any photo size.

LaMa sees one padded crop per connected mask component, not the whole frame, so a 5400 px photo keeps
its detail: only the hole is synthesised, at the crop's working resolution, and everything outside the
grown mask is copied back byte-for-byte.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import cv2
import numpy as np
import torch

from ..config import get_settings
from . import device as dev
from ._composite import composite, grow

log = logging.getLogger(__name__)

# SHA-256 of the file each known URL served when this adapter was written. A different file at a
# pinned URL fails loudly instead of loading unknown weights. Unpinned URLs are only checked by loading.
PINNED_SHA256 = {
    "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt":
        "7ba7aa7ac37a4d41fdbbeba3a2af7ead18058552997e3a3cd1a3b2210c9e6b4c",
}

MAX_WORK_SIDE = 1024  # crop long edge LaMa runs at; ~1.0 GB peak on the 4060, ~0.5 s per crop
# LaMa was trained on ~256-512 px images, and what breaks it is hole thickness (distance to the nearest
# known pixel), not crop size. Removing the pedal bin in customer-data-068 at four working sizes: hole
# ~100 px thick gave clean wall and floor; ~150 px left a dark ghost of the bin's footprint; ~200 px
# filled it with a dark smear. So crops are scaled until the hole is at most this thick.
MAX_HOLE_THICKNESS = 100
CONTEXT = 1.0  # pad around each component's box, as a multiple of its long side
MIN_PAD = 48  # px at full resolution


def hole_thickness(hole: np.ndarray) -> float:
    """Twice the largest distance from a hole pixel to the nearest known pixel (a disk's diameter)."""
    dist = cv2.distanceTransform(np.pad(hole, 1).astype(np.uint8), cv2.DIST_L2, 5)
    return 2.0 * float(dist.max())


def work_scale(crop_hw: tuple[int, int], hole: np.ndarray) -> float:
    """Downscale factor for one crop: fits MAX_WORK_SIDE and keeps the hole under MAX_HOLE_THICKNESS."""
    return min(1.0, MAX_WORK_SIDE / max(crop_hw), MAX_HOLE_THICKNESS / max(hole_thickness(hole), 1.0))


def lama_path() -> Path:
    return get_settings().models_dir / "big-lama.pt"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_lama() -> Path:
    """Download big-lama to `settings.models_dir` if missing, and check its hash when the URL is pinned."""
    import httpx

    settings = get_settings()
    path = lama_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        log.info("downloading big-lama from %s", settings.lama_url)
        with httpx.stream("GET", settings.lama_url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with part.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        os.replace(part, path)
    expected = PINNED_SHA256.get(settings.lama_url)
    if expected is not None:
        got = _sha256(path)
        if got != expected:
            raise RuntimeError(
                f"{path} has SHA-256 {got}, expected {expected} for {settings.lama_url}. "
                "Delete the file to re-download it, or point LAMA_URL at the weights you meant."
            )
    return path


def _load_lama():
    path = ensure_lama()
    device = dev.get_device()
    try:
        model = torch.jit.load(str(path), map_location=device).eval()
    except Exception as e:
        raise RuntimeError(f"could not load big-lama TorchScript from {path}: {e}. Delete it to re-download.") from e
    return model


# fp32 only: LaMa's fast Fourier convolutions need power-of-two sizes for half-precision cuFFT.
_LAMA = dev.register("lama", _load_lama, est_mb=1300)


def unload() -> None:
    _LAMA.unload()


def _pad_to(x: np.ndarray, mod: int = 8) -> np.ndarray:
    h, w = x.shape[:2]
    ph, pw = (-h) % mod, (-w) % mod
    if ph == 0 and pw == 0:
        return x
    pad = ((0, ph), (0, pw)) + ((0, 0),) * (x.ndim - 2)
    return np.pad(x, pad, mode="symmetric")


@torch.inference_mode()
def _run_lama(crop: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """LaMa on one RGB uint8 crop and bool hole mask at `work_scale`; returns uint8 at crop size."""
    h, w = hole.shape
    scale = work_scale((h, w), hole)
    if scale < 1.0:
        size = (max(8, round(w * scale)), max(8, round(h * scale)))
        work = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
        # Area-resize the mask and keep any pixel it touches, so the hole never shrinks on the way down.
        work_hole = cv2.resize(hole.astype(np.float32), size, interpolation=cv2.INTER_AREA) > 0
    else:
        work, work_hole = crop, hole
    wh, ww = work_hole.shape
    img_t = torch.from_numpy(_pad_to(work)).permute(2, 0, 1)[None].float().div(255)
    mask_t = torch.from_numpy(_pad_to(work_hole.astype(np.float32)))[None, None]
    with dev.gpu_lock():
        model = _LAMA.get()
        device = dev.get_device()
        # The TorchScript profiling executor re-optimises for every new input shape, which made each
        # crop take 6-10 s; plain execution runs the same graph in ~0.5 s at 1024 px.
        with torch.jit.optimized_execution(False):
            out = model(img_t.to(device), mask_t.to(device))
        res = out[0, :, :wh, :ww].clamp(0, 1).mul(255).round().byte().permute(1, 2, 0).cpu().numpy()
    if scale < 1.0:
        res = cv2.resize(res, (w, h), interpolation=cv2.INTER_CUBIC)
    return res


def component_crops(region: np.ndarray, context: float = CONTEXT, min_pad: int = MIN_PAD) -> list[tuple[int, int, int, int]]:
    """Padded crop (x0, y0, x1, y1) for each connected component of `region`, largest first."""
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(region.astype(np.uint8), connectivity=8)
    h, w = region.shape
    crops = []
    for i in sorted(range(1, n), key=lambda i: -stats[i, cv2.CC_STAT_AREA]):
        x, y, bw, bh, _a = stats[i]
        pad = max(int(context * max(bw, bh)), min_pad)
        crops.append((max(0, x - pad), max(0, y - pad), min(w, x + bw + pad), min(h, y + bh + pad)))
    return crops


def lama_inpaint(img: np.ndarray, mask: np.ndarray, dilate_px: int = 8, feather_px: int = 3) -> np.ndarray:
    """Remove what `mask` covers. Returns a new RGB uint8 image the same shape as `img`.

    The mask grows by `dilate_px` to swallow edges and contact halos; the fill blends over a further
    `feather_px`. Pixels outside `affected_region(mask, dilate_px, feather_px)` are byte-identical.
    Components are filled largest first; a later crop sees earlier fills as context, never refills them.

    Good for small and medium objects (cords, bins against plain walls, clutter). Large objects and
    anything casting a big shadow come out as dark or blurry smears; use `genedit.generative_edit`
    with `absorb_shadows=True` for those. A wide-hole "clean plate" pass to find shadows was tried
    and dropped: on customer-train-010 it also erased a planter next to the car and absorbed it.
    """
    if not (img.dtype == np.uint8 and img.ndim == 3 and img.shape[2] == 3):
        raise ValueError(f"expected RGB uint8 (H, W, 3), got {img.dtype} {img.shape}")
    m = np.asarray(mask, dtype=bool)
    if m.shape != img.shape[:2]:
        raise ValueError(f"mask {m.shape} does not match image {img.shape[:2]}")
    if not m.any():
        raise ValueError("lama_inpaint got an empty mask; nothing to remove")

    region = grow(m, dilate_px)
    todo = region.copy()
    out = img.copy()
    for x0, y0, x1, y1 in component_crops(region):
        hole = todo[y0:y1, x0:x1].copy()
        if not hole.any():
            continue
        crop = np.ascontiguousarray(out[y0:y1, x0:x1])
        filled = _run_lama(crop, hole)
        out[y0:y1, x0:x1] = composite(crop, filled, hole, feather_px)
        todo[y0:y1, x0:x1] = False
    return out
