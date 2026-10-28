"""What changed between what the camera captured and each listing photo, and what a buyer must be told.

ShootAgent can answer this where an editing tool alone cannot: it keeps the camera's own brackets, every
version it made with the edit that made it, and the QC verdict on what the upstream editor (AutoHDR) did.
Every change falls in one class:

* alteration          adds, removes or materially changes what the scene shows (AB 723: disclose it and pair
                      the photo with the unaltered original)
* adjustment          exposure, colour correction, straightening; or removing the photographer's own gear
* restoration         a QC fix that brings the photo back to what the camera captured (an erased lamp restored,
                      two camera positions re-merged, a drifted wall colour returned to the brackets' colour)
* overlay             text and graphics laid over the photo (watermark, lot lines)
* possible_alteration a QC finding nobody has ruled on yet; the photographer decides before publishing

Only the versions on the head's ancestry count: reverted attempts never reached the listing photo.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..store import Change, Shoot, ShootStore, View, Version, now_iso
from ..trace import read_events
from .policy import classify_clause

PhotoStatus = Literal["altered", "unaltered", "unverified", "review", "superseded"]


class PhotoReport(BaseModel):
    view: str
    label: str
    room: str | None = None
    status: PhotoStatus
    changes: list[Change] = Field(default_factory=list)
    disclosure: str | None = None  # the sentence shown with the photo, when it is altered
    head: str | None = None
    final_blob: str | None = None
    delivered_blob: str | None = None
    has_brackets: bool = False
    qc_checked: bool = False
    deliver: bool = True  # part of the listing set; False when regrouping replaced it
    notes: list[str] = Field(default_factory=list)


class ComplianceReport(BaseModel):
    shoot: str
    generated_at: str = Field(default_factory=now_iso)
    photos: list[PhotoReport]
    counts: dict[str, int]
    ready: bool  # nothing left for the photographer to rule on
    issues: list[str] = Field(default_factory=list)


# QC finding types whose "legitimate edit" verdict means the editor changed what the scene shows.
EDITOR_ALTERATIONS: dict[str, tuple[str, str]] = {
    "window_sky": ("sky replaced", "sky"),
    "twilight": ("converted to a twilight scene", "twilight"),
    "grass": ("lawn colour enhanced", "lawn"),
    "declutter": ("items removed", "removal"),
    "staging": ("virtually staged", "staging"),
    "fireplace": ("fire added to the fireplace", "fire"),
    "tv_art": ("TV screen picture replaced", "screen"),
    "detail_hallucination": ("details redrawn", "change"),
}
# Types where an unresolved or unfixed finding may mean the scene was changed (a drifted wall colour, an added
# fire). camera_person_removal counts only when the finding says something real went missing: a leftover tripod or
# hand is a defect in the photo, not a change to the property.
MAY_ALTER = set(EDITOR_ALTERATIONS) | {"paint_color", "wood_floor_color", "rendering_artifact"}
_ERASED = re.compile(r"\b(?:erased?|missing|removed|lost|absent|gone|translucent|smear\w*|disappear\w*|lacks?)\b", re.I)

# What a buyer is told when the photographer rules that a QC finding is an alteration to disclose.
DISCLOSED_FINDING = {
    "camera_person_removal": "object removed",
    "paint_color": "paint colour changed",
    "wood_floor_color": "floor colour changed",
    "rendering_artifact": "details redrawn",
}

_GEAR_NOTE = re.compile(r"\b(?:tripod|camera|photographer|reflection|light\s+stand|flash)\b", re.I)
_ITEMS_NOTE = re.compile(r"\b(?:clutter|declutter|bins?|cars?|cords?|hoses?|signs?|personal|items?|toys?)\b", re.I)
# Order lines (a view's requested_edits) that name an alteration the editor was asked to make.
_ORDER_ALTERATION = [
    (re.compile(r"\bsky\b", re.I), "sky replaced", "sky"),
    (re.compile(r"\btwilight|dusk\b", re.I), "converted to a twilight scene", "twilight"),
    (re.compile(r"\bvirtual(?:ly)?\s+stag|\bstaging\b", re.I), "virtually staged", "staging"),
    (re.compile(r"\bdeclutter|\bclutter\b", re.I), "items removed", "removal"),
    (re.compile(r"\b(?:grass|lawn)\b", re.I), "lawn colour enhanced", "lawn"),
    (re.compile(r"\bfire\b(?!\s*(?:place)?\s*(?:disabled|off))", re.I), "fire added to the fireplace", "fire"),
    (re.compile(r"\b(?:tv|television)\b", re.I), "TV screen picture replaced", "screen"),
]


class _Runs:
    """run_start data per run id (mode, request, origin), read once per report."""

    def __init__(self, store: ShootStore, shoot_id: str):
        self.store, self.shoot_id, self._cache = store, shoot_id, {}

    def __call__(self, run_id: str | None) -> dict:
        if not run_id:
            return {}
        if run_id not in self._cache:
            events = read_events(self.store.shoot_dir(self.shoot_id) / "runs" / f"{run_id}.jsonl")
            start = next((e for e in events if e["kind"] == "run_start"), None)
            self._cache[run_id] = (start or {}).get("data", {})
        return self._cache[run_id]


def head_chain(view: View) -> list[Version]:
    """Versions from the root to the head, oldest first: the edits that reached the current photo."""
    if not view.head:
        return []
    by_id = {v.id: v for v in view.versions}
    chain: list[Version] = []
    cur = by_id.get(view.head)
    seen: set[str] = set()
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        chain.append(cur)
        cur = by_id.get(cur.parent) if cur.parent else None
    return list(reversed(chain))


def _short(text: str, n: int = 80) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _requested_by(run: dict) -> str:
    if run.get("origin") == "client":
        return "client"
    return {"chat": "customer", "qc": "qc", "reel": "unknown"}.get(run.get("mode") or "", "unknown")


def classify_version(shoot: Shoot, view: View, v: Version, run: dict) -> Change | None:
    """The change one version made, or None for the root (the photo as ingested or re-merged)."""
    p = v.params or {}
    mode = run.get("mode")
    base = dict(by="shootagent", requested_by=_requested_by(run), version=v.id, detail=v.note or "",
                request=_short(run.get("request") or "", 300) or None)
    op = v.op
    if op in ("ingest", "merge"):
        return None
    if op == "regroup":
        return Change(kind="restoration", what="re-merged from this camera position's own brackets",
                      category="restoration", **{**base, "requested_by": "qc"})
    if op == "restore_from_brackets":
        return Change(kind="restoration", what="restored to what the camera captured", category="restoration",
                      **base)
    if op == "adjust_exposure":
        ev = p.get("ev")
        what = f"exposure {float(ev):+.2f} EV" if isinstance(ev, (int, float)) else "exposure adjusted"
        return Change(kind="adjustment", what=what, category="adjustment", **base)
    if op == "adjust_white_balance":
        return Change(kind="adjustment", what="white balance adjusted", category="adjustment", **base)
    if op == "recolor_region":
        hexv = str(p.get("hex") or "")
        mask = shoot.masks.get(str(p.get("object_mask") or ""))
        surface = (mask.query if mask and mask.query else "") or "surface"
        if mode == "qc":
            return Change(kind="restoration", what=f"{surface} colour returned to the camera's {hexv}".strip(),
                          category="restoration", **base)
        return Change(kind="alteration", what=f"{surface} colour changed to {hexv}".strip(), category="colour",
                      **base)
    if op == "remove_object":
        what = str(p.get("what") or "object")
        if classify_clause(f"remove {what}").category == "gear":
            return Change(kind="adjustment", what=f"photographer's {what} removed", category="gear", **base)
        return Change(kind="alteration", what=f"{what} removed", category="removal", **base)
    if op == "generative_fill":
        prompt = str(p.get("prompt") or "")
        cat = classify_clause(f"add {prompt}").category
        what = {
            "fire": "fire added to the fireplace",
            "sky": "sky replaced",
            "lawn": "lawn enhanced",
            "staging": "virtually staged",
            "twilight": "converted to a twilight scene",
        }.get(cat, f"area redrawn: {_short(prompt, 60)}")
        return Change(kind="alteration", what=what, category=cat if cat != "unclear" else "addition", **base)
    if op == "place_on_screen":
        return Change(kind="alteration", what="TV screen picture replaced", category="screen", **base)
    if op == "add_watermark":
        return Change(kind="overlay", what=f"watermark “{_short(str(p.get('text') or ''), 40)}”", category="overlay",
                      **base)
    if op == "draw_lot_lines":
        return Change(kind="overlay", what="approximate lot lines drawn", category="overlay", **base)
    return Change(kind="alteration", what=f"edited ({op.replace('_', ' ')})", category="change", **base)


def _finding_changes(view: View) -> list[Change]:
    """The editor's changes, from the QC verdicts on its finished photo."""
    out: list[Change] = []
    for f in view.findings:
        note = str(f.evidence.get("agent_note") or "")
        verdict = f.evidence.get("photographer")
        if f.status == "dismissed" and verdict == "disclose":
            what = DISCLOSED_FINDING.get(f.type) or EDITOR_ALTERATIONS.get(f.type, ("scene edited", ""))[0]
            out.append(Change(kind="alteration", what=what, detail=f"{f.explanation} {note}".strip(),
                              by="editor", requested_by="order", category=f.type, finding=f.id))
        elif f.status == "dismissed" and verdict == "not_alteration":
            continue
        elif f.status == "dismissed":
            if f.type == "camera_person_removal":
                if _ITEMS_NOTE.search(note) and not _GEAR_NOTE.search(note):
                    out.append(Change(kind="alteration", what="items removed", detail=note, by="editor",
                                      requested_by="order", category="removal", finding=f.id))
                else:
                    out.append(Change(kind="adjustment", what="photographer's equipment removed", detail=note,
                                      by="editor", requested_by="order", category="gear", finding=f.id))
            elif f.type in EDITOR_ALTERATIONS:
                what, cat = EDITOR_ALTERATIONS[f.type]
                out.append(Change(kind="alteration", what=what, detail=note or f.explanation, by="editor",
                                  requested_by="order", category=cat, finding=f.id))
        elif f.status in ("open", "failed") and (
            f.type in MAY_ALTER or (f.type == "camera_person_removal" and _ERASED.search(f"{f.explanation} {note}"))
        ):
            prefix = "unresolved" if f.status == "open" else "could not be repaired"
            out.append(Change(kind="possible_alteration", what=_short(f.explanation, 120),
                              detail=f"QC finding {prefix}: {note or f.explanation}", by="editor",
                              requested_by="qc", category=f.type, finding=f.id))
    return out


