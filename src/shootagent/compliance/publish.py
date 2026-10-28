"""Publish the listing photos: JPEGs at listing size, each altered photo paired with its unaltered original, the
disclosure sentence, and a hash chain from the camera's brackets to the published file.

The original paired with an altered photo is the editor's delivered photo when QC confirmed the editor changed
nothing in the scene and every alteration is ShootAgent's own; otherwise it is what the camera captured: the
brackets fused and registered onto the photo's frame, so a before/after slider lines up pixel for pixel.
"""

from __future__ import annotations

import hashlib
import io
import json
import secrets
from pathlib import Path

import numpy as np
from PIL import Image

from ..store import Publication, PublishedImage, PublishedPhoto, Shoot, ShootStore, View, now_iso
from .disclosure import PhotoReport, build_report, head_chain
from .provenance import Chain, root_hash, sha256_file

LISTING_LONG_EDGE = 2048  # MLS systems accept 2048 px on the long edge; most resize anything larger
JPEG_QUALITY = 90


# ---- images ---------------------------------------------------------------------------------------------


def jpeg_bytes(img: np.ndarray | Image.Image, long_edge: int = LISTING_LONG_EDGE, quality: int = JPEG_QUALITY) -> tuple[bytes, int, int]:
    pil = Image.fromarray(img) if isinstance(img, np.ndarray) else img
    pil = pil.convert("RGB")
    if max(pil.size) > long_edge:
        pil = pil.copy()
        pil.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue(), pil.size[0], pil.size[1]


def _put_jpeg(store: ShootStore, shoot_id: str, img: np.ndarray, long_edge: int = LISTING_LONG_EDGE) -> PublishedImage:
    data, w, h = jpeg_bytes(img, long_edge)
    blob = store.put_bytes(shoot_id, data, "jpg")
    return PublishedImage(blob=blob, sha256=hashlib.sha256(data).hexdigest(), width=w, height=h)


def capture_image(store: ShootStore, shoot: Shoot, view: View) -> np.ndarray | None:
    """What the camera captured for this view, on the view's own frame, or None without brackets.

    Cached in `view.meta["capture"]` as a listing-size JPEG blob, keyed by the root version it is registered to."""
    if not view.versions:
        return None
    root = view.versions[0]
    cached = view.meta.get("capture") or {}
    if cached.get("for") == root.blob and cached.get("blob") and store.blob_path(shoot.id, cached["blob"]).exists():
        return store.get_image(shoot.id, cached["blob"])
    img: np.ndarray | None = None
    if view.derived_from and view.reference:
        img = store.get_image(shoot.id, view.reference)  # a split view's raw fusion, already on its frame
    elif view.brackets:
        try:
            from ..qc.analyze import reference_for_fix

            ref, valid = reference_for_fix(store, shoot.id, view.id, long_edge=LISTING_LONG_EDGE, target_blob=root.blob)
            img = np.where(valid[..., None], ref, 0).astype(np.uint8) if valid.mean() > 0.6 else None
        except Exception:  # registration can fail on a mixed or tiny set of brackets; fall back below
            img = None
        if img is None and view.reference:
            img = store.get_image(shoot.id, view.reference)
    if img is None:
        return None
    data, _, _ = jpeg_bytes(img)
    blob = store.put_bytes(shoot.id, data, "jpg")

    def save(s: Shoot):
        s.view(view.id).meta["capture"] = {"blob": blob, "for": root.blob}

    store.mutate(shoot.id, save)
    return store.get_image(shoot.id, blob)


class _Hashes:
    """Full SHA-256 of blobs, persisted per shoot: blobs never change, so a hash is computed once."""

    def __init__(self, store: ShootStore, shoot_id: str):
        self.store, self.shoot_id = store, shoot_id
        self.path = store.shoot_dir(shoot_id) / "cache" / "sha256.json"
        try:
            self.cache: dict[str, str] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.cache = {}
        self.dirty = False

    def __call__(self, blob: str | None) -> str | None:
        if not blob:
            return None
        if blob not in self.cache:
            path = self.store.blob_path(self.shoot_id, blob)
            if not path.exists():
                return None
            self.cache[blob] = sha256_file(path)
            self.dirty = True
        return self.cache[blob]

    def save(self) -> None:
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cache), encoding="utf-8")


