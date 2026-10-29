"""Which photos a listing shows, and what to call them.

A listing shows the approved photos. QC can split one photo into a photo per camera position and the agent
leaves tight partial framings unapproved, so the shoot's own view list (21 entries for the 7-photo house: 7
blended originals and their 14 parts) is not what a buyer sees. Before anything is approved, every photo with
a version counts, so a shoot that skipped review still has a listing.
"""
from __future__ import annotations

import re

from ..store import Shoot, View


def listed_views(shoot: Shoot) -> list[View]:
    approved = [v for v in shoot.views if v.head and v.approved]
    return approved or [v for v in shoot.views if v.head]


def _room(view: View) -> str:
    """The room, without the numbering room labels carry ("Dining Room 2" is the dining room)."""
    base = view.room or re.sub(r"\s+\d+$", "", view.label or "") or view.id
    return base.replace("_", " ").strip().capitalize()


def display_names(views: list[View]) -> dict[str, str]:
    """A name per view: the room, plus "view N" when the listing shows that room more than once."""
    rooms = [_room(v) for v in views]
    seen: dict[str, int] = {}
    out = {}
    for v, room in zip(views, rooms):
        if rooms.count(room) > 1:
            seen[room] = seen.get(room, 0) + 1
            out[v.id] = f"{room}, view {seen[room]}"
        else:
            out[v.id] = room
    return out
