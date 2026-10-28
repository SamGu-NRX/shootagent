"""Versioned, content-addressed storage for a shoot.

Layout on disk::

    <data>/shoots/<shoot_id>/
        shoot.json        the shoot record below; the only mutable file
        blobs/<sha>.png   immutable images and masks, named by content hash
        runs/<run>.jsonl  agent traces (see trace.py)
        reel/             reel spec and renders

Every edit tool reads one version and writes a new one whose `parent` is the version it read, so any
result can be reproduced from the op log and undone by moving `head` back. Blobs are never rewritten.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Bracket(BaseModel):
    blob: str
    index: int  # position in the original input list (AutoHDR `inputs` order)
    ev: float | None = None  # relative exposure estimate, 0 = middle bracket
    name: str | None = None


class Finding(BaseModel):
    """One located defect. `region` is [x0, y0, x1, y1] normalised to 0..1, matching AutoHDR's result format."""

    id: str = Field(default_factory=lambda: "f_" + uuid.uuid4().hex[:8])
    type: str  # AutoHDR taxonomy name, e.g. "ghosting_grouping", "paint_color"
    explanation: str
    region: list[float] | None = None
    confidence: float | None = None
    status: Literal["open", "fixed", "dismissed", "failed"] = "open"
    evidence: dict[str, Any] = Field(default_factory=dict)  # metric values that triggered it


class Version(BaseModel):
    id: str
    parent: str | None
    blob: str
    op: str  # tool name, or "ingest" / "merge"
    params: dict[str, Any] = Field(default_factory=dict)
    mask: str | None = None  # mask id the op was confined to
    note: str = ""
    run: str | None = None  # agent run that produced it
    created_at: str = Field(default_factory=now_iso)
    check: dict[str, Any] | None = None  # self-check result for this version


class View(BaseModel):
    """One camera position: its brackets, AutoHDR's finished photo, and our edit history."""

    id: str
    label: str = ""
    room: str | None = None
    brackets: list[Bracket] = Field(default_factory=list)
    finished: str | None = None  # blob of the photo as delivered by AutoHDR
    reference: str | None = None  # blob of our aligned exposure fusion of the brackets
    groups: list[list[int]] | None = None  # bracket indices per detected view, when regrouped
    derived_from: str | None = None  # view id this one was split from by regrouping
    versions: list[Version] = Field(default_factory=list)
    head: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    approved: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)  # dataset case id, dimensions, etc.

    def version(self, version_id: str) -> Version:
        for v in self.versions:
            if v.id == version_id:
                return v
        raise KeyError(f"view {self.id} has no version {version_id!r}")

    @property
    def head_version(self) -> Version:
        if self.head is None:
            raise KeyError(f"view {self.id} has no versions yet")
        return self.version(self.head)


class Mask(BaseModel):
    id: str
    view: str
    blob: str  # single-channel PNG, 255 = inside
    source: str  # "grounding+sam", "box+sam", "user", "diff", ...
    query: str | None = None
    bbox: list[float] | None = None  # normalised [x0, y0, x1, y1]
    area: float | None = None  # fraction of the image
    created_at: str = Field(default_factory=now_iso)


class Listing(BaseModel):
    address: str | None = None
    price: str | None = None
    beds: float | None = None
    baths: float | None = None
    sqft: int | None = None
    headline: str | None = None


class Shoot(BaseModel):
    schema_: int = Field(default=SCHEMA_VERSION, alias="schema")
    id: str
    title: str
    created_at: str = Field(default_factory=now_iso)
    source: dict[str, Any] = Field(default_factory=dict)
    listing: Listing = Field(default_factory=Listing)
    views: list[View] = Field(default_factory=list)
    masks: dict[str, Mask] = Field(default_factory=dict)
    runs: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    def view(self, view_id: str) -> View:
        for v in self.views:
            if v.id == view_id:
                return v
        raise KeyError(f"shoot {self.id} has no view {view_id!r}")


