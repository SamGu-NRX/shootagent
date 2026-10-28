"""Claude vision judgements that return JSON: a generic `ask_json` and the edit self-check `judge_edit`.

JSON comes back through a single client tool whose `input_schema` is the requested schema. Claude Sonnet
5.5, Opus 5.5 and Fable 5.1 reject forced `tool_choice` with a 400 (checked against the gateway:
"tool_choice: type "tool" and "any" are not supported for this model"), so for those the call uses
`tool_choice: auto`, `strict: true` and an instruction to call the tool; older models are forced.

Prices are Anthropic's first-party rates per 1M tokens from the claude-api skill (cached 2026-10-06).
The gateway may bill differently; `Usage.usd` is what the same tokens cost on the Claude API.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from typing import Any, Literal

import cv2
import numpy as np
from PIL import Image

from ..config import get_settings

MAX_LONG_EDGE = 1568  # Claude's vision encoder downsizes anything larger; sending more only costs upload time
JPEG_QUALITY = 90
TOOL_NAME = "respond"

# USD per 1M tokens: (input, output). Cache reads bill at 0.1x input, cache writes at 1.25x.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
FORCED_TOOL_REJECTED = {"claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5"}


@dataclass
class Usage:
    model: str
    input_tokens: int  # all input tokens, cached or not
    output_tokens: int
    usd: float

    def __add__(self, other: Usage) -> Usage:
        model = self.model if self.model == other.model else f"{self.model}+{other.model}"
        return Usage(model, self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens, self.usd + other.usd)

    def as_event(self) -> dict[str, Any]:
        """Payload for a trace `usage` event."""
        return {"model": self.model, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "usd": round(self.usd, 6)}


def usage_cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    if model not in PRICES:
        raise KeyError(f"no price for {model!r}; add it to vision.PRICES (known: {sorted(PRICES)})")
    pin, pout = PRICES[model]
    return (input_tokens * pin + cache_read * pin * 0.1 + cache_write * pin * 1.25 + output_tokens * pout) / 1e6


def encode_image(img: np.ndarray, long_edge: int = MAX_LONG_EDGE) -> tuple[str, tuple[int, int]]:
    """Base64 JPEG of an RGB uint8 image downscaled to `long_edge`; returns (data, (w, h) sent)."""
    if not (img.dtype == np.uint8 and img.ndim == 3 and img.shape[2] == 3):
        raise ValueError(f"expected RGB uint8 (H, W, 3), got {img.dtype} {img.shape}")
    h, w = img.shape[:2]
    s = min(1.0, long_edge / max(h, w))
    if s < 1.0:
        img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=JPEG_QUALITY)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii"), (img.shape[1], img.shape[0])


def _client():
    import anthropic

    s = get_settings()
    kwargs: dict[str, Any] = {"base_url": s.anthropic_base_url, "max_retries": 3, "timeout": 300}
    if s.anthropic_api_key:
        kwargs["api_key"] = s.anthropic_api_key
    elif s.anthropic_auth_token:
        kwargs["auth_token"] = s.anthropic_auth_token
    else:
        raise RuntimeError("set ANTHROPIC_API_KEY, or ANTHROPIC_BASE_URL with ANTHROPIC_AUTH_TOKEN, for vision checks")
    return anthropic.Anthropic(**kwargs)


def build_request(
    images: list[tuple[str, np.ndarray]], prompt: str, schema: dict, model: str, max_tokens: int, effort: str, system: str | None
) -> dict[str, Any]:
    """The messages.create kwargs. Separate from the call so tests can check it without a network."""
    content: list[dict[str, Any]] = []
    for i, (label, img) in enumerate(images, 1):
        data, (w, h) = encode_image(img)
        content.append({"type": "text", "text": f"Image {i}: {label} ({w}x{h})"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    content.append({"type": "text", "text": f"{prompt}\n\nGive your answer only by calling the `{TOOL_NAME}` tool."})
    tool = {
        "name": TOOL_NAME,
        "description": "Submit the structured answer.",
        "input_schema": schema,
        "strict": True,
    }
    req: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
        "tools": [tool],
        "tool_choice": {"type": "auto"} if model in FORCED_TOOL_REJECTED else {"type": "tool", "name": TOOL_NAME},
        "output_config": {"effort": effort},
    }
    if system:
        req["system"] = system
    return req


def parse_response(resp: Any, model: str) -> tuple[dict, Usage]:
    """Tool input and usage from a Messages response. Raises when the model refused, ran out of tokens,
    or answered in text instead of calling the tool."""
    u = resp.usage
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    usage = Usage(
        model=model,
        input_tokens=u.input_tokens + cache_read + cache_write,
        output_tokens=u.output_tokens,
        usd=usage_cost(model, u.input_tokens, u.output_tokens, cache_read, cache_write),
    )
    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        raise RuntimeError(f"{model} refused the vision request ({getattr(details, 'category', None)}); usage {usage}")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(f"{model} hit max_tokens before answering; raise max_tokens (usage {usage})")
    for block in resp.content:
        if block.type == "tool_use" and block.name == TOOL_NAME:
            return dict(block.input), usage
    text = " ".join(b.text for b in resp.content if b.type == "text")[:300]
    raise RuntimeError(f"{model} answered without calling {TOOL_NAME} (stop_reason={resp.stop_reason}): {text!r}")


def ask_json(
    images: list[tuple[str, np.ndarray]],
    prompt: str,
    schema: dict,
    model: str | None = None,
    max_tokens: int = 8000,
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low",
    system: str | None = None,
) -> tuple[dict, Usage]:
    """Ask Claude about labelled images; returns (answer matching `schema`, usage).

    `images` are (label, RGB uint8 array) pairs; each is downscaled to 1568 px and introduced as
    "Image N: label" so the prompt can refer to it. `schema` must be strict-compatible: every object
    lists all its properties in `required` and sets `additionalProperties: false`.
    """
    model = model or get_settings().judge_model
    if model not in PRICES:
        raise KeyError(f"no price for {model!r}; add it to vision.PRICES before calling it")
    req = build_request(images, prompt, schema, model, max_tokens, effort, system)
    resp = _client().messages.create(**req)
    return parse_response(resp, model)


# ---- the edit self-check -------------------------------------------------------------------------------------


@dataclass
class Verdict:
    verdict: Literal["pass", "fail"]
    reason: str
    issues: list[str] = field(default_factory=list)  # defects that fail the edit
    minor: list[str] = field(default_factory=list)  # close-up-only defects, reported to the agent, not failing
    checks: dict[str, bool] = field(default_factory=dict)  # the sub-answers the verdict was derived from
    model: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def as_event(self) -> dict[str, Any]:
        """The `judge` payload of a trace `check` event."""
        return {"verdict": self.verdict, "reason": self.reason, "issues": self.issues, "minor": self.minor, "checks": self.checks, "model": self.model}


JUDGE_SYSTEM = """You are the final quality gate at a real-estate photo retouching studio. Every edit you pass goes straight onto a listing. You are strict: a buyer viewing the listing photo full-screen must not be able to tell it was edited, and the requested change must actually be done. When in doubt about a defect that would show at that size, fail it."""

JUDGE_PROMPT = """The editor was asked: "{intent}"