def _order_changes(view: View, known: set[str]) -> list[Change]:
    """Alterations this view's own order explicitly requested from the editor. Used only before QC has looked at the
    photo (QC sees what was actually done), never from shoot-wide orders (they name edits for some rooms only), and
    never from `allowed_edits`: AutoHDR cases list permissions and presets there ("sky treatment where sky is
    visible"), which is no evidence that an interior's sky was replaced."""
    out: list[Change] = []
    lines = list(view.meta.get("requested_edits") or [])
    for line in (x for x in lines if isinstance(x, str)):
        for pat, what, cat in _ORDER_ALTERATION:
            if cat not in known and pat.search(line) and not re.search(r"\b(?:no|not|never|disabled|keep)\b", line, re.I):
                known.add(cat)
                out.append(Change(kind="alteration", what=what, detail=f"Ordered from the editor: {line}",
                                  by="editor", requested_by="order", category=cat))
                break
    return out


def disclosure_text(changes: list[Change]) -> str | None:
    """'Digitally altered: grey car removed, sky replaced.' ('Virtually staged: ...' when staged), or None."""
    alts = [c for c in changes if c.kind == "alteration"]
    if not alts:
        return None
    staged = any(c.category == "staging" for c in alts)
    whats: list[str] = []
    for c in alts:
        w = (c.what if not staged or c.category != "staging" else "").strip().rstrip(".;:,")
        if w and w not in whats:
            whats.append(w)
    head = "Virtually staged" if staged else "Digitally altered"
    if not whats:
        return f"{head}."
    if len(whats) > 4:
        whats = whats[:4] + [f"{len(whats) - 4} more change{'s' if len(whats) > 5 else ''}"]
    return f"{head}: {', '.join(whats)}."


