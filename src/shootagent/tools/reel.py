"""Reel tools: label rooms, then render the reel from a spec the agent writes.

The agent decides order, captions, camera moves and focus points; `reel.render` draws the frames.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ..reel.render import render_reel as _render
from ..reel.spec import ReelSpec, Slide
from ._common import ToolError, contact_sheet, put_preview
from .base import ToolContext, ToolInput, ToolResult, tool


class RoomLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: str
    room: str = Field(description="Room as a buyer would name it: 'exterior', 'kitchen', 'primary bedroom'...")


class LabelViewsIn(ToolInput):
    labels: list[RoomLabel] = Field(min_length=1)


@tool(
    "label_views",
    "Record which room each view shows. The labels order the reel and become its captions, so use the "
    "words a listing agent would: 'exterior', 'living room', 'kitchen', 'primary bedroom', 'bathroom'.",
    kind="shoot",
)
def label_views(ctx: ToolContext, args: LabelViewsIn) -> ToolResult:
    labels = {lab.view: lab.room.strip().lower() for lab in args.labels}

    def apply(shoot):
        ids = {v.id for v in shoot.views}
        unknown = sorted(set(labels) - ids)
        if unknown:
            raise ToolError(f"Unknown views {', '.join(unknown)}; views are {', '.join(sorted(ids))}.")
        seen: dict[str, int] = {}
        for v in shoot.views:
            room = labels.get(v.id, v.room)
            if v.id in labels:
                v.room = room
            if room:
                # The display label follows the room ("Kitchen", "Bedroom 2"); dataset ids stay in meta.
                seen[room] = seen.get(room, 0) + 1
                if v.id in labels:
                    v.label = room.title() + (f" {seen[room]}" if seen[room] > 1 else "")

    ctx.store.mutate(ctx.shoot_id, apply)
    return ToolResult(summary="Labelled " + ", ".join(f"{k} as {v}" for k, v in labels.items()) + ".")


class SlideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: str
    caption: str | None = Field(None, description="Short room label shown on screen, e.g. 'Kitchen'.")
    motion: Literal["pan_right", "pan_left", "push_in", "pull_out"] = Field(
        description="Camera move. Pans suit wide rooms; push_in suits a hero feature; pull_out reveals."
    )
    focus: tuple[float, float] | None = Field(
        None, description="Point of interest [x, y] as fractions 0..1 that a push or pull settles on."
    )
    seconds: float = Field(3.0, ge=2.0, le=6.0)


class RenderReelIn(ToolInput):
    slides: list[SlideIn] = Field(min_length=2, max_length=14, description="Photos in showing order.")
    stats_after: int | None = Field(
        1, description="Index of the slide that shows the beds/baths/sq ft card, or null to hide it."
    )
    brand: str | None = Field(None, description="Small wordmark on the end card (brokerage or photographer).")


@tool(
    "render_reel",
    "Render the 9:16 listing reel: your slides in order, with slow camera moves, the listing's address and "
    "price on the first photo, a stats card, room captions and an end card, over a generated music bed. "
    "Returns a strip of frames so you can check framing and legibility. Re-render with changes if needed.",
    kind="shoot",
)
def render_reel(ctx: ToolContext, args: RenderReelIn) -> ToolResult:
    shoot = ctx.store.load(ctx.shoot_id)
    ids = {v.id for v in shoot.views if v.head}
    unknown = [s.view for s in args.slides if s.view not in ids]
    if unknown:
        raise ToolError(f"Unknown or empty views {', '.join(unknown)}.")
    spec = ReelSpec(
        slides=[Slide(**s.model_dump()) for s in args.slides],
        listing=shoot.listing,
        stats_after=args.stats_after if args.stats_after is not None and args.stats_after < len(args.slides) else None,
        brand=args.brand,
    )
    path = _render(ctx.store, ctx.shoot_id, spec, trace=None)
    strip = _frame_strip(path, spec)
    seconds = sum(s.seconds for s in spec.slides) - spec.crossfade * (len(spec.slides) - 1) + 2.6
    return ToolResult(
        summary=f"Rendered a {seconds:.0f}-second reel of {len(spec.slides)} photos.",
        data={"path": str(path), "seconds": round(seconds, 1), "url": f"/api/shoots/{ctx.shoot_id}/reel/latest.mp4"},
        images=[put_preview(ctx, strip, long_edge=1568)],
    )


def _frame_strip(path, spec: ReelSpec) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    tiles = []
    for k, frac in enumerate((0.06, 0.25, 0.45, 0.65, 0.97)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * frac))
        ok, frame = cap.read()
        if ok:
            tiles.append((f"{frac:.0%}", cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    cap.release()
    if not tiles:
        raise ToolError("The reel rendered but no frames could be read back; check ffmpeg's output.")
    return contact_sheet(tiles, cols=len(tiles), tile_w=300)
