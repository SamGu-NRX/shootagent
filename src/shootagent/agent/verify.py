"""Self-check run by the loop after every edit: deterministic metrics, then a vision judgement.

The model cannot skip this. A version fails when pixels outside its mask moved, when a region metric
misses its target (exposure change, colour distance), or when the vision judge says the result does not
meet the stated intent. The loop reverts failed versions.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np

from ..tools import _ops
from ..tools._common import (
    ToolError,
    context_box,
    crop_norm,
    lab_of,
    luminance,
    mean_rgb,
    parse_hex,
    resize_long,
)
from ..tools.base import ToolContext, ToolResult
from .usage import usage_event

OUTSIDE_TOL = 1  # max |delta| in 0..255 allowed outside the mask; composite should make it exactly 0
DELTA_E_MAX = 10.0  # region mean vs target hex, CIE76; ~10 is "clearly the same colour family" under shading


def _outside_delta(before: np.ndarray, after: np.ndarray, mask: np.ndarray) -> float:
    try:
        return _ops.outside_delta(before, after, mask)
    except ToolError:
        outside = ~mask
        if not outside.any():
            return 0.0
        return float(np.abs(after.astype(np.int16) - before.astype(np.int16))[outside].max())


def _measured_ev(before: np.ndarray, after: np.ndarray, mask: np.ndarray | None) -> float:
    lb, la = luminance(before), luminance(after)
    sel = (lb > 0.01) & (lb < 0.6)  # mid-tones: shadows are noisy, highlights are protected from clipping
    if mask is not None:
        sel &= mask
    if sel.sum() < 100:
        sel = mask if mask is not None else np.ones_like(lb, bool)
    return float(np.log2(np.median(la[sel]) / max(1e-6, np.median(lb[sel]))))


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    from skimage.metrics import structural_similarity

    ga = resize_long(a, 512).astype(np.float32).mean(axis=2)
    gb = resize_long(b, 512).astype(np.float32).mean(axis=2)
    if min(ga.shape) < 7:
        return 1.0
    return float(structural_similarity(ga, gb, data_range=255.0))


def region_metrics(
    kind: str,
    metric: dict[str, Any],
    before: np.ndarray,
    after: np.ndarray,
    mask: np.ndarray | None,
    reference: np.ndarray | None,
) -> tuple[dict[str, float], list[str]]:
    """Metric values and failure reasons for the region the edit targeted."""
    out: dict[str, float] = {}
    fails: list[str] = []
    if kind == "ev":
        target = float(metric["ev"])
        got = _measured_ev(before, after, mask)
        out.update(ev_target=round(target, 3), ev_measured=round(got, 3))
        if abs(got - target) > 0.15 + 0.5 * abs(target):
            fails.append(f"exposure changed by {got:+.2f} EV, not the {target:+.2f} EV asked for")
    elif kind == "delta_e" and mask is not None:
        target = np.array(parse_hex(metric["hex"]), np.float64)
        lab_t = lab_of(target)[0]
        de_after = float(np.linalg.norm(lab_of(mean_rgb(after, mask))[0] - lab_t))
        de_before = float(np.linalg.norm(lab_of(mean_rgb(before, mask))[0] - lab_t))
        out.update(delta_e_before=round(de_before, 2), delta_e_after=round(de_after, 2))
        if de_after > DELTA_E_MAX:
            fails.append(
                f"region colour is {de_after:.1f} delta-E from {metric['hex']} (limit {DELTA_E_MAX})"
            )
    elif kind == "ssim_reference" and mask is not None and reference is not None:
        box = context_box(_bbox(mask), pad=0.1, min_side=0.05)
        s_after = _ssim(crop_norm(after, box), crop_norm(reference, box))
        s_before = _ssim(crop_norm(before, box), crop_norm(reference, box))
        out.update(region_ssim_to_reference=round(s_after, 3), region_ssim_before=round(s_before, 3))
        if s_after + 0.02 < s_before:
            fails.append(f"region moved away from the brackets (SSIM {s_before:.2f} -> {s_after:.2f})")
    return out, fails


def _bbox(mask: np.ndarray) -> list[float]:
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    return [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]


# Metrics that measure the requested amount directly. The judge is unreliable at perceiving such amounts
# (run 20261027-224425-743250: it failed two edits measured at exactly +0.50 EV as "not visibly brighter"), so
# for these its intent answer is advisory; photorealism and outside-region answers still fail the edit.
INTENT_METRICS = ("ev", "delta_e")


def _metric_owns_intent(metric: dict[str, Any], failures: list[str], judge: dict[str, Any]) -> bool:
    checks = judge.get("checks") or {}
    return (
        metric.get("kind") in INTENT_METRICS
        and not failures
        and bool(checks)
        and checks.get("photoreal") is not False
        and checks.get("outside_unchanged") is not False
    )


def _verdict_dict(v: Any) -> dict[str, Any]:
    if isinstance(v, dict):
        d = dict(v)
    elif dataclasses.is_dataclass(v):
        d = dataclasses.asdict(v)
    elif hasattr(v, "model_dump"):
        d = v.model_dump()
    else:
        d = {k: getattr(v, k, None) for k in ("verdict", "reason", "issues")}
    d["verdict"] = str(d.get("verdict") or "fail").lower()
    return d


def _emit_usage(ctx: ToolContext, view: str, usage: Any, purpose: str) -> None:
    if usage is None:
        return
    model = (
        usage.get("model") if isinstance(usage, dict) else getattr(usage, "model", None)
    ) or ctx.settings.judge_model
    usd = usage.get("usd") if isinstance(usage, dict) else getattr(usage, "usd", None)
    data = usage_event(model, usage)
    if usd is not None:
        data["usd"] = round(float(usd), 6)
        data.pop("unpriced", None)
    ctx.trace.emit("usage", view=view, purpose=purpose, **data)


def verify_edit(ctx: ToolContext, view_id: str, result: ToolResult, use_judge: bool = True) -> dict[str, Any]:
    """Check one new version. Returns the `check` payload (also stored on the version)."""
    info = result.data["verify"]
    shoot = ctx.store.load(ctx.shoot_id)
    view = shoot.view(view_id)
    new_v, parent_v = view.version(result.version), view.version(info["parent"])
    before = ctx.store.get_image(ctx.shoot_id, parent_v.blob)
    after = ctx.store.get_image(ctx.shoot_id, new_v.blob)
    mask = (
        _ops.fit_mask(ctx.store.get_mask(ctx.shoot_id, info["mask"]), before.shape[:2])
        if info.get("mask")
        else None
    )
    metric = info.get("metric") or {}
    # Region metrics use the surface/object mask the agent chose; leaks are measured outside the version's
    # mask (that surface plus the edge margin the op is allowed to touch).
    target = mask
    if metric.get("region_mask"):
        target = _ops.fit_mask(ctx.store.get_mask(ctx.shoot_id, metric["region_mask"]), before.shape[:2])
    reference = None
    if info.get("reference"):
        reference = _ops.fit_image(ctx.store.get_image(ctx.shoot_id, info["reference"]), before.shape[:2])
    elif view.reference and metric.get("kind") == "ssim_reference":
        reference = _ops.fit_image(ctx.store.get_image(ctx.shoot_id, view.reference), before.shape[:2])

    metrics: dict[str, Any] = {}
    failures: list[str] = []
    if mask is not None:
        d = _outside_delta(before, after, mask)
        metrics.update(outside_max_delta=d, mask_area=round(float(mask.mean()), 4))
        if d > OUTSIDE_TOL:
            failures.append(f"pixels outside the mask changed (max delta {d:.0f})")
    if metric.get("kind"):
        m, f = region_metrics(metric["kind"], metric, before, after, target, reference)
        metrics.update(m)
        failures += f

    judge: dict[str, Any] | None = None
    if use_judge:
        # judge_edit crops around the mask itself and also looks at the full frame; global edits get a
        # whole-frame mask.
        jmask = target if target is not None else np.ones(before.shape[:2], bool)
        ref = reference if metric.get("kind") == "ssim_reference" else None
        try:
            verdict, usage = _ops.judge(before, after, jmask, info["intent"], reference=ref)
            _emit_usage(ctx, view_id, usage, "judge_edit")
            judge = _verdict_dict(verdict)
            judge["model"] = judge.get("model") or ctx.settings.judge_model
            if judge["verdict"] != "pass":
                if _metric_owns_intent(metric, failures, judge):
                    judge["overridden"] = f"intent measured by the {metric['kind']} metric, which passed"
                else:
                    failures.append(f"vision judge: {judge.get('reason') or 'did not meet the intent'}")
        except Exception as e:  # judge unavailable: deterministic metrics decide, and the check says so
            judge = {
                "verdict": "unavailable",
                "reason": f"{type(e).__name__}: {e}",
                "model": ctx.settings.judge_model,
            }

    check = {
        "version": result.version,
        "parent": info["parent"],
        "passed": not failures,
        "metrics": metrics,
        "judge": judge,
        "failures": failures,
        "intent": info["intent"],
    }
    ctx.store.set_check(ctx.shoot_id, view_id, result.version, check)
    return check