def photo_report(store: ShootStore, shoot: Shoot, view: View, runs: _Runs | None = None) -> PhotoReport:
    runs = runs or _Runs(store, shoot.id)
    chain = head_chain(view)
    changes = [c for v in chain if (c := classify_version(shoot, view, v, runs(v.run))) is not None]
    qc_checked = bool(view.meta.get("qc_at") or view.meta.get("qc")) or view.derived_from is not None
    editor = _finding_changes(view)
    if not qc_checked:
        editor += _order_changes(view, {c.category for c in editor if c.category})
    changes = editor + changes
    has_brackets = len(view.brackets) > 0
    notes: list[str] = []
    if view.meta.get("split_into"):
        status: PhotoStatus = "superseded"
        notes.append("Replaced by " + ", ".join(view.meta["split_into"]) + " after regrouping its brackets.")
    elif any(c.kind == "possible_alteration" for c in changes):
        status = "review"
    elif any(c.kind == "alteration" for c in changes):
        status = "altered"
    elif has_brackets and not qc_checked:
        status = "unverified"
    else:
        status = "unaltered"
    if status != "superseded" and has_brackets and not qc_checked:
        notes.append("The editor's own changes have not been checked against the brackets; run QC.")
    if not has_brackets:
        notes.append("No camera brackets: the delivered photo stands in for the original.")
    head = view.head_version if view.head else None
    return PhotoReport(
        view=view.id,
        label=view.label or view.id,
        room=view.room,
        status=status,
        changes=changes,
        disclosure=disclosure_text(changes) if status in ("altered", "review") else None,
        head=view.head,
        final_blob=head.blob if head else None,
        delivered_blob=view.finished or (view.versions[0].blob if view.versions else None),
        has_brackets=has_brackets,
        qc_checked=qc_checked,
        deliver=status != "superseded",
        notes=notes,
    )


