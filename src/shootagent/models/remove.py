"""Object removal: grow the mask, ask the image edit model for an empty scene, report what changed.

Removals are most customer requests (cars, bins, cords, lockboxes). What made them pass the judge in
eval/models_removal.py on 12 real photos (see `choose_method` for the numbers):

- A prompt that asks for a removal. Prompts that describe the replacement ("grey wall, wood floor,
  nothing else") let the model keep the object: shoot 6de3da631b's second attempt kept the bin.
- The object's cast shadow lies outside its mask; `absorb_shadows` adds it from the model's clean plate.
- Fill detail matched to the photo (`genedit.match_detail`), so the patch is neither smeared nor grainy.
- LaMa smears anything bulky into a dark ghost; it stays as the offline fallback and an explicit choice.

`remove_object` returns the region outside which the result is byte-identical to the input; the
self-check should measure the outside delta against that region, not against the input mask.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from . import genedit
from ._composite import affected_region, grow

Method = Literal["auto", "lama", "generative", "lama+generative"]

GROW_FRAC = 0.04  # grow the object mask by this fraction of its long side...
GROW_MIN_PX = 6  # ...and at least this much, to swallow anti-aliased edges and contact halos
LAMA_FEATHER_PX = 3
GEN_FEATHER_PX = 6

REMOVAL_PROMPT = (
    "Remove the {what} completely, together with its shadow, its reflection and anything attached to it. "
    "Show only what was behind it: continue the surrounding wall, floor, ground or driveway with the same "
    "material, texture, pattern, joints, edges and lighting. The area must end up empty: do not add any "
    "object, plant, furniture, decoration or text."
)
PREFILL_NOTE = (
    " The masked area currently holds a rough placeholder fill; replace it with a clean, photographic "
    "continuation of the surroundings. Any dark smudge inside it is an artifact, not a shadow."
)


@dataclass
class Removal:
    image: np.ndarray  # RGB uint8, same shape as the input
    region: np.ndarray  # bool; outside it the image is byte-identical to the input
    method: str  # the method actually used ("lama", "generative", "lama+generative")
    usd: float  # image-model spend (estimate); 0 for LaMa
    ms: int  # image-model endpoint time; 0 for LaMa
    grow_px: int  # how far the object mask was grown
    model: str = ""


def grow_px_for(mask: np.ndarray) -> int:
    ys, xs = np.nonzero(mask)
    size = max(np.ptp(xs), np.ptp(ys)) + 1
    return max(GROW_MIN_PX, round(GROW_FRAC * size))


def choose_method(mask: np.ndarray) -> str:
    """The edit model whenever it is configured; LaMa only as the offline fallback.

    eval/models_removal.py, 12 real removals (4 bins, 4 cars, 3 cords, the 6de3da631b bedroom bin),
    judged by vision.judge_edit: generative 11/12, LaMa prefill + generative 9/12, LaMa 2/12. LaMa
    lost even on thin cords lying on glossy counters (2 of 3), so thinness is no reason to prefer it.
    """
    from ..config import get_settings

    return "generative" if get_settings().openai_api_key else "lama"


def remove_object(
    img: np.ndarray,
    mask: np.ndarray,
    what: str = "object",
    method: Method = "auto",
    model: str | None = None,
    quality: str = genedit.QUALITY,
) -> Removal:
    """Remove the object under `mask` (bool, same size as `img`). `what` names it for the prompt
    ("white trash bin", "silver car"). Raises on an empty mask or endpoint errors."""
    m = np.asarray(mask, dtype=bool)
    if m.shape != img.shape[:2]:
        raise ValueError(f"mask {m.shape} does not match image {img.shape[:2]}")
    if not m.any():
        raise ValueError("remove_object got an empty mask")
    if method == "auto":
        method = choose_method(m)
    px = grow_px_for(m)
    prompt = REMOVAL_PROMPT.format(what=what.strip() or "object")

    if method == "lama":
        from .inpaint import lama_inpaint

        out = lama_inpaint(img, m, dilate_px=px, feather_px=LAMA_FEATHER_PX)
        return Removal(out, affected_region(m, px, LAMA_FEATHER_PX), "lama", 0.0, 0, px)

    if method == "generative":
        r = genedit.generative_edit(
            img, grow(m, px), prompt, model=model, feather_px=GEN_FEATHER_PX, quality=quality, absorb_shadows=True
        )
        return Removal(r.image, grow(r.mask, GEN_FEATHER_PX), "generative", r.usd, r.ms, px, r.model)

    if method == "lama+generative":
        from .inpaint import lama_inpaint

        # The prefill keeps the edit model from seeing (and keeping) the object. Its changed region sits
        # inside the generative mask, so the final result differs from `img` only within grow(r.mask).
        pre = lama_inpaint(img, m, dilate_px=px, feather_px=LAMA_FEATHER_PX)
        pre_region = affected_region(m, px, LAMA_FEATHER_PX)
        r = genedit.generative_edit(
            pre, pre_region, prompt + PREFILL_NOTE, model=model, feather_px=GEN_FEATHER_PX, quality=quality, absorb_shadows=True
        )
        return Removal(r.image, grow(r.mask, GEN_FEATHER_PX), "lama+generative", r.usd, r.ms, px, r.model)

    raise ValueError(f"unknown removal method {method!r}; use auto, lama, generative or lama+generative")
