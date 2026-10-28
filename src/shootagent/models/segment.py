"""Masks from text or clicks: Grounding DINO boxes, SAM 2.1 masks.

Coordinates in and out are normalised to 0..1 as [x0, y0, x1, y1] (boxes) or [x, y] (points), matching
the store and AutoHDR's region format. Images are RGB uint8 (H, W, 3); masks come back as bool (H, W)
at the full input resolution, however large the photo.

Detection runs on a copy downscaled to `DETECT_LONG_EDGE`; the processor would shrink it to about
800x1333 anyway. SAM 2.1 sees the image squashed to 1024x1024, so a lamp cord in a 7000 px frame is
under 2 px wide there. `segment_boxes` therefore re-runs SAM on a padded crop for every box smaller
than `REFINE_BELOW` of the frame, and returns the crop mask pasted into a full-size mask.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np
import torch

from ..config import get_settings
from . import device as dev
from ._composite import grow

DETECT_LONG_EDGE = 1333
SAM_INPUT_LONG_EDGE = 2048  # downscale before the processor; it resizes to 1024 anyway
REFINE_BELOW = 0.4  # boxes whose long side is under this fraction of the frame get a crop pass
REFINE_CONTEXT = 0.6  # crop pad on each side, as a fraction of the box's long side
REFINE_MIN_SIDE = 384  # px at full resolution; tiny boxes still get enough context to segment


@dataclass(frozen=True)
class Detection:
    box_norm: tuple[float, float, float, float]
    score: float
    label: str


@dataclass
class Found:
    mask: np.ndarray  # bool (H, W), full resolution
    box_norm: tuple[float, float, float, float]  # tight box around the cleaned mask
    score: float  # detector confidence
    label: str


# ---- loading ----------------------------------------------------------------------------------------------


def _load_grounding():
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    name = get_settings().grounding_model
    device = dev.get_device()
    processor = AutoProcessor.from_pretrained(name)
    # fp32: the multi-scale deformable attention in Grounding DINO is the part most likely to lose
    # accuracy in half precision, and the base model fits (about 0.9 GB of weights).
    model = AutoModelForZeroShotObjectDetection.from_pretrained(name).to(device).eval()
    return model, processor


def _load_sam():
    from transformers import Sam2Model, Sam2Processor

    name = get_settings().sam_model
    device = dev.get_device()
    processor = Sam2Processor.from_pretrained(name)
    model = Sam2Model.from_pretrained(name).to(device).eval()
    return model, processor


# VRAM estimates measured on the RTX 4060 (see eval/models_smoke.py output in the report).
_GROUNDING = dev.register("grounding_dino", _load_grounding, est_mb=2200)
_SAM = dev.register("sam2", _load_sam, est_mb=1800)


def unload() -> None:
    _GROUNDING.unload()
    _SAM.unload()


# ---- small pure helpers (unit tested) ---------------------------------------------------------------------


def normalize_prompt(prompt: str) -> str:
    """Grounding DINO wants lowercase phrases each ending in a period: "car. trash bin."."""
    parts = [p.strip() for p in re.split(r"[.,;\n]+", prompt.lower())]
    parts = [re.sub(r"\s+", " ", p) for p in parts if p]
    if not parts:
        raise ValueError(f"empty detection prompt {prompt!r}")
    return " ".join(p + "." for p in parts)


def resize_long_edge(img: np.ndarray, long_edge: int) -> tuple[np.ndarray, float]:
    """Downscale (never upscale) so the long edge is at most `long_edge`. Returns (image, scale)."""
    h, w = img.shape[:2]
    scale = min(1.0, long_edge / max(h, w))
    if scale >= 1.0:
        return img, 1.0
    out = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    return out, scale


def norm_to_px(box_norm, w: int, h: int) -> tuple[int, int, int, int]:
    """Normalised [x0, y0, x1, y1] to integer pixel bounds, clipped, with x1 > x0 and y1 > y0."""
    x0, y0, x1, y1 = (float(v) for v in box_norm)
    if not (x1 > x0 and y1 > y0):
        raise ValueError(f"box {box_norm} is empty or inverted; expected [x0, y0, x1, y1] with x1>x0, y1>y0")
    px0 = int(np.clip(np.floor(x0 * w), 0, w - 1))
    py0 = int(np.clip(np.floor(y0 * h), 0, h - 1))
    px1 = int(np.clip(np.ceil(x1 * w), px0 + 1, w))
    py1 = int(np.clip(np.ceil(y1 * h), py0 + 1, h))
    return px0, py0, px1, py1


def mask_box_norm(mask: np.ndarray) -> tuple[float, float, float, float] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    h, w = mask.shape
    return (xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h)


def box_iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def nms_detections(dets: list[Detection], iou: float = 0.5) -> list[Detection]:
    """Class-agnostic NMS: one object often gets two boxes with different phrases ("bin", "trash bin")."""
    kept: list[Detection] = []
    for d in sorted(dets, key=lambda d: d.score, reverse=True):
        if all(box_iou(d.box_norm, k.box_norm) < iou for k in kept):
            kept.append(d)
    return kept


def clean_mask(mask: np.ndarray, min_speck_frac: float = 0.02, max_hole_frac: float = 0.25, dilate_px: int = 0) -> np.ndarray:
    """Fill interior holes up to `max_hole_frac` of the mask area, drop disconnected specks smaller than
    `min_speck_frac` of the largest component, then optionally dilate."""
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return m.copy()
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.flatnonzero(areas >= max(1, min_speck_frac * areas.max())) + 1
    m = np.isin(labels, keep)

    area = int(m.sum())
    n, labels, stats, _ = cv2.connectedComponentsWithStats((~m).astype(np.uint8), connectivity=4)
    h, w = m.shape
    for i in range(1, n):
        x, y, bw, bh, a = stats[i]
        touches_border = x == 0 or y == 0 or x + bw == w or y + bh == h
        if not touches_border and a <= max_hole_frac * area:
            m[labels == i] = True

    return grow(m, dilate_px)


def mask_overlap(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """(IoU, intersection over the smaller mask)."""
    inter = np.logical_and(a, b).sum()
    if inter == 0:
        return 0.0, 0.0
    sa, sb = a.sum(), b.sum()
    return float(inter / (sa + sb - inter)), float(inter / min(sa, sb))


def refine_crop(box_px: tuple[int, int, int, int], w: int, h: int) -> tuple[int, int, int, int] | None:
    """Square-ish crop around a small box for a second SAM pass, or None when the box is big enough that
    the full-frame pass already has the resolution."""
    x0, y0, x1, y1 = box_px
    bw, bh = x1 - x0, y1 - y0
    if max(bw / w, bh / h) >= REFINE_BELOW:
        return None
    side = max(int(max(bw, bh) * (1 + 2 * REFINE_CONTEXT)), REFINE_MIN_SIDE)
    side_w, side_h = min(side, w), min(side, h)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cx0 = int(np.clip(round(cx - side_w / 2), 0, w - side_w))
    cy0 = int(np.clip(round(cy - side_h / 2), 0, h - side_h))
    return cx0, cy0, cx0 + side_w, cy0 + side_h


# ---- models -----------------------------------------------------------------------------------------------


def _check_image(img: np.ndarray) -> None:
    if not (isinstance(img, np.ndarray) and img.dtype == np.uint8 and img.ndim == 3 and img.shape[2] == 3):
        raise ValueError(f"expected an RGB uint8 array (H, W, 3), got {getattr(img, 'dtype', type(img))} {getattr(img, 'shape', '')}")


# Grounding DINO is phrase-sensitive. On customer-data-068 (a pedal bin in a kitchen) "trash bin" and
# "bin" return nothing even at a 0.15 threshold, while "trash can" scores 0.73 and "wastebasket" 0.72.
# Each phrase in a prompt also runs on its own: putting "trash can. bin." in one prompt cut the score
# to 0.52. Keys are lowercase user phrases; values are extra phrases to try.
SYNONYMS: dict[str, list[str]] = {
    "trash bin": ["trash can", "wastebasket"],
    "bin": ["trash can", "wastebasket"],
    "rubbish bin": ["trash can", "wastebasket"],
    "garbage bin": ["trash can", "garbage can"],
    "wheelie bin": ["trash can", "garbage can"],
    "trash": ["trash can"],
    "car": ["vehicle"],
    "cars": ["car", "vehicle"],
    "vehicle": ["car", "truck"],
    "cord": ["cable", "wire"],
    "cords": ["cable", "wire"],
    "cable": ["cord", "wire"],
    "lamp": ["table lamp", "floor lamp"],
    "toilet seat": ["toilet"],
    "tv": ["television"],
}


def expand_phrases(prompt: str) -> list[tuple[str, str]]:
    """[(phrase to run, label to report)] for each phrase in the prompt plus its synonyms, de-duplicated."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for phrase in normalize_prompt(prompt).rstrip(".").split(". "):
        for q in [phrase, *SYNONYMS.get(phrase, [])]:
            if q not in seen:
                seen.add(q)
                out.append((q, phrase))
    return out