The images show the same photo before and after the edit. The edit region is outlined in red on the "before" crop; the outline is not part of either photo.

Check each point and answer honestly:
1. intent_achieved: is the requested change fully done in the after image? Partially done, wrong object, or the target still visible counts as false.
2. photoreal: viewed at normal listing size (the full photo on a laptop screen), does the edited region look like an unedited photograph, with correct perspective, lighting, shadows and texture? Look for the usual failures of inpainting and generative fill: blurry or smeared patches, dark or grey blobs, a ghost silhouette of the removed object, its shadow left behind, repeated or tiled texture, lines (skirting boards, floor seams, door edges, roof lines) that bend, break or fail to continue, colour or brightness that does not match its surroundings, a hard seam or halo at the region edge, and invented or duplicated objects.
3. outside_unchanged: does everything outside the edit region look the same in before and after? Small invisible differences do not matter; a moved, missing, added or recoloured object does.
4. artifacts: list every defect you can see, each with a short description saying where it is (for example "dark smear on the floor where the bin stood") and a severity:
   - "major": a buyer would notice it viewing the full photo at listing size, or it leaves part of the target behind.
   - "minor": only visible when zoomed in close-up and looking for it (slight softness, a faint seam you have to hunt for).
   Empty list if none.

Then give verdict "pass" only if 1-3 are all true and there is no major artifact, and a one-sentence reason a photographer would understand."""

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent_achieved": {"type": "boolean"},
        "photoreal": {"type": "boolean"},
        "outside_unchanged": {"type": "boolean"},
        "artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"description": {"type": "string"}, "severity": {"type": "string", "enum": ["major", "minor"]}},
                "required": ["description", "severity"],
                "additionalProperties": False,
            },
        },
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "reason": {"type": "string"},
    },
    "required": ["intent_achieved", "photoreal", "outside_unchanged", "artifacts", "verdict", "reason"],
    "additionalProperties": False,
}

CONTEXT = 0.75  # judge crop pad on each side, as a fraction of the mask box's long side
MIN_CROP = 384  # px at full resolution
FULL_FRAME_EDGE = 768


def judge_crop_box(mask: np.ndarray) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) around the mask with CONTEXT padding, at least MIN_CROP square where it fits."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise ValueError("judge_edit got an empty mask")
    h, w = mask.shape
    bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    pad = CONTEXT * max(bx1 - bx0, by1 - by0)
    cw = min(w, max(int(bx1 - bx0 + 2 * pad), MIN_CROP))
    ch = min(h, max(int(by1 - by0 + 2 * pad), MIN_CROP))
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    x0 = int(np.clip(round(cx - cw / 2), 0, w - cw))
    y0 = int(np.clip(round(cy - ch / 2), 0, h - ch))
    return x0, y0, x0 + cw, y0 + ch


def outline(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Copy of `img` with the mask boundary drawn in red, about 0.4% of the long edge thick."""
    out = img.copy()
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, contours, -1, (255, 0, 0), max(2, round(0.004 * max(img.shape[:2]))))
    return out


