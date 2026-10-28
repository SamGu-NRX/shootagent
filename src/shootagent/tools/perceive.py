"""Perception tools: look at photos, run QC analysis, and build masks. None of these write a version."""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np
from pydantic import Field

from ..store import Finding
from . import _ops
from ._common import (
    ToolError,
    box_to_mask,
    contact_sheet,
    crop_norm,
    label,
    load_mask,
    load_view,
    mask_bbox,
    norm_box,
    overlay_masks,
    put_preview,
    resize_long,
    version_image,
)
from .base import ToolContext, ToolInput, ToolResult, tool

Box = list[float]


def view_status(view) -> dict:
    return {
        "view": view.id,
        "label": view.label or None,
        "room": view.room,
        "head": view.head,
        "edited": bool(view.head and view.versions and view.head != view.versions[0].id),
        "versions": len(view.versions),
        "open_findings": sum(1 for f in view.findings if f.status == "open"),
        "brackets": len(view.brackets),
        "has_reference": view.reference is not None,
        "approved": view.approved,
    }


def build_contact_sheet(ctx: ToolContext, view_ids: list[str] | None = None) -> tuple[str, list[dict]]:
    shoot = ctx.store.load(ctx.shoot_id)
    views = [v for v in shoot.views if view_ids is None or v.id in view_ids]
    tiles = []
    for v in views:
        if v.head is None:
            continue
        img = ctx.store.get_image(ctx.shoot_id, v.head_version.blob)
        tiles.append((v.id, resize_long(img, 480)))
    sheet = contact_sheet(tiles)
    return put_preview(ctx, sheet, long_edge=1568), [view_status(v) for v in views]


class ListViewsIn(ToolInput):
    pass


@tool(
    "list_views",
    "Show every view in the shoot as one labelled contact sheet, plus each view's status (current version, "
    "whether it was edited, open findings, bracket count). Use it to orient yourself or to recheck the shoot "
    "after several edits. You already received a contact sheet at the start, so do not call this first.",
    kind="perceive",
)
def list_views(ctx: ToolContext, args: ListViewsIn) -> ToolResult:
    blob, status = build_contact_sheet(ctx)
    return ToolResult(summary=f"Contact sheet of {len(status)} views.", data={"views": status}, images=[blob])


class ViewPhotoIn(ToolInput):
    view: str = Field(description="View id, e.g. 'v01'.")
    source: Literal["head", "finished", "reference", "bracket"] = Field(
        "head",
        description="'head' is the current edited version (default). 'finished' is AutoHDR's delivered photo. "
        "'reference' is our aligned fusion of the brackets: what the camera actually saw. 'bracket' is one raw "
        "exposure bracket (pick it with bracket_index).",
    )
    version: str | None = Field(None, description="A specific version id such as 'v01.2' instead of head.")
    bracket_index: int | None = Field(
        None, description="Bracket position (0-based) when source is 'bracket'."
    )
    crop: Box | None = Field(
        None,
        description="Zoom box [x0, y0, x1, y1] as fractions 0..1 of width and height. Omit for the whole photo.",
    )