def detect(
    img: np.ndarray,
    prompt: str,
    box_threshold: float = 0.3,
    text_threshold: float = 0.25,
    max_items: int = 20,
    expand: bool = True,
) -> list[Detection]:
    """Zero-shot boxes for a text prompt ("car. trash bin."), highest score first, after NMS.

    With `expand`, every phrase and its `SYNONYMS` run as separate detector passes (about 0.5 s each on
    the 4060) and the boxes are merged; labels are the user's phrase, not the synonym that matched.
    """
    _check_image(img)
    if not expand:
        return _detect_once(img, normalize_prompt(prompt), box_threshold, text_threshold, None)[:max_items]
    dets: list[Detection] = []
    for query, label in expand_phrases(prompt):
        dets += _detect_once(img, query + ".", box_threshold, text_threshold, label)
    return nms_detections(dets)[:max_items]


@torch.inference_mode()
def _detect_once(img: np.ndarray, text: str, box_threshold: float, text_threshold: float, label: str | None) -> list[Detection]:
    from PIL import Image

    small, _ = resize_long_edge(img, DETECT_LONG_EDGE)
    with dev.gpu_lock():
        model, processor = _GROUNDING.get()
        device = next(model.parameters()).device
        inputs = processor(images=Image.fromarray(small), text=text, return_tensors="pt").to(device)
        outputs = model(**inputs)
        # No target_sizes: boxes stay normalised to the (unpadded) image, which is what we return.
        result = processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids, threshold=box_threshold, text_threshold=text_threshold
        )[0]
    labels = result.get("text_labels") or result["labels"]
    dets = []
    for box, score, raw in zip(result["boxes"].float().cpu().tolist(), result["scores"].float().cpu().tolist(), labels):
        x0, y0, x1, y1 = (float(np.clip(v, 0.0, 1.0)) for v in box)
        if x1 - x0 <= 1e-4 or y1 - y0 <= 1e-4:
            continue
        dets.append(Detection((x0, y0, x1, y1), float(score), label or str(raw).strip() or text.rstrip(".")))
    return nms_detections(dets)


