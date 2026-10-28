"""Edit tools. Each reads the view's head version, writes a new version whose parent is that head, and
returns a before/after crop. Masked edits are composited back through the mask so pixels outside it stay
byte-identical. The agent loop verifies every new version (agent/verify.py) and reverts it on failure.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import Field

from . import _ops
from ._common import (
    Loaded,
    ToolError,
    before_after,
    load_mask,
    load_view,
    mask_bbox,
    parse_hex,
    version_image,
)
from .base import ToolContext, ToolInput, ToolResult, tool

INTENT = Field(
    description="One sentence saying what this edit should achieve, as the customer would judge it "
    "(e.g. 'the white trash bin by the garage is gone and the driveway behind it looks natural'). "
    "The self-check judges the result against this sentence."
)


class EditIn(ToolInput):
    view: str = Field(description="View id to edit.")
    intent: str = INTENT


def commit(
    ctx: ToolContext,
    lv: Loaded,
    before: np.ndarray,
    after: np.ndarray,
    parent: str,
    op: str,
    params: dict[str, Any],
    intent: str,
    mask_id: str | None = None,
    mask: np.ndarray | None = None,
    metric: dict[str, Any] | None = None,
    summary: str = "",
) -> ToolResult:
    if after.shape != before.shape or after.dtype != np.uint8:
        raise ToolError(
            f"{op} produced a {after.shape} {after.dtype} image for a {before.shape} photo; this is a bug."
        )
    if np.array_equal(after, before):
        raise ToolError(f"{op} changed nothing on {lv.view.id}; adjust the parameters or the mask.")
    version = ctx.store.add_version(
        ctx.shoot_id,
        lv.view.id,
        after,
        op=op,
        params=params,
        parent=parent,
        mask=mask_id,
        note=intent,
        run=ctx.trace.id,
    )
    bbox = mask_bbox(mask) if mask is not None else None
    blob = before_after(ctx, before, after, bbox)
    return ToolResult(
        summary=summary or f"{op} on {lv.view.id} wrote {version.id}.",
        data={
            "view": lv.view.id,
            "version": version.id,
            "parent": parent,
            "verify": {
                "intent": intent,
                "mask": mask_id,
                "parent": parent,
                "metric": metric or {},
                "bbox": bbox,
            },
        },
        images=[blob],
        version=version.id,
        mask=mask_id,
    )


def _optional_mask(ctx: ToolContext, lv: Loaded, mask_id: str | None, shape) -> np.ndarray | None:
    return load_mask(ctx, lv.shoot, lv.view, mask_id, shape) if mask_id else None


# ---- tone and colour -------------------------------------------------------------------------------------


class ExposureIn(EditIn):
    ev: float = Field(
        ge=-3, le=3, description="Stops to add: 0.5 is half a stop brighter, -0.33 a third darker."
    )
    mask: str | None = Field(None, description="Mask id to confine the change to. Omit for the whole photo.")


@tool(
    "adjust_exposure",
    "Brighten or darken the whole photo or a masked region by some stops, keeping highlights from clipping. "
    "Use for 'brighter/darker' requests and dull rooms. Not for colour casts (adjust_white_balance) or for "
    "blown windows (restore_from_brackets).",
    kind="edit",
)
def adjust_exposure(ctx: ToolContext, args: ExposureIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    before, vid = version_image(ctx, lv.view)
    mask = _optional_mask(ctx, lv, args.mask, before.shape[:2])
    after = _ops.exposure(before, args.ev, mask)
    return commit(
        ctx,
        lv,
        before,
        after,
        vid,
        "adjust_exposure",
        {"ev": args.ev},
        args.intent,
        args.mask,
        mask,
        metric={"kind": "ev", "ev": args.ev},
        summary=f"Exposure {args.ev:+.2f} EV on {lv.view.id}{' in ' + args.mask if args.mask else ''}.",
    )


class WhiteBalanceIn(EditIn):
    temperature: float = Field(
        0,
        ge=-100,
        le=100,
        description="Negative cools (bluer), positive warms (more amber). 10-25 is a gentle shift.",
    )
    tint: float = Field(0, ge=-100, le=100, description="Negative adds green, positive adds magenta.")
    mask: str | None = Field(None, description="Mask id to confine the change to. Omit for the whole photo.")


@tool(
    "adjust_white_balance",
    "Warm, cool or re-tint the whole photo or a masked region. Use for colour casts (yellow tungsten walls, "
    "blue shade, green spill from a lawn). Not for changing a surface to a specific colour (recolor_region).",
    kind="edit",
)
def adjust_white_balance(ctx: ToolContext, args: WhiteBalanceIn) -> ToolResult:
    if args.temperature == 0 and args.tint == 0:
        raise ToolError("Set temperature or tint; both are 0.")
    lv = load_view(ctx, args.view)
    before, vid = version_image(ctx, lv.view)
    mask = _optional_mask(ctx, lv, args.mask, before.shape[:2])
    after = _ops.white_balance(before, args.temperature, args.tint, mask)
    return commit(
        ctx,
        lv,
        before,
        after,
        vid,
        "adjust_white_balance",
        {"temperature": args.temperature, "tint": args.tint},
        args.intent,
        args.mask,
        mask,
        summary=f"White balance temperature {args.temperature:+g}, tint {args.tint:+g} on {lv.view.id}.",
    )


class RecolorIn(EditIn):
    mask: str = Field(description="Mask id of the surface to recolour (a wall, a floor, a door).")
    hex: str = Field(description="Target colour as hex, e.g. '#C8C2B7'.")


@tool(
    "recolor_region",
    "Change a surface to a target colour while keeping its texture, shading and reflections: wall paint, "
    "floor stain, cabinets, a door. Use with a hex from the customer, from sample_color on another view, or "
    "from the brackets (source='brackets') to undo a colour shift. The mask must cover only that surface.",
    kind="edit",
)
def recolor_region(ctx: ToolContext, args: RecolorIn) -> ToolResult:
    parse_hex(args.hex)
    lv = load_view(ctx, args.view)
    before, vid = version_image(ctx, lv.view)
    mask = load_mask(ctx, lv.shoot, lv.view, args.mask, before.shape[:2])
    raw, region = _ops.recolor(before, mask, args.hex)
    rec = ctx.store.add_mask(ctx.shoot_id, lv.view.id, region, source="affected:recolor_region")
    after = _ops.composite(before, raw, region)
    # The colour metric is measured on the surface mask; leaks are measured outside its feathered edge.
    return commit(
        ctx,
        lv,
        before,
        after,
        vid,
        "recolor_region",
        {"hex": args.hex, "object_mask": args.mask},
        args.intent,
        rec.id,
        region,
        metric={"kind": "delta_e", "hex": args.hex, "region_mask": args.mask},
        summary=f"Recoloured {args.mask} on {lv.view.id} toward {args.hex}.",
    )


# ---- object removal and fills ---------------------------------------------------------------------------


class RemoveIn(EditIn):
    mask: str = Field(
        description="Mask id covering the object, its shadow and a few pixels of margin (grow_mask)."
    )
    method: Literal["lama", "generative"] = Field(
        "lama",
        description="'lama' (fast, local) fills from surrounding texture: best for small or mid-size objects on "
        "simple backgrounds (bins, cords, signs on a lawn). 'generative' redraws the area with an image model: "
        "use for large objects, complex backgrounds (cars in front of a house), or after lama left a smudge.",
    )
    prompt: str | None = Field(
        None, description="For 'generative': what should be there instead, e.g. 'empty concrete driveway'."
    )


@tool(
    "remove_object",
    "Erase an object inside a mask and reconstruct what was behind it. Use for customer removal requests "
    "(cars, trash bins, cords, lockboxes, people, tripods) and for leftover photographer/tripod reflections. "
    "Pixels outside the mask are unchanged. If the check fails with lama, retry with method='generative' or a "
    "larger mask.",
    kind="edit",
)
def remove_object(ctx: ToolContext, args: RemoveIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    before, vid = version_image(ctx, lv.view)
    mask = load_mask(ctx, lv.shoot, lv.view, args.mask, before.shape[:2])
    usage: dict[str, Any] = {}
    if args.method == "lama":
        raw, region = _ops.lama(before, mask)
    else:
        raw, region, usage = _ops.generative(
            ctx,
            before,
            mask,
            args.prompt or "remove the object; reconstruct the background naturally",
            view=lv.view.id,
        )
    res = _commit_region(
        ctx,
        lv,
        before,
        raw,
        region,
        vid,
        "remove_object",
        {"method": args.method, "prompt": args.prompt, "object_mask": args.mask},
        args.intent,
        f"Removed {args.mask} on {lv.view.id} with {args.method}.",
    )
    res.data.update(usage)
    return res


def _commit_region(
    ctx: ToolContext,
    lv: Loaded,
    before: np.ndarray,
    raw: np.ndarray,
    region: np.ndarray,
    parent: str,
    op: str,
    params: dict[str, Any],
    intent: str,
    summary: str,
) -> ToolResult:
    """Commit an inpaint/generative result. The model may change the object mask grown by its edge and
    feather margins; that region becomes the version's mask, so the self-check measures leaks beyond it."""
    region = _ops.fit_mask(region, before.shape[:2])
    rec = ctx.store.add_mask(ctx.shoot_id, lv.view.id, region, source=f"affected:{op}")
    after = _ops.composite(before, raw, region)
    return commit(ctx, lv, before, after, parent, op, params, intent, rec.id, region, summary=summary)