# ---- the listing set ------------------------------------------------------------------------------------


def listing_views(shoot: Shoot, report_photos: list[PhotoReport]) -> list[PhotoReport]:
    """Approved photos (every deliverable photo when none is approved), in showing order."""
    from ..reel.spec import room_rank

    live = [p for p in report_photos if p.deliver and p.final_blob]
    approved = [p for p in live if shoot.view(p.view).approved]
    chosen = approved or live
    index = {v.id: i for i, v in enumerate(shoot.views)}
    return sorted(chosen, key=lambda p: (room_rank(p.room or p.label), index.get(p.view, 0)))


def is_stale(store: ShootStore, shoot_id: str) -> bool:
    """True when the photos or their heads changed since the last publication."""
    shoot = store.load(shoot_id)
    pub = shoot.publication
    if pub is None:
        return True
    report = build_report(store, shoot_id)
    now = {p.view: p.head for p in listing_views(shoot, report.photos)}
    return now != pub.heads


def public_url(base: str, token: str, view: str | None = None) -> str:
    return f"{base.rstrip('/')}/#/d/{token}" + (f"/{view}" if view else "")


# ---- publish --------------------------------------------------------------------------------------------


def _check_summary(check: dict | None) -> dict | None:
    if not check:
        return None
    judge = check.get("judge") or {}
    metrics = check.get("metrics") or {}
    return {
        "passed": bool(check.get("passed")),
        "outside_max_delta": metrics.get("outside_max_delta"),
        "judge": judge.get("verdict"),
    }


# The chain is public and cannot be redacted later without breaking it, so it records who asked for each edit but
# never the customer's own words.
def _photo_chain(store: ShootStore, shoot: Shoot, view: View, report: PhotoReport, hashes: _Hashes,
                 final: PublishedImage, original: PublishedImage | None, original_kind: str | None,
                 capture: PublishedImage | None) -> Chain:
    chain = Chain()
    if view.brackets:
        chain.add({
            "step": "captured",
            "view": view.id,
            "brackets": [{"index": b.index, "ev": b.ev, "blob": b.blob, "sha256": hashes(b.blob)} for b in view.brackets],
        })
    versions = head_chain(view)
    root = versions[0] if versions else None
    if root is not None and root.op == "regroup":
        chain.add({"step": "merged", "by": "shootagent", "from_view": view.derived_from, "version": root.id,
                   "blob": root.blob, "sha256": hashes(root.blob), "note": root.note})
    elif root is not None:
        chain.add({"step": "delivered", "by": "editor", "version": root.id, "blob": root.blob,
                   "sha256": hashes(root.blob)})
    by_version = {c.version: c for c in report.changes if c.version}
    blobs = {v.id: v.blob for v in view.versions}
    for v in versions[1:]:
        change = by_version.get(v.id)
        mask = shoot.masks.get(v.mask) if v.mask else None
        chain.add({
            "step": "edit",
            "version": v.id,
            "parent": v.parent,
            "op": v.op,
            "kind": change.kind if change else None,
            "what": change.what if change else None,
            "requested_by": change.requested_by if change else None,
            "blob": v.blob,
            "sha256": hashes(v.blob),
            "parent_blob": blobs.get(v.parent or ""),
            "parent_sha256": hashes(blobs.get(v.parent or "")),
            "mask_blob": mask.blob if mask else None,
            "mask_sha256": hashes(mask.blob) if mask else None,
            "check": _check_summary(v.check),
            "run": v.run,
            "at": v.created_at,
        })
    editor_changes = [c for c in report.changes if c.by == "editor"]
    chain.add({
        "step": "published",
        "view": view.id,
        "label": report.label,
        "head": view.head,
        "status": report.status,
        "disclosure": report.disclosure,
        "editor_changes": [{"kind": c.kind, "what": c.what, "finding": c.finding} for c in editor_changes],
        "final": final.model_dump(),
        "original": original.model_dump() if original else None,
        "original_kind": original_kind,
        "capture": capture.model_dump() if capture else None,
        "at": now_iso(),
    })
    return chain