def _shrink(img: np.ndarray, long_edge: int) -> np.ndarray:
    s = min(1.0, long_edge / max(img.shape[:2]))
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1.0 else img


def combine_verdict(answer: dict) -> tuple[Literal["pass", "fail"], list[str], list[str]]:
    """(verdict, major issues, minor issues). The verdict follows the sub-answers, not the model's own
    verdict field: a failed check or any major artifact fails the edit, so a lenient final answer cannot
    pass a defect it described. Minor artifacts are reported but do not fail it: listing photos are seen
    full-screen, and failing on close-up softness made every edit in eval/models_smoke.py fail."""
    major, minor = [], []
    for a in answer.get("artifacts", []):
        text = str(a.get("description", "")).strip()
        if text:
            (major if a.get("severity") == "major" else minor).append(text)
    if not answer["outside_unchanged"]:
        major.insert(0, "content outside the edit region changed")
    if not answer["photoreal"]:
        major.insert(0, "edited region does not look photographic")
    if not answer["intent_achieved"]:
        major.insert(0, "requested change not achieved")
    ok = answer["verdict"] == "pass" and not major
    return ("pass" if ok else "fail"), major, minor

def judge_edit(
    before: np.ndarray,
    after: np.ndarray,
    mask: np.ndarray,
    intent: str,
    reference: np.ndarray | None = None,
    model: str | None = None,
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium",
) -> tuple[Verdict, Usage]:
    """Strict vision check of one edit. `mask` is the edit's region (bool, same size as the photos);
    `reference` is optional evidence of what should be there, such as the aligned bracket fusion."""
    if before.shape != after.shape:
        raise ValueError(f"before {before.shape} and after {after.shape} differ in shape")
    m = np.asarray(mask, dtype=bool)
    if m.shape != before.shape[:2]:
        raise ValueError(f"mask {m.shape} does not match image {before.shape[:2]}")
    x0, y0, x1, y1 = judge_crop_box(m)
    sl = (slice(y0, y1), slice(x0, x1))
    images = [
        ("BEFORE, close-up of the edit region (red outline marks the region)", outline(before[sl], m[sl])),
        ("AFTER, the same close-up", np.ascontiguousarray(after[sl])),
    ]
    if reference is not None:
        if reference.shape != before.shape:
            raise ValueError(f"reference {reference.shape} does not match photo {before.shape}")
        images.append(("REFERENCE from the exposure brackets, same close-up: what the scene really looks like", np.ascontiguousarray(reference[sl])))
    images += [("BEFORE, full frame", _shrink(before, FULL_FRAME_EDGE)), ("AFTER, full frame", _shrink(after, FULL_FRAME_EDGE))]

    model = model or get_settings().judge_model
    answer, usage = ask_json(images, JUDGE_PROMPT.format(intent=intent), JUDGE_SCHEMA, model=model, effort=effort, system=JUDGE_SYSTEM)
    verdict, issues, minor = combine_verdict(answer)
    checks = {k: bool(answer[k]) for k in ("intent_achieved", "photoreal", "outside_unchanged")}
    reason = str(answer.get("reason", "")).strip()
    if verdict == "fail" and answer["verdict"] == "pass":
        reason = f"Failed on its own findings: {'; '.join(issues)}."
    return Verdict(verdict=verdict, reason=reason, issues=issues, minor=minor, checks=checks, model=model), usage