def _sam_embed(img: np.ndarray):
    """Run the SAM encoder once. Returns (embeddings, working image) for prompts on that image."""
    from PIL import Image

    model, processor = _SAM.get()
    small, _ = resize_long_edge(img, SAM_INPUT_LONG_EDGE)
    device = next(model.parameters()).device
    inputs = processor(images=Image.fromarray(small), return_tensors="pt").to(device)
    emb = model.get_image_embeddings(inputs["pixel_values"])
    return emb, small


def _sam_decode(emb, small: np.ndarray, out_hw: tuple[int, int], *, boxes_px=None, points_px=None, labels=None, multimask: bool):
    """Masks for prompts given in `small`'s pixel coordinates, returned as bool at `out_hw`.

    Returns (masks [N, H, W] bool, iou scores [N]). With multimask, the best of the 3 candidates per
    prompt is kept by predicted IoU.
    """
    import torch.nn.functional as F

    model, processor = _SAM.get()
    device = next(model.parameters()).device
    sh, sw = small.shape[:2]
    kwargs = {"original_sizes": [[sh, sw]], "return_tensors": "pt"}
    if boxes_px is not None:
        kwargs["input_boxes"] = [[list(map(float, b)) for b in boxes_px]]
    if points_px is not None:
        kwargs["input_points"] = [[[list(map(float, p)) for p in points_px]]]
        kwargs["input_labels"] = [[list(map(int, labels))]]
    prompts = processor(**kwargs).to(device)
    prompts.pop("original_sizes", None)
    out = model(image_embeddings=emb, multimask_output=multimask, **{k: v for k, v in prompts.items() if k.startswith("input_")})
    logits = out.pred_masks[0]  # (N, C, 256, 256)
    iou = out.iou_scores[0]  # (N, C)
    best = iou.argmax(dim=1)
    idx = torch.arange(logits.shape[0], device=logits.device)
    logits = logits[idx, best][:, None]  # (N, 1, 256, 256)
    scores = iou[idx, best].float().cpu().numpy()
    masks = []
    for i in range(logits.shape[0]):  # one at a time: a 7000x4700 float map is 130 MB
        up = F.interpolate(logits[i : i + 1].float(), out_hw, mode="bilinear", align_corners=False)
        masks.append((up[0, 0] > 0).cpu().numpy())
    return masks, scores