class GenFillIn(EditIn):
    mask: str = Field(description="Mask id of the area to redraw.")
    prompt: str = Field(
        description="What the area should show, concretely: 'lit crystal chandelier, warm light'."
    )
    reference: str | None = Field(
        None, description="Optional blob name of a reference image to match (listed in the request, if any)."
    )


@tool(
    "generative_fill",
    "Redraw a masked area from a text prompt with an image model: turn on lamps or a chandelier, add fire to a "
    "fireplace, green a patchy lawn, smooth wrinkled bedding, replace a sky. Slower and costlier than other "
    "tools; prefer adjust/recolor/remove when they can do the job. Pixels outside the mask are unchanged.",
    kind="edit",
)
def generative_fill(ctx: ToolContext, args: GenFillIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    before, vid = version_image(ctx, lv.view)
    mask = load_mask(ctx, lv.shoot, lv.view, args.mask, before.shape[:2])
    refs = (
        [("reference image from the customer", ctx.store.get_image(ctx.shoot_id, args.reference))]
        if args.reference
        else None
    )
    raw, region, usage = _ops.generative(ctx, before, mask, args.prompt, refs, view=lv.view.id)
    res = _commit_region(
        ctx,
        lv,
        before,
        raw,
        region,
        vid,
        "generative_fill",
        {"prompt": args.prompt, "reference": args.reference, "object_mask": args.mask},
        args.intent,
        f"Generative fill of {args.mask} on {lv.view.id}: {args.prompt!r}.",
    )
    res.data.update(usage)
    return res


class RestoreIn(EditIn):
    mask: str = Field(description="Mask id of the area to bring back from the brackets.")


@tool(
    "restore_from_brackets",
    "Replace a masked area with what the camera actually captured (the aligned bracket fusion), restyled to "
    "match the edited photo's tone. Use when the edit erased a real object by mistake, invented structure "
    "(fake fire, warped window), ghosted, or blew out a window. Not for removals the customer asked for.",
    kind="edit",
)
def restore_from_brackets(ctx: ToolContext, args: RestoreIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    if not lv.view.brackets:
        raise ToolError(f"View {lv.view.id} has no brackets; restore is impossible. Try another tool.")
    before, vid = version_image(ctx, lv.view)
    mask = load_mask(ctx, lv.shoot, lv.view, args.mask, before.shape[:2])
    try:
        ref, valid = _ops.reference_for_fix(ctx.store, ctx.shoot_id, lv.view.id, lv.view.version(vid).blob)
    except ValueError as e:
        raise ToolError(f"The brackets could not be registered to {vid} ({e}); try another tool.") from None
    raw, region, info = _ops.restore_from_reference(before, ref, mask, valid)
    rec = ctx.store.add_mask(ctx.shoot_id, lv.view.id, region, source="affected:restore_from_brackets")
    after = _ops.composite(before, raw, region)
    res = commit(
        ctx,
        lv,
        before,
        after,
        vid,
        "restore_from_brackets",
        {"object_mask": args.mask, **info},
        args.intent,
        rec.id,
        region,
        metric={"kind": "ssim_reference", "region_mask": args.mask},
        summary=f"Restored {args.mask} on {lv.view.id} from the brackets.",
    )
    res.data["verify"]["reference"] = ctx.store.put_image(ctx.shoot_id, ref, fmt="jpg")
    return res


class PlaceOnScreenIn(EditIn):
    mask: str = Field(description="Mask id of the screen (TV, monitor) or frame to fill.")
    reference_image: str = Field(
        description="Blob name of the image to place (from the request's references)."
    )


@tool(
    "place_on_screen",
    "Put an image onto a TV, monitor or picture frame with correct perspective (fits the screen's four corners). "
    "Use when the customer supplies an image for the TV. Pixels outside the mask are unchanged.",
    kind="edit",
)
def place_on_screen(ctx: ToolContext, args: PlaceOnScreenIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    before, vid = version_image(ctx, lv.view)
    mask = load_mask(ctx, lv.shoot, lv.view, args.mask, before.shape[:2])
    try:
        ref = ctx.store.get_image(ctx.shoot_id, args.reference_image)
    except FileNotFoundError:
        raise ToolError(
            f"No image blob {args.reference_image!r} in this shoot; use one listed in the request."
        ) from None
    raw, region = _ops.place_on_screen(before, mask, ref)
    rec = ctx.store.add_mask(ctx.shoot_id, lv.view.id, region, source="affected:place_on_screen")
    after = _ops.composite(before, raw, region)
    return commit(
        ctx,
        lv,
        before,
        after,
        vid,
        "place_on_screen",
        {"reference_image": args.reference_image, "object_mask": args.mask},
        args.intent,
        rec.id,
        region,
        metric={"region_mask": args.mask},
        summary=f"Placed {args.reference_image} on {args.mask} in {lv.view.id}.",
    )


# ---- overlays -------------------------------------------------------------------------------------------


class WatermarkIn(EditIn):
    text: str = Field(min_length=1, max_length=60, description="Watermark text.")
    position: Literal["bottom-right", "bottom-left", "top-right", "top-left", "center"] = "bottom-right"
    opacity: float = Field(0.85, ge=0.1, le=1.0, description="0.1 faint .. 1.0 solid.")
    scale: float = Field(
        0.032, ge=0.01, le=0.2, description="Text cap height as a fraction of the photo's short edge."
    )


@tool(
    "add_watermark",
    "Stamp a text watermark (brokerage, photographer, 'Virtually staged') in a corner. Only the watermark's "
    "own area changes. Apply the same parameters on every view so the shoot is consistent.",
    kind="edit",
)
def add_watermark(ctx: ToolContext, args: WatermarkIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    before, vid = version_image(ctx, lv.view)
    after, area = _ops.watermark(before, args.text, args.position, args.opacity, args.scale)
    rec = ctx.store.add_mask(ctx.shoot_id, lv.view.id, area, source="watermark", query=args.text)
    return commit(
        ctx,
        lv,
        before,
        after,
        vid,
        "add_watermark",
        {"text": args.text, "position": args.position, "opacity": args.opacity, "scale": args.scale},
        args.intent,
        rec.id,
        area,
        summary=f"Watermark {args.text!r} {args.position} on {lv.view.id}.",
    )


class LotLinesIn(EditIn):
    points: list[list[float]] = Field(
        min_length=2,
        description="Property boundary corners in order, each [x, y] as fractions 0..1 of width/height.",
    )
    label: str | None = Field(None, description="Optional label drawn near the outline, e.g. '0.42 acres'.")
    closed: bool = Field(True, description="Close the outline back to the first point.")


@tool(
    "draw_lot_lines",
    "Draw a property boundary outline (and an optional label) on an aerial or exterior photo. Only the drawn "
    "lines and label change pixels.",
    kind="edit",
)
def draw_lot_lines(ctx: ToolContext, args: LotLinesIn) -> ToolResult:
    if any(len(p) != 2 for p in args.points):
        raise ToolError("Each point is [x, y] with values 0..1.")
    lv = load_view(ctx, args.view)
    before, vid = version_image(ctx, lv.view)
    after, area = _ops.lot_lines(before, args.points, args.label, args.closed)
    rec = ctx.store.add_mask(ctx.shoot_id, lv.view.id, area, source="lot_lines", query=args.label)
    return commit(
        ctx,
        lv,
        before,
        after,
        vid,
        "draw_lot_lines",
        {"points": args.points, "label": args.label, "closed": args.closed},
        args.intent,
        rec.id,
        area,
        summary=f"Lot lines ({len(args.points)} points) on {lv.view.id}.",
    )


# ---- structure and history ------------------------------------------------------------------------------


class RegroupIn(ToolInput):
    view: str = Field(description="View id whose brackets mix two or more camera positions.")
    groups: list[list[int]] = Field(
        min_length=2,
        description="Bracket indices per camera position, e.g. [[0,1,2],[3,4]] from run_qc_checks evidence.",
    )


@tool(
    "regroup_brackets",
    "Split a view whose brackets came from different camera positions (ghosting_grouping) into one view per "
    "group, each fused from its own brackets and styled like the original. Returns the new view ids; continue "
    "QC on them. Use only when run_qc_checks reports a grouping problem with suggested groups.",
    kind="edit",
)
def regroup_brackets(ctx: ToolContext, args: RegroupIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    n = len(lv.view.brackets)
    flat = [i for g in args.groups for i in g]
    if sorted(flat) != sorted(set(flat)) or any(i < 0 or i >= n for i in flat):
        raise ToolError(f"Groups must use each bracket index 0..{n - 1} at most once; got {args.groups}.")
    new_ids = list(_ops.regroup_view(ctx.store, ctx.shoot_id, args.view, args.groups))
    from .perceive import build_contact_sheet

    blob, _ = build_contact_sheet(ctx, [args.view, *new_ids])
    return ToolResult(
        summary=f"Split {args.view} into {len(new_ids)} view(s): {', '.join(new_ids)}.",
        data={"view": args.view, "new_views": new_ids},
        images=[blob],
    )


class RevertIn(ToolInput):
    view: str = Field(description="View id.")
    version: str = Field(description="Version id to make current again, e.g. 'v03.0' for the original.")


@tool(
    "revert",
    "Make an earlier version current again (undo). Version '<view>.0' is the photo as ingested. Use when an "
    "edit you made turned out wrong after all, or when a request is withdrawn.",
    kind="edit",
)
def revert(ctx: ToolContext, args: RevertIn) -> ToolResult:
    lv = load_view(ctx, args.view)
    try:
        lv.view.version(args.version)
    except KeyError:
        raise ToolError(
            f"{args.view} has no version {args.version!r}; versions: "
            f"{', '.join(v.id for v in lv.view.versions)}."
        ) from None
    ctx.store.set_head(ctx.shoot_id, args.view, args.version)
    return ToolResult(
        summary=f"{args.view} head moved back to {args.version}.",
        data={"view": args.view, "head": args.version},
    )
