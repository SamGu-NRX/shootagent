"""Reel spec: the JSON the agent edits. `render.py` turns it into an mp4 deterministically."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..store import Listing, Shoot

Motion = Literal["pan_right", "pan_left", "push_in", "pull_out"]

# Showing order a listing agent would walk a buyer through. Unknown rooms go before the backyard.
ROOM_ORDER = [
    "exterior", "front", "entry", "foyer", "living", "family", "great room", "kitchen", "dining",
    "office", "den", "primary bedroom", "bedroom", "primary bathroom", "bathroom", "bath", "laundry",
    "garage", "basement", "patio", "backyard", "pool", "aerial",
]


class Slide(BaseModel):
    view: str
    version: str | None = None  # None = the view's head
    seconds: float = Field(3.0, ge=1.5, le=8)
    motion: Motion = "pan_right"
    focus: tuple[float, float] | None = None  # normalised point of interest the move settles on
    caption: str | None = None  # room label shown lower-left


class ReelSpec(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    slides: list[Slide]
    listing: Listing = Field(default_factory=Listing)
    stats_after: int | None = 1  # show the beds/baths/sqft card over slide index N (None hides it)
    crossfade: float = Field(0.45, ge=0, le=1.5)
    music: str | None = "synth"  # "synth" = generated track, a file path, or None for silence
    brand: str | None = None  # small wordmark on the end card


def room_rank(room: str | None) -> int:
    r = (room or "").lower()
    for i, key in enumerate(ROOM_ORDER):
        if key in r:
            return i
    return ROOM_ORDER.index("patio")


def default_spec(shoot: Shoot, view_ids: list[str] | None = None) -> ReelSpec:
    """Approved views (or every view when none are approved), in showing order, alternating moves."""
    views = [v for v in shoot.views if v.head and (view_ids is None or v.id in view_ids)]
    approved = [v for v in views if v.approved]
    views = approved or views
    if not views:
        raise ValueError("The shoot has no photos to put in a reel.")
    views = sorted(views, key=lambda v: (room_rank(v.room or v.label), shoot.views.index(v)))
    motions: list[Motion] = ["push_in", "pan_right", "pan_left", "pull_out"]
    slides = [
        Slide(
            view=v.id,
            motion=motions[i % len(motions)],
            caption=(v.room or v.label or "").strip().title() or None,
            seconds=3.6 if i == 0 else 3.0,
        )
        for i, v in enumerate(views)
    ]
    return ReelSpec(slides=slides, listing=shoot.listing, stats_after=1 if len(slides) > 2 else None)