def listing_views(shoot: Shoot, report_photos: list[PhotoReport]) -> list[PhotoReport]:
    """The photos that will be published: the approved ones (every deliverable photo when none is approved), in
    showing order. Same rule as the reel."""
    from ..reel.spec import room_rank

    live = [p for p in report_photos if p.deliver and p.final_blob]
    approved = [p for p in live if shoot.view(p.view).approved]
    chosen = approved or live
    index = {v.id: i for i, v in enumerate(shoot.views)}
    return sorted(chosen, key=lambda p: (room_rank(p.room or p.label), index.get(p.view, 0)))


def build_report(store: ShootStore, shoot_id: str) -> ComplianceReport:
    shoot = store.load(shoot_id)
    runs = _Runs(store, shoot_id)
    photos = [photo_report(store, shoot, v, runs) for v in shoot.views if v.head]
    live = listing_views(shoot, photos)
    counts = {s: sum(p.status == s for p in photos) for s in ("altered", "unaltered", "unverified", "review", "superseded")}
    issues: list[str] = []
    review = [p for p in live if p.status == "review"]
    if review:
        issues.append(
            f"{len(review)} photo{'s' if len(review) > 1 else ''} in the listing need{'' if len(review) > 1 else 's'} a "
            "decision on a QC finding before publishing: " + ", ".join(p.label for p in review) + "."
        )
    unverified = [p for p in live if p.status == "unverified" or (p.has_brackets and not p.qc_checked)]
    if unverified:
        issues.append(
            f"{len(unverified)} photo{'s were' if len(unverified) > 1 else ' was'} not checked against the brackets, "
            "so changes the editor made may be missing from the disclosures. Run QC first."
        )
    return ComplianceReport(shoot=shoot_id, photos=photos, counts=counts, ready=not review, issues=issues)
