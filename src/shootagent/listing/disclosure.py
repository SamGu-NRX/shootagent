"""California AB 723 (2024): a real-estate ad that uses a digitally altered image must say so and give access to
the unaltered image. This builds that record for one listing: each published photo beside its original, what
changed (AutoHDR's delivered edit, ShootAgent's own versions, fixed QC findings), and the film's AI-generated
motion clips from its manifest."""
from __future__ import annotations

from ..store import Shoot, View
from .film import read_film
from .views import display_names, listed_views

def original_of(view: View) -> tuple[str | None, str]:
    """The least-processed image of the view, and which kind it is: "fusion" (ShootAgent's fusion of the raw
    exposures), "exposure" (the camera's middle exposure) or "delivered" (the editor's photo, when no raw
    exposures exist)."""
    if view.reference:
        return view.reference, "fusion"
    if view.brackets:
        b = sorted(view.brackets, key=lambda b: b.index)[len(view.brackets) // 2]
        return b.blob, "exposure"
    if view.finished:
        return view.finished, "delivered"
    return None, ""


def changes_of(view: View) -> list[dict]:
    """What changed between the original and the published photo, oldest first, as records the page words in
    the reader's language: {kind: "hdr", exposures}, {kind: "edit", op, note} for each ShootAgent version on the
    head's chain, and {kind: "fix", finding, explanation} for each fixed QC finding."""
    out: list[dict] = []
    # A view QC split off a blended photo is ShootAgent's own fusion of its camera position's exposures (its
    # "regroup" version below); the editor's HDR photo is the blended parent, so it is not this photo's history.
    if view.finished and view.brackets and not view.derived_from:
        out.append({"kind": "hdr", "exposures": len(view.brackets)})
    chain: list = []
    vid = view.head
    by_id = {v.id: v for v in view.versions}
    while vid and vid in by_id:
        chain.append(by_id[vid])
        vid = by_id[vid].parent
    for v in reversed(chain):
        if v.op not in ("ingest", "merge"):
            out.append({"kind": "edit", "op": v.op, "note": v.note})
    for f in view.findings:
        if f.status == "fixed":
            out.append({"kind": "fix", "finding": f.type, "explanation": f.explanation})
    return out


def build(shoot: Shoot, store) -> dict:
    film = read_film(store, shoot.id)
    listed = listed_views(shoot)
    ids = {v.id for v in listed}
    # the film's photos first, in play order, then the rest of the listing's photos
    order = []
    if film:
        for s in film["shots"]:
            order += [v for v in (s["view"], s["end_view"]) if v in ids and v not in order]
    order += [v.id for v in listed if v.id not in order]
    names = display_names([shoot.view(v) for v in order])  # "view 1, 2, ..." counts down the page
    photos = []
    for vid in order:
        view = shoot.view(vid)
        original, original_kind = original_of(view)
        changes = changes_of(view)
        photos.append({
            "view": view.id,
            "room": names[vid],
            "published": view.head_version.blob,
            "original": original,
            "original_kind": original_kind,
            "altered": bool(changes) or original != view.head_version.blob,
            "changes": changes,
        })
    clips, rejected = [], []
    for s in (film or {}).get("shots", []):
        label = next(iter(s["labels"].values()), "") if s["labels"] else ""
        if s["ai_label"]:
            clips.append({"shot": s["index"] + 1, "label": label, "view": s["view"], "end_view": s["end_view"]})
        for a in s["attempts"]:
            if not a["passed"]:
                rejected.append({"shot": s["index"] + 1, "label": label, "reasons": a["reasons"], "changes": a["changes"]})
    return {
        "law": "California AB 723",
        "photos": photos,
        "film": None if film is None else {
            "ai_clips": clips,
            "rejected_clips": rejected,
            "label": film["disclosure_label"],
            "complete": film["complete"],
        },
    }