@tool(
    "view_photo",
    "Look at one photo of a view, optionally zoomed into a box. Use it to inspect a region before deciding on "
    "an edit, to compare the edited photo with the bracket reference, or to confirm an edit worked. Each call "
    "costs an image; zoom with `crop` instead of viewing the whole photo repeatedly.",
    kind="perceive",
)
def view_photo(ctx: ToolContext, args: ViewPhotoIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    v = lv.view
    if args.version or args.source == "head":
        img, vid = version_image(ctx, v, args.version)
        name = vid
    elif args.source == "finished":
        if not v.finished:
            raise ToolError(f"View {v.id} has no AutoHDR finished photo.")
        img, name = ctx.store.get_image(ctx.shoot_id, v.finished), "finished"
    elif args.source == "reference":
        if not v.reference:
            raise ToolError(f"View {v.id} has no bracket reference (it has {len(v.brackets)} brackets).")
        img, name = ctx.store.get_image(ctx.shoot_id, v.reference), "reference"
    else:
        idx = args.bracket_index or 0
        match = [b for b in v.brackets if b.index == idx] or (
            v.brackets[idx : idx + 1] if idx < len(v.brackets) else []
        )
        if not match:
            raise ToolError(f"View {v.id} has brackets 0..{len(v.brackets) - 1}; no bracket {idx}.")
        img, name = ctx.store.get_image(ctx.shoot_id, match[0].blob), f"bracket {idx}"
    if args.crop:
        img = crop_norm(img, norm_box(args.crop))
    blob = put_preview(ctx, label(resize_long(img, 1024), f"{v.id} {name}"))
    h, w = img.shape[:2]
    return ToolResult(
        summary=f"{v.id} {name}{' (zoomed)' if args.crop else ''}, {w}x{h}px.",
        images=[blob],
        data={"view": v.id, "shown": name, "crop": args.crop},
    )


class QcIn(ToolInput):
    view: str = Field(description="View id to analyse.")


@tool(
    "run_qc_checks",
    "Compare a view's finished photo with its exposure brackets and list candidate defects: regions where "
    "the edit disagrees with what the camera saw (erased objects, ghosting from mixed brackets, colour shifts, "
    "fake fire, windows). Each candidate has an id, a type from AutoHDR's taxonomy, a region box and evidence, "
    "and comes with a 'finished | reference' crop. Candidates are not verdicts: a sky replacement, declutter "
    "or TV-screen fill is a legitimate edit; dismiss those with set_finding. Use once per view in QC mode.",
    kind="perceive",
)
def run_qc_checks(ctx: ToolContext, args: QcIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    if not lv.view.brackets:
        raise ToolError(f"View {args.view} has no brackets, so there is nothing to check it against.")
    # trace=None: this tool emits the finding events itself, with the ids the agent uses in set_finding.
    va = _ops.analyze_view(ctx.store, ctx.shoot_id, args.view, None)
    existing = {(f.type, tuple(f.region or ())): f for f in lv.view.findings}
    findings: list[Finding] = []
    new: list[Finding] = []
    for cand, mask_id in zip(va.candidates, va.candidate_masks):
        key = (cand["type"], tuple(cand.get("region") or ()))
        if key in existing:  # re-running on the same version must not duplicate findings
            findings.append(existing[key])
            continue
        evidence = {
            **cand.get("evidence", {}),
            "signal": cand.get("kind"),
            "suggested_fix": cand.get("suggested_fix"),
            "mask": mask_id,
        }
        f = Finding(
            type=cand["type"],
            explanation=cand["explanation"],
            region=cand.get("region"),
            confidence=cand.get("confidence"),
            evidence=evidence,
        )
        findings.append(f)
        new.append(f)
    if new:
        ctx.store.mutate(ctx.shoot_id, lambda s: s.view(args.view).findings.extend(new))
    by_id = {f.id: crop for f, crop in zip(findings, va.crops)}
    for f in new:
        ctx.trace.emit(
            "finding", view=args.view, finding=f.model_dump(), images=[by_id[f.id]] if by_id.get(f.id) else []
        )

    images = [c for c in va.crops[:3] if c]
    if va.groups_sheet and len(va.groups) > 1:
        images.insert(0, va.groups_sheet)
    cands = [
        {
            "finding": f.id,
            "type": f.type,
            "region": f.region,
            "confidence": f.confidence,
            "explanation": f.explanation,
            "mask": f.evidence.get("mask"),
            "suggested_fix": f.evidence.get("suggested_fix"),
            "status": f.status,
        }
        for f in findings
    ]
    data = {"view": args.view, "summary": va.summary, "candidates": cands}
    if len(va.groups) > 1:
        data["bracket_groups"] = va.groups
    summary = (
        f"{len(findings)} candidate(s) on {args.view}: "
        + ", ".join(f"{f.type} {f.id} ({(f.confidence or 0):.2f})" for f in findings)
        if findings
        else f"No candidate defects on {args.view}: {va.summary}"
    )
    return ToolResult(summary=summary, data=data, images=images[:MAX_QC_IMAGES])


MAX_QC_IMAGES = 4


class FindRegionsIn(ToolInput):
    view: str = Field(description="View id.")
    prompt: str = Field(
        description="Short noun phrase naming what to select, e.g. 'white trash bin', 'car', 'tv screen', "
        "'hardwood floor', 'lawn'. One kind of thing per call."
    )
    max_results: int = Field(3, ge=1, le=6, description="Most masks to return, best first.")


@tool(
    "find_regions",
    "Select things in a photo from a text prompt (open-vocabulary detection + segmentation). Returns mask ids "
    "with boxes and scores, and an overlay image with each mask tinted and named so you can confirm the right "
    "thing was selected. Always look at the overlay before editing with a mask; if it selected the wrong thing, "
    "try a more specific prompt or mask_from_box. Combine several results with combine_masks.",
    kind="perceive",
)
def find_regions(ctx: ToolContext, args: FindRegionsIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    img, vid = version_image(ctx, lv.view)
    found = _ops.segment_find(img, args.prompt)[: args.max_results]
    if not found:
        return ToolResult(
            summary=f"Nothing matching {args.prompt!r} found on {args.view}.", data={"masks": []}, ok=True
        )
    out, masks, names = [], [], []
    for f in found:
        m = _ops.fit_mask(f.mask, img.shape[:2])
        if not m.any():
            continue
        rec = ctx.store.add_mask(ctx.shoot_id, args.view, m, source="grounding+sam", query=args.prompt)
        masks.append(m)
        names.append(rec.id)
        out.append(
            {
                "mask": rec.id,
                "label": getattr(f, "label", args.prompt),
                "score": round(float(f.score), 3),
                "box": [round(x, 4) for x in rec.bbox],
                "area": round(rec.area, 4),
            }
        )
    blob = put_preview(ctx, overlay_masks(img, masks, names))
    summary = f"Found {len(out)} region(s) for {args.prompt!r} on {args.view}: " + ", ".join(
        f"{o['mask']} (score {o['score']}, {o['area'] * 100:.1f}% of frame)" for o in out
    )
    return ToolResult(
        summary=summary,
        data={"view": args.view, "version": vid, "masks": out},
        images=[blob],
        mask=out[0]["mask"] if out else None,
    )


class MaskFromBoxIn(ToolInput):
    view: str = Field(description="View id.")
    box: Box = Field(description="[x0, y0, x1, y1] as fractions 0..1 of width and height.")
    refine: bool = Field(
        True,
        description="True snaps the box to the object inside it (segmentation). False keeps the plain rectangle.",
    )


@tool(
    "mask_from_box",
    "Make a mask from a box you read off a photo. With refine=true the mask follows the main object inside "
    "the box; with refine=false it is the rectangle itself (good for a watermark area, a sky band, or a "
    "region with no clear object). Use when find_regions misses or picks the wrong thing.",
    kind="perceive",
)
def mask_from_box(ctx: ToolContext, args: MaskFromBoxIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    img, _ = version_image(ctx, lv.view)
    box = list(norm_box(args.box))
    if args.refine:
        m = _ops.fit_mask(_ops.segment_box(img, box), img.shape[:2])
        source = "box+sam"
    else:
        m = box_to_mask(box, img.shape[:2])
        source = "box"
    if not m.any():
        raise ToolError("The refined mask came back empty; retry with refine=false or a tighter box.")
    rec = ctx.store.add_mask(ctx.shoot_id, args.view, m, source=source)
    blob = put_preview(ctx, overlay_masks(img, [m], [rec.id]))
    return ToolResult(
        summary=f"Mask {rec.id} on {args.view} covers {rec.area * 100:.1f}% of the frame.",
        data={"mask": rec.id, "box": rec.bbox, "area": round(rec.area, 4)},
        images=[blob],
        mask=rec.id,
    )


class CombineMasksIn(ToolInput):
    view: str = Field(description="View id; every mask must belong to it.")
    masks: list[str] = Field(
        min_length=1, description="Mask ids, in order. The first is the base for subtract."
    )
    op: Literal["union", "intersect", "subtract"] = Field("union", description="How to combine them.")


@tool(
    "combine_masks",
    "Combine masks into one: union (several objects at once), intersect, or subtract (first minus the rest, "
    "e.g. wall minus window). Returns the new mask id and an overlay.",
    kind="perceive",
)
def combine_masks(ctx: ToolContext, args: CombineMasksIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    img, _ = version_image(ctx, lv.view)
    ms = [load_mask(ctx, lv.shoot, lv.view, m, img.shape[:2]) for m in args.masks]
    out = ms[0].copy()
    for m in ms[1:]:
        out = out | m if args.op == "union" else out & m if args.op == "intersect" else out & ~m
    if not out.any():
        raise ToolError(f"The {args.op} of {', '.join(args.masks)} is empty.")
    rec = ctx.store.add_mask(ctx.shoot_id, args.view, out, source=f"combine:{args.op}")
    blob = put_preview(ctx, overlay_masks(img, [out], [rec.id]))
    return ToolResult(
        summary=f"Mask {rec.id} = {args.op} of {', '.join(args.masks)} ({rec.area * 100:.1f}% of frame).",
        data={"mask": rec.id, "box": rec.bbox, "area": round(rec.area, 4)},
        images=[blob],
        mask=rec.id,
    )


class GrowMaskIn(ToolInput):
    view: str = Field(description="View id.")
    mask: str = Field(description="Mask id to grow or shrink.")
    pixels: int = Field(
        ge=-200,
        le=200,
        description="Pixels to dilate (positive) or erode (negative), measured on a 1024-px-long-edge copy of the "
        "photo. 6-12 covers shadows and halo around an object before removal.",
    )


@tool(
    "grow_mask",
    "Grow (or shrink) a mask by some pixels. Grow a removal mask a little so the object's edge, shadow and "
    "halo are inside it; shrink a mask that bleeds onto neighbouring surfaces.",
    kind="perceive",
)
def grow_mask(ctx: ToolContext, args: GrowMaskIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    img, _ = version_image(ctx, lv.view)
    m = load_mask(ctx, lv.shoot, lv.view, args.mask, img.shape[:2])
    px = max(1, round(abs(args.pixels) * max(img.shape[:2]) / 1024))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    fn = cv2.dilate if args.pixels > 0 else cv2.erode
    out = fn(m.astype(np.uint8), k) > 0
    if not out.any():
        raise ToolError(f"Shrinking {args.mask} by {args.pixels}px leaves nothing; use a smaller value.")
    rec = ctx.store.add_mask(ctx.shoot_id, args.view, out, source=f"grow:{args.pixels}")
    blob = put_preview(ctx, overlay_masks(img, [out], [rec.id]))
    return ToolResult(
        summary=f"Mask {rec.id} is {args.mask} grown by {args.pixels}px ({rec.area * 100:.1f}% of frame).",
        data={"mask": rec.id, "box": rec.bbox, "area": round(rec.area, 4)},
        images=[blob],
        mask=rec.id,
    )


class SampleColorIn(ToolInput):
    view: str = Field(description="View id.")
    mask: str | None = Field(None, description="Mask id of the area to sample.")
    box: Box | None = Field(None, description="Or a box [x0, y0, x1, y1] (fractions 0..1) to sample.")
    source: Literal["head", "finished", "brackets"] = Field(
        "head",
        description="'head' samples the current photo, 'finished' AutoHDR's delivery, 'brackets' the "
        "exposure-normalised bracket fusion (the true surface colour, before any styling).",
    )


@tool(
    "sample_color",
    "Read the average colour of a masked area or box as a hex value. Sample source='brackets' to learn a "
    "surface's true colour when an edit shifted it (paint or floor colour defects), or sample another view to "
    "match colours across the shoot.",
    kind="perceive",
)
def sample_color(ctx: ToolContext, args: SampleColorIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    v = lv.view
    if args.source == "head":
        img, _ = version_image(ctx, v)
    elif args.source == "finished":
        if not v.finished:
            raise ToolError(f"View {v.id} has no finished photo.")
        img = ctx.store.get_image(ctx.shoot_id, v.finished)
    else:
        if not v.reference:
            raise ToolError(f"View {v.id} has no bracket reference to sample.")
        img = ctx.store.get_image(ctx.shoot_id, v.reference)
    if args.mask:
        m = load_mask(ctx, lv.shoot, v, args.mask, img.shape[:2])
    elif args.box:
        m = box_to_mask(args.box, img.shape[:2])
    else:
        raise ToolError("Give a mask id or a box to sample.")
    hexval = _ops.sample_hex(img, m)
    return ToolResult(
        summary=f"{v.id} {args.source} colour in {'mask ' + args.mask if args.mask else 'box'}: {hexval}.",
        data={"view": v.id, "hex": hexval, "source": args.source, "region": mask_bbox(m)},
    )