class ShootStore:
    """File-backed store. One lock per shoot serialises read-modify-write of shoot.json."""

    _locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- paths -------------------------------------------------------------------------------------------
    def shoot_dir(self, shoot_id: str) -> Path:
        return self.root / shoot_id

    def blob_path(self, shoot_id: str, blob: str) -> Path:
        return self.shoot_dir(shoot_id) / "blobs" / blob

    def lock(self, shoot_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(shoot_id, threading.RLock())

    # ---- shoot records -----------------------------------------------------------------------------------
    def list_ids(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("*/shoot.json"))

    def create(self, title: str, source: dict[str, Any] | None = None, shoot_id: str | None = None) -> Shoot:
        shoot = Shoot(id=shoot_id or uuid.uuid4().hex[:10], title=title, source=source or {})
        (self.shoot_dir(shoot.id) / "blobs").mkdir(parents=True, exist_ok=True)
        (self.shoot_dir(shoot.id) / "runs").mkdir(exist_ok=True)
        self.save(shoot)
        return shoot

    def load(self, shoot_id: str) -> Shoot:
        path = self.shoot_dir(shoot_id) / "shoot.json"
        if not path.exists():
            raise KeyError(f"no shoot {shoot_id!r} under {self.root}")
        return Shoot.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, shoot: Shoot) -> None:
        path = self.shoot_dir(shoot.id) / "shoot.json"
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(shoot.model_dump_json(indent=1, by_alias=True), encoding="utf-8")
        os.replace(tmp, path)

    # ---- blobs -------------------------------------------------------------------------------------------
    def put_bytes(self, shoot_id: str, data: bytes, ext: str) -> str:
        name = hashlib.sha256(data).hexdigest()[:20] + "." + ext.lstrip(".")
        path = self.blob_path(shoot_id, name)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return name

    def put_file(self, shoot_id: str, src: Path) -> str:
        """Store an original file byte-for-byte (keeps JPEG quality and EXIF of dataset inputs)."""
        return self.put_bytes(shoot_id, Path(src).read_bytes(), Path(src).suffix.lower().lstrip(".") or "bin")

    def put_image(self, shoot_id: str, img: np.ndarray | Image.Image, fmt: str = "png") -> str:
        """Store an RGB uint8 array (H, W, 3) or a single-channel mask (H, W). PNG is lossless; use it for
        every intermediate so repeated edits do not accumulate JPEG loss."""
        pil = Image.fromarray(img) if isinstance(img, np.ndarray) else img
        buf = io.BytesIO()
        if fmt == "jpg":
            pil.convert("RGB").save(buf, "JPEG", quality=95, subsampling=0)
        else:
            pil.save(buf, "PNG", compress_level=1)
        return self.put_bytes(shoot_id, buf.getvalue(), fmt)

    def get_image(self, shoot_id: str, blob: str) -> np.ndarray:
        """RGB uint8 array for images, (H, W) uint8 for masks."""
        with Image.open(self.blob_path(shoot_id, blob)) as im:
            if im.mode in ("L", "1", "I;16"):
                return np.asarray(im.convert("L"))
            return np.asarray(im.convert("RGB"))

    # ---- versions and masks --------------------------------------------------------------------------------
    def add_version(
        self,
        shoot_id: str,
        view_id: str,
        image: np.ndarray,
        op: str,
        params: dict[str, Any] | None = None,
        parent: str | None = None,
        mask: str | None = None,
        note: str = "",
        run: str | None = None,
        move_head: bool = True,
    ) -> Version:
        blob = self.put_image(shoot_id, image)
        with self.lock(shoot_id):
            shoot = self.load(shoot_id)
            view = shoot.view(view_id)
            parent = parent if parent is not None else view.head
            version = Version(
                id=f"{view_id}.{len(view.versions)}",
                parent=parent,
                blob=blob,
                op=op,
                params=params or {},
                mask=mask,
                note=note,
                run=run,
            )
            view.versions.append(version)
            if move_head:
                view.head = version.id
            self.save(shoot)
        return version

    def set_head(self, shoot_id: str, view_id: str, version_id: str) -> None:
        with self.lock(shoot_id):
            shoot = self.load(shoot_id)
            view = shoot.view(view_id)
            view.version(version_id)
            view.head = version_id
            self.save(shoot)

    def set_check(self, shoot_id: str, view_id: str, version_id: str, check: dict[str, Any]) -> None:
        with self.lock(shoot_id):
            shoot = self.load(shoot_id)
            shoot.view(view_id).version(version_id).check = check
            self.save(shoot)

    def add_mask(
        self,
        shoot_id: str,
        view_id: str,
        mask: np.ndarray,
        source: str,
        query: str | None = None,
    ) -> Mask:
        m = (mask > 127).astype(np.uint8) * 255 if mask.dtype != bool else mask.astype(np.uint8) * 255
        ys, xs = np.nonzero(m)
        h, w = m.shape
        bbox = [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h] if len(xs) else None
        record = Mask(
            id="m_" + uuid.uuid4().hex[:8],
            view=view_id,
            blob=self.put_image(shoot_id, m),
            source=source,
            query=query,
            bbox=bbox,
            area=float(len(xs)) / (h * w),
        )
        with self.lock(shoot_id):
            shoot = self.load(shoot_id)
            shoot.masks[record.id] = record
            self.save(shoot)
        return record

    def get_mask(self, shoot_id: str, mask_id: str) -> np.ndarray:
        """Boolean (H, W) mask."""
        shoot = self.load(shoot_id)
        if mask_id not in shoot.masks:
            raise KeyError(f"shoot {shoot_id} has no mask {mask_id!r}")
        return self.get_image(shoot_id, shoot.masks[mask_id].blob) > 127

    def mutate(self, shoot_id: str, fn) -> Shoot:
        """Apply `fn(shoot)` under the shoot lock and save. Use for findings, labels, approval, listing."""
        with self.lock(shoot_id):
            shoot = self.load(shoot_id)
            fn(shoot)
            self.save(shoot)
            return shoot


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