def publish(store: ShootStore, shoot_id: str) -> Publication:
    """Snapshot the listing photos with disclosures and provenance. Keeps the shoot's public token."""
    report = build_report(store, shoot_id)
    shoot = store.load(shoot_id)
    chosen = listing_views(shoot, report.photos)
    if not chosen:
        raise ValueError("The shoot has no photos to publish.")
    hashes = _Hashes(store, shoot_id)
    photos: list[PublishedPhoto] = []
    for order, rep in enumerate(chosen, start=1):
        view = shoot.view(rep.view)
        head_img = store.get_image(shoot_id, view.head_version.blob)
        final = _put_jpeg(store, shoot_id, head_img)
        cap_img = capture_image(store, shoot, view)
        capture = _put_jpeg(store, shoot_id, cap_img) if cap_img is not None else None
        original: PublishedImage | None = None
        original_kind: str | None = None
        if rep.status in ("altered", "review"):
            editor_altered = any(c.by == "editor" and c.kind in ("alteration", "possible_alteration") for c in rep.changes)
            if rep.qc_checked and not editor_altered and view.versions:
                original_kind = "delivered"
                original = _put_jpeg(store, shoot_id, store.get_image(shoot_id, view.versions[0].blob))
            elif capture is not None:
                original_kind, original = "capture", capture
            elif view.versions:
                original_kind = "delivered"
                original = _put_jpeg(store, shoot_id, store.get_image(shoot_id, view.versions[0].blob))
        chain = _photo_chain(store, shoot, view, rep, hashes, final, original, original_kind, capture)
        photos.append(PublishedPhoto(
            view=view.id, label=rep.label, room=rep.room, order=order, status=rep.status,
            disclosure=rep.disclosure, changes=rep.changes, head=view.head or "", final=final, original=original,
            original_kind=original_kind, capture=capture, chain=chain.entries,
        ))
    hashes.save()
    token = shoot.public_token or secrets.token_urlsafe(9)
    previous = shoot.publication
    pub = Publication(
        token=token,
        version=(previous.version + 1) if previous else 1,
        heads={p.view: p.head for p in photos},
        photos=photos,
        root=root_hash(p.chain[-1].hash for p in photos),
    )
    manifest = manifest_dict(shoot, pub)
    pub.manifest_blob = store.put_bytes(shoot_id, json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"), "json")

    def save(s: Shoot):
        s.public_token = token
        s.publication = pub

    store.mutate(shoot_id, save)
    return pub


def manifest_dict(shoot: Shoot, pub: Publication) -> dict:
    return {
        "format": "shootagent-provenance/1",
        "how_to_verify": (
            "For each photo, every chain entry's hash is sha256(body) and its body's 'prev' is the previous entry's "
            "hash. Every image an entry names carries the sha256 of the file's bytes. `shootagent verify <kit.zip>` "
            "checks both."
        ),
        "shoot": shoot.id,
        "title": shoot.title,
        "listing": shoot.listing.model_dump(exclude_none=True),
        "published_at": pub.created_at,
        "version": pub.version,
        "root": pub.root,
        "photos": [
            {
                "order": p.order,
                "view": p.view,
                "label": p.label,
                "status": p.status,
                "disclosure": p.disclosure,
                "changes": [c.model_dump(exclude_none=True) for c in p.changes],
                "final": p.final.model_dump(),
                "original": p.original.model_dump() if p.original else None,
                "original_kind": p.original_kind,
                "capture": p.capture.model_dump() if p.capture else None,
                "chain": [e.model_dump() for e in p.chain],
            }
            for p in pub.photos
        ],
    }


def published_blobs(pub: Publication) -> set[str]:
    out: set[str] = set()
    for p in pub.photos:
        for img in (p.final, p.original, p.capture):
            if img is not None:
                out.add(img.blob)
    if pub.manifest_blob:
        out.add(pub.manifest_blob)
    return out


def blob_path(store: ShootStore, shoot_id: str, blob: str) -> Path:
    return store.blob_path(shoot_id, blob)
