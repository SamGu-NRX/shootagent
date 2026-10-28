"""The one place the tools call into `imaging/`, `qc/` and `models/` (owned by other modules).

Each function imports its dependency when called, so a module that is missing or broken fails only the
tool that needs it, with a message naming the module. Signatures here are the tools' own; adapting to a
change in imaging/models happens in this file only.
"""

from __future__ import annotations

import importlib
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import cv2
import numpy as np

from ._common import ToolError


def _mod(name: str):
    try:
        return importlib.import_module(f"shootagent.{name}")
    except ImportError as e:
        raise ToolError(f"shootagent.{name} is not available ({e}); use a different tool.") from e


def _fn(module: str, attr: str):
    fn = getattr(_mod(module), attr, None)
    if fn is None:
        raise ToolError(f"shootagent.{module}.{attr} is not implemented yet; use a different tool.")
    return fn


def fit_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    m = np.asarray(mask)
    if m.dtype != bool:
        m = m > (127 if m.max() > 1 else 0.5)
    if m.shape != tuple(shape):
        m = cv2.resize(m.astype(np.uint8) * 255, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR) > 127
    return m


def fit_image(img: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    if img.shape[:2] == tuple(shape):
        return img
    return cv2.resize(img, (shape[1], shape[0]), interpolation=cv2.INTER_AREA)


# ---- imaging --------------------------------------------------------------------------------------------


def exposure(img: np.ndarray, ev: float, mask: np.ndarray | None) -> np.ndarray:
    return _fn("imaging.adjust", "exposure")(img, ev, mask=mask)


def white_balance(img: np.ndarray, temperature: float, tint: float, mask: np.ndarray | None) -> np.ndarray:
    return _fn("imaging.adjust", "white_balance")(img, temp=temperature, tint=tint, mask=mask)


RECOLOR_FEATHER_PX = 3


def recolor(img: np.ndarray, mask: np.ndarray, hex_color: str) -> tuple[np.ndarray, np.ndarray]:
    """Returns (image, region it may change): the mask plus its feather."""
    out = _fn("imaging.adjust", "recolor")(img, mask, hex_color, feather_px=RECOLOR_FEATHER_PX)
    return out, grow(mask, RECOLOR_FEATHER_PX)


def sample_hex(img: np.ndarray, mask: np.ndarray) -> str:
    return _fn("imaging.adjust", "sample_hex")(img, mask)


def _composite_mod():
    """imaging.composite once it lands; until then the models adapters' private copy, which documents the
    same signature and the same byte-identical-outside guarantee."""
    try:
        mod = importlib.import_module("shootagent.imaging.composite")
        if hasattr(mod, "composite"):
            return mod
    except ImportError:
        pass
    return _mod("models._composite")


def grow(mask: np.ndarray, px: int) -> np.ndarray:
    """Mask dilated by a disk of radius `px`; the same dilation imaging/models use for their guarantees."""
    if px <= 0:
        return mask.astype(bool)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    return cv2.dilate(mask.astype(np.uint8), k) > 0


def composite(before: np.ndarray, after: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """`after` inside the mask, `before` exactly outside it."""
    return _composite_mod().composite(before, fit_image(after, before.shape[:2]), mask)


def affected_region(mask: np.ndarray, dilate_px: int, feather_px: int) -> np.ndarray:
    """Pixels an edit that grows `mask` by `dilate_px` and feathers by `feather_px` may change."""
    return grow(grow(mask, dilate_px), feather_px)


def outside_delta(before: np.ndarray, after: np.ndarray, mask: np.ndarray) -> float:
    out = _fn("imaging.composite", "outside_delta")(before, after, mask)
    return float(out["max"] if isinstance(out, dict) else out)


RESTORE_FEATHER_PX = 6


def restore_from_reference(
    img: np.ndarray, reference: np.ndarray, mask: np.ndarray, valid: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Bracket content back inside the mask, styled like the photo. Returns (image, changed region, info)."""
    out = _fn("imaging.composite", "restore_from_reference")(
        img, reference, mask, feather_px=RESTORE_FEATHER_PX, valid=valid
    )
    img_out, info = out if isinstance(out, tuple) else (out, {})
    return img_out, grow(mask, RESTORE_FEATHER_PX), info


_ref_cache: OrderedDict[tuple[str, str, str], tuple[np.ndarray, np.ndarray | None]] = OrderedDict()
_REF_CACHE_SIZE = 3  # full-resolution RGB arrays are ~50 MB each


def reference_for_fix(
    store, shoot_id: str, view_id: str, target_blob: str
) -> tuple[np.ndarray, np.ndarray | None]:
    """Full-resolution bracket fusion registered onto `target_blob`, and where it is valid. Cached, because
    it takes tens of seconds and a retry on the same view needs the same reference."""
    key = (shoot_id, view_id, target_blob)
    with _qc_lock:
        if key in _ref_cache:
            _ref_cache.move_to_end(key)
            return _ref_cache[key]
    ref, valid = _fn("qc", "reference_for_fix")(store, shoot_id, view_id, target_blob=target_blob)
    with _qc_lock:
        _ref_cache[key] = (ref, valid)
        while len(_ref_cache) > _REF_CACHE_SIZE:
            _ref_cache.popitem(last=False)
    return ref, valid


def watermark(
    img: np.ndarray, text: str, position: str, opacity: float, scale: float
) -> tuple[np.ndarray, np.ndarray]:
    out = _fn("imaging.overlay", "watermark")(img, text, position=position, opacity=opacity, scale=scale)
    return _with_area(img, out)


def lot_lines(
    img: np.ndarray, points: list[list[float]], label: str | None, closed: bool
) -> tuple[np.ndarray, np.ndarray]:
    out = _fn("imaging.overlay", "lot_lines")(img, points, label=label, closed=closed)
    return _with_area(img, out)


def _with_area(before: np.ndarray, out) -> tuple[np.ndarray, np.ndarray]:
    """Overlay functions may return the image alone or (image, mask); derive the mask from the diff if alone."""
    if isinstance(out, tuple):
        img, area = out
        return img, fit_mask(area, before.shape[:2])
    area = np.any(out != before, axis=2)
    if not area.any():
        raise ToolError("The overlay drew nothing.")
    return out, cv2.dilate(area.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0


SCREEN_FEATHER_PX = 1


def place_on_screen(img: np.ndarray, mask: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Returns (image, region it may change)."""
    out = _fn("imaging.screen", "place_on_screen")(img, mask, ref, feather_px=SCREEN_FEATHER_PX)
    return out, grow(mask, SCREEN_FEATHER_PX)


# ---- qc -------------------------------------------------------------------------------------------------


# QC analysis is CPU-heavy (seconds per view) and cached by qc.analyze_view per analysed blob. A QC run
# starts it for every view in the background while the model plans; run_qc_checks then waits for the
# in-flight job instead of computing the same view twice. Two workers: OpenCV already uses several cores
# per job, and eight parallel jobs ran slower in total than this.
_qc_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="qc-prewarm")
_qc_inflight: dict[tuple[str, str], Future] = {}
_qc_lock = threading.Lock()


def _analysis_job(store, shoot_id: str, view_id: str) -> Future:
    key = (shoot_id, view_id)
    with _qc_lock:
        fut = _qc_inflight.get(key)
        if fut is None or (fut.done() and fut.exception() is not None):
            fut = _qc_pool.submit(_fn("qc", "analyze_view"), store, shoot_id, view_id, None)
            _qc_inflight[key] = fut
            fut.add_done_callback(lambda f, k=key: _qc_done(k, f))
        return fut


def _qc_done(key, fut: Future) -> None:
    with _qc_lock:
        if _qc_inflight.get(key) is fut:
            del _qc_inflight[key]


def prewarm_analysis(store, shoot_id: str, view_ids: list[str]) -> None:
    for vid in view_ids:
        _analysis_job(store, shoot_id, vid)


def analyze_view(store, shoot_id: str, view_id: str, trace):
    """qc.analyze_view, joining a background job for the same view if one is running."""
    with _qc_lock:
        fut = _qc_inflight.get((shoot_id, view_id))
    if fut is not None:
        try:
            fut.result()
        except Exception:  # the prewarm failed; the direct call below raises the error for the model to read
            pass
    return _fn("qc", "analyze_view")(store, shoot_id, view_id, trace)


def regroup_view(store, shoot_id: str, view_id: str, groups: list[list[int]]) -> list[str]:
    return _fn("qc", "regroup_view")(store, shoot_id, view_id, groups)


# ---- models ---------------------------------------------------------------------------------------------


def segment_find(img: np.ndarray, prompt: str) -> list:
    return list(_fn("models.segment", "find")(img, prompt))


def segment_box(img: np.ndarray, box: list[float]) -> np.ndarray:
    out = _fn("models.segment", "segment_boxes")(img, [box])
    m = out[0]
    return getattr(m, "mask", m)


def segment_points(img: np.ndarray, points: list[list[float]], box: list[float] | None) -> np.ndarray:
    """Mask from clicks [[x, y, label]] (normalised; label 1 = object, 0 = background) and/or a box."""
    if not points:
        if box is None:
            raise ToolError("Give at least one point or a box.")
        return segment_box(img, box)
    if any(len(p) not in (2, 3) for p in points):
        raise ToolError("Each point is [x, y] or [x, y, label] with x, y in 0..1.")
    xy = [[float(p[0]), float(p[1])] for p in points]
    labels = [int(p[2]) if len(p) == 3 else 1 for p in points]
    return _fn("models.segment", "segment_points")(img, xy, labels, box_norm=box)


LAMA_DILATE_PX, LAMA_FEATHER_PX = 8, 3  # models.inpaint.lama_inpaint defaults, passed explicitly


def lama(img: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """LaMa removal. Returns (image, region it may have changed): the mask grown to swallow edges and halo."""
    out = _fn("models.inpaint", "lama_inpaint")(
        img, mask, dilate_px=LAMA_DILATE_PX, feather_px=LAMA_FEATHER_PX
    )
    return out, affected_region(mask, LAMA_DILATE_PX, LAMA_FEATHER_PX)


def generative(
    ctx,
    img: np.ndarray,
    mask: np.ndarray,
    prompt: str,
    references: list[tuple[str, np.ndarray]] | None = None,
    view: str | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Image-model edit inside the mask. Emits a `usage` event; returns (image, changed region, usage dict)."""
    genedit = _mod("models.genedit")
    feather = 6
    res = _fn("models.genedit", "generative_edit")(
        img, mask, prompt, references=references, feather_px=feather
    )
    usd = float(getattr(res, "usd", 0) or 0)
    model = getattr(res, "model", None) or ctx.settings.image_edit_model
    u = getattr(res, "usage", None) or {}
    ctx.trace.emit(
        "usage",
        view=view,
        model=model,
        input_tokens=int(u.get("input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        usd=round(usd, 6),
        purpose="generative_edit",
        ms=getattr(res, "ms", None),
    )
    region = affected_region(mask, getattr(genedit, "MASK_GROW_PX", 6), feather)
    return (
        res.image,
        region,
        {"image_model": model, "image_usd": round(usd, 4), "image_ms": getattr(res, "ms", None)},
    )


def judge(
    before: np.ndarray,
    after: np.ndarray,
    mask: np.ndarray | None,
    intent: str,
    reference: np.ndarray | None = None,
):
    return _fn("models.vision", "judge_edit")(before, after, mask, intent, reference=reference)
