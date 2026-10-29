"""Portable shoot bundle: one zip that moves a finished shoot to another machine exactly as it is.

The bundle holds the shoot folder (shoot.json, blobs, run traces, film outputs and manifest, listing state, reel)
and bundle.json, which lists every file with its size and SHA-256. Left out: film/work/ (Reel Studio's
scratch projects, rebuilt by any assembly) and cache/ (resized copies the API makes on demand), temp files, and
anything named like an env file. The shoot folder never holds credentials; the env-file rule is a second guard.

Import refuses a bundle whose entries are not exactly the files bundle.json lists, whose bytes do not match
their hashes, or whose paths could land outside the shoot folder. It unpacks into a temp folder beside the
target and moves it into place only after every check passed, so a bad bundle leaves nothing behind. An
existing shoot with the same id is kept: import refuses unless asked to replace, and then renames the old
folder rather than deleting it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .store import Shoot

FORMAT = "shootagent-shoot/1"
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
SKIP_DIRS = {("film", "work"), ("cache",)}


class BundleError(ValueError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _skipped(rel: PurePosixPath) -> bool:
    parts = rel.parts
    if any(parts[: len(d)] == d for d in SKIP_DIRS):
        return True
    name = rel.name
    return name.startswith(".env") or name.endswith((".tmp", ".part")) or name.startswith(".")


def export_shoot(shoot_dir: Path, out: Path) -> dict:
    """Write `out` (zip) from `shoot_dir`; returns the bundle.json content."""
    shoot_dir = Path(shoot_dir)
    shoot = Shoot.model_validate_json((shoot_dir / "shoot.json").read_text(encoding="utf-8"))
    files = []
    for p in sorted(shoot_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = PurePosixPath(p.relative_to(shoot_dir).as_posix())
        if _skipped(rel):
            continue
        files.append({"path": str(rel), "size": p.stat().st_size, "sha256": _sha256(p)})
    manifest = {"format": FORMAT, "shoot_id": shoot.id, "title": shoot.title,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": files}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex[:6]}.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("bundle.json", json.dumps(manifest, indent=1, ensure_ascii=False))
        for f in files:
            # Photos, video and audio are already compressed; deflating them again only costs time.
            stored = f["path"].rsplit(".", 1)[-1].lower() in ("jpg", "jpeg", "png", "mp4", "mov", "m4a", "mp3")
            z.write(shoot_dir / f["path"], f"shoot/{f['path']}",
                    compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
    os.replace(tmp, out)
    return manifest


def _safe(name: str) -> PurePosixPath:
    """The path under shoot/ an entry names, or BundleError when it could escape the shoot folder."""
    if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        raise BundleError(f"unsafe path in bundle: {name!r}")
    p = PurePosixPath(name)
    if p.parts[:1] != ("shoot",) or len(p.parts) < 2 or any(x in ("", ".", "..") for x in p.parts):
        raise BundleError(f"unsafe path in bundle: {name!r}")
    return PurePosixPath(*p.parts[1:])


def import_shoot(zip_path: Path, shoots_dir: Path, replace: bool = False) -> dict:
    """Unpack a bundle into `shoots_dir/<shoot id>` after checking it; returns {shoot_id, files, replaced}."""
    shoots_dir = Path(shoots_dir)
    with zipfile.ZipFile(zip_path) as z:
        try:
            manifest = json.loads(z.read("bundle.json"))
        except KeyError:
            raise BundleError("not a shoot bundle: no bundle.json") from None
        if manifest.get("format") != FORMAT:
            raise BundleError(f"unknown bundle format {manifest.get('format')!r}; expected {FORMAT}")
        sid = manifest.get("shoot_id", "")
        if not ID_RE.match(sid):
            raise BundleError(f"bad shoot id {sid!r}")
        listed = {f["path"]: f for f in manifest["files"]}
        entries = {}
        for info in z.infolist():
            if info.filename == "bundle.json" or info.is_dir():
                continue
            rel = str(_safe(info.filename))
            if rel not in listed:
                raise BundleError(f"{info.filename} is in the zip but not in bundle.json")
            entries[rel] = info
        missing = sorted(set(listed) - set(entries))
        if missing:
            raise BundleError(f"bundle.json lists files the zip does not hold: {missing[:5]}")
        if "shoot.json" not in entries:
            raise BundleError("the bundle has no shoot.json")

        shoots_dir.mkdir(parents=True, exist_ok=True)
        stage = shoots_dir / f".import-{sid}-{uuid.uuid4().hex[:6]}"
        try:
            for rel, info in entries.items():
                dest = stage.joinpath(*PurePosixPath(rel).parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                h, size = hashlib.sha256(), 0
                with z.open(info) as src, dest.open("wb") as out:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        h.update(chunk)
                        size += len(chunk)
                        out.write(chunk)
                want = listed[rel]
                if h.hexdigest() != want["sha256"] or size != want["size"]:
                    raise BundleError(f"{rel} does not match its hash in bundle.json; the bundle is damaged")
            shoot = Shoot.model_validate_json((stage / "shoot.json").read_text(encoding="utf-8"))
            if shoot.id != sid:
                raise BundleError(f"shoot.json is shoot {shoot.id!r}, bundle.json says {sid!r}")
            target = shoots_dir / sid
            replaced = None
            if target.exists():
                if not replace:
                    raise BundleError(f"shoot {sid} already exists in {shoots_dir}; pass --replace to keep it aside")
                replaced = target.with_name(f"{sid}.replaced-{time.strftime('%Y%m%d-%H%M%S')}")
                os.replace(target, replaced)
            os.replace(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)  # our own staging folder: only reached when a check failed
    return {"shoot_id": sid, "files": len(entries), "replaced": str(replaced) if replaced else None}