@torch.inference_mode()
def segment_boxes(img: np.ndarray, boxes_norm: list, refine: bool = True) -> list[np.ndarray]:
    """One bool mask (H, W) per normalised box. Small boxes get a second pass on a full-resolution crop."""
    _check_image(img)
    if not boxes_norm:
        return []
    h, w = img.shape[:2]
    boxes_px = [norm_to_px(b, w, h) for b in boxes_norm]
    with dev.gpu_lock():
        emb, small = _sam_embed(img)
        s = small.shape[1] / w
        masks, _ = _sam_decode(emb, small, (h, w), boxes_px=[[v * s for v in b] for b in boxes_px], multimask=False)
        if refine:
            for i, b in enumerate(boxes_px):
                crop = refine_crop(b, w, h)
                if crop is None:
                    continue
                cx0, cy0, cx1, cy1 = crop
                sub = np.ascontiguousarray(img[cy0:cy1, cx0:cx1])
                emb_c, small_c = _sam_embed(sub)
                sc = small_c.shape[1] / sub.shape[1]
                local = [(b[0] - cx0) * sc, (b[1] - cy0) * sc, (b[2] - cx0) * sc, (b[3] - cy0) * sc]
                (m_c,), _ = _sam_decode(emb_c, small_c, sub.shape[:2], boxes_px=[local], multimask=False)
                full = np.zeros((h, w), bool)
                full[cy0:cy1, cx0:cx1] = m_c
                masks[i] = full
    return masks


@torch.inference_mode()
def segment_points(img: np.ndarray, points_norm: list, labels: list[int], box_norm=None) -> np.ndarray:
    """Bool mask (H, W) from clicks: label 1 = on the object, 0 = not on it. An optional box constrains it.

    A single click is ambiguous and SAM's own scores do not resolve it: on customer-train-010 a click
    on the car's hood ranks the hood (score 0.98) above the whole car (0.03). Send a box, more positive
    clicks, or negative clicks to get a whole object.
    """
    _check_image(img)
    if not points_norm:
        raise ValueError("segment_points needs at least one point")
    if len(points_norm) != len(labels):
        raise ValueError(f"{len(points_norm)} points but {len(labels)} labels")
    if any(lb not in (0, 1) for lb in labels):
        raise ValueError(f"point labels must be 1 (object) or 0 (background), got {labels}")
    h, w = img.shape[:2]
    with dev.gpu_lock():
        emb, small = _sam_embed(img)
        sh, sw = small.shape[:2]
        pts = [[float(np.clip(x, 0, 1)) * sw, float(np.clip(y, 0, 1)) * sh] for x, y in points_norm]
        boxes = None
        if box_norm is not None:
            x0, y0, x1, y1 = norm_to_px(box_norm, sw, sh)
            boxes = [[x0, y0, x1, y1]]
        # One click without a box: let SAM propose three nested masks and keep its top-scored one.
        multimask = len(pts) == 1 and boxes is None
        (mask,), _ = _sam_decode(emb, small, (h, w), boxes_px=boxes, points_px=pts, labels=labels, multimask=multimask)
    return clean_mask(mask)


def find(
    img: np.ndarray,
    prompt: str,
    max_items: int = 10,
    min_score: float = 0.3,
    text_threshold: float = 0.25,
    dilate_px: int = 0,
    min_area_frac: float = 2e-5,
) -> list[Found]:
    """Objects matching a text prompt, as cleaned full-resolution masks, best first.

    Drops masks smaller than `min_area_frac` of the frame and masks that duplicate a better one
    (IoU > 0.6, or > 0.9 of the smaller mask inside the larger, e.g. a car window under "car").
    """
    dets = detect(img, prompt, box_threshold=min_score, text_threshold=text_threshold, max_items=max_items * 2)
    if not dets:
        return []
    masks = segment_boxes(img, [d.box_norm for d in dets])
    h, w = img.shape[:2]
    found: list[Found] = []
    for d, m in zip(dets, masks):
        m = clean_mask(m, dilate_px=dilate_px)
        if m.sum() < min_area_frac * h * w:
            continue
        if any(max(iou > 0.6, contained > 0.9) for iou, contained in (mask_overlap(m, f.mask) for f in found)):
            continue
        found.append(Found(mask=m, box_norm=mask_box_norm(m), score=d.score, label=d.label))
        if len(found) >= max_items:
            break
    return found
