"""Build shoots from the AutoHDR hackathon datasets and from editor requests.

Brackets and finished photos are stored byte-identical (`ShootStore.put_file`), so JPEG data and colour
profiles of the inputs survive. The finished photo becomes version 0 of its view with op "ingest".
Dataset labels are never read here: they are for eval only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .config import Settings, get_settings
from .imaging import features as F
from .imaging.brackets import align_brackets, apply_alignment, estimate_ev, fuse_mertens, pick_reference
from .imaging.io import image_size, load_rgb
from .store import Bracket, Shoot, ShootStore, Version, View

EV_LONG_EDGE = 400


def resolve_path(p: str | Path | None, what: str) -> Path:
    """Accept native paths and Git Bash style paths (/c/Users/..) from .env on Windows."""
    if p is None or str(p) == "":
        raise ValueError(f"{what} is not configured; set it in .env (see config.py)")
    s = str(p)
    m = re.match(r"^/([a-zA-Z])/(.*)$", s.replace("\\", "/"))
    if os.name == "nt" and m:
        s = f"{m.group(1).upper()}:/{m.group(2)}"
    path = Path(os.path.expanduser(s))
    if not path.exists():
        raise FileNotFoundError(f"{what} {path} does not exist")
    return path


def fuse_files(paths: list[Path], long_edge: int = 4096) -> np.ndarray:
    """Aligned Mertens fusion of bracket files (used when a view arrives without a finished photo)."""
    imgs = [load_rgb(p, long_edge) for p in paths]
    if len(imgs) == 1:
        return imgs[0]
    feats = [F.detect(im) for im in imgs]
    evs = estimate_ev(imgs)
    r = pick_reference(evs)
    aligns = align_brackets(imgs, r, feats)
    aligned, _ = apply_alignment(imgs, aligns, r)
    return fuse_mertens([a for a, al in zip(aligned, aligns) if al.ok])


def _next_view_id(shoot: Shoot) -> str:
    taken = {v.id for v in shoot.views}
    n = len(shoot.views) + 1
    while f"v{n:02d}" in taken:
        n += 1
    return f"v{n:02d}"


def _add_view(store: ShootStore, shoot_id: str, *, finished: Path | None, brackets: list[Path],
              label: str, meta: dict[str, Any], bracket_names: list[str] | None = None) -> str:
    """Store files, estimate EVs and append a view. Returns the new view id."""
    bracket_blobs = [store.put_file(shoot_id, p) for p in brackets]
    evs = estimate_ev([load_rgb(p, EV_LONG_EDGE) for p in brackets]) if brackets else []
    fin_blob = store.put_file(shoot_id, finished) if finished is not None else None
    dims = {}
    if finished is not None:
        w, h = image_size(finished)
        dims = {"width": w, "height": h}
    bracket_dims = [list(image_size(p)) for p in brackets]
    fused_blob = None
    if finished is None and brackets:
        fused = fuse_files(brackets)
        fused_blob = store.put_image(shoot_id, fused)
        dims = {"width": fused.shape[1], "height": fused.shape[0]}
    holder: dict[str, str] = {}

    def add(shoot: Shoot):
        vid = _next_view_id(shoot)
        holder["id"] = vid
        view = View(
            id=vid,
            label=label,
            brackets=[Bracket(blob=b, index=i, ev=ev, name=(bracket_names or [p.name for p in brackets])[i])
                      for i, (b, ev, p) in enumerate(zip(bracket_blobs, evs, brackets))],
            finished=fin_blob,
            meta={**meta, **dims, "bracket_dims": bracket_dims},
        )
        if fin_blob:
            view.versions.append(Version(id=f"{vid}.0", parent=None, blob=fin_blob, op="ingest",
                                         note="Finished photo as delivered."))
            view.head = f"{vid}.0"
        elif fused_blob:
            view.versions.append(Version(id=f"{vid}.0", parent=None, blob=fused_blob, op="ingest",
                                         note="No finished photo was supplied; aligned exposure fusion of the brackets."))
            view.head = f"{vid}.0"
        shoot.views.append(view)

    store.mutate(shoot_id, add)
    return holder["id"]


# ---- AutoHDR defect set --------------------------------------------------------------------------------
def _read_case(case_dir: Path) -> dict:
    path = case_dir / "case.json"
    if not path.exists():
        raise FileNotFoundError(f"{case_dir} has no case.json; expected an AutoHDR case folder")
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_autohdr_defect_case(store: ShootStore, shoot_id: str, case_dir: str | Path) -> str:
    """One defect case folder -> one view (brackets in `inputs` order, finished photo as version 0)."""
    case_dir = Path(case_dir)
    case = _read_case(case_dir)
    inputs = [case_dir / Path(p).name for p in case.get("inputs", [])]
    missing = [str(p) for p in inputs if not p.exists()]
    if missing:
        raise FileNotFoundError(f"case {case.get('case_id')} lists inputs that are not on disk: {missing}")
    current = case.get("current_image")
    finished = case_dir / Path(current).name if current else None
    if finished is not None and not finished.exists():
        raise FileNotFoundError(f"case {case.get('case_id')} finished photo {finished} is missing")
    meta = {
        "source": "autohdr_defect",
        "case_id": case.get("case_id", case_dir.name),
        "request": case.get("request", ""),
        "editing_context": case.get("editing_context", ""),
        "allowed_edits": case.get("allowed_edits", []),
        "edit_intent_status": case.get("edit_intent_status"),
        "original_dimensions": case.get("original_dimensions"),
        "related_images": case.get("related_images", []),
    }
    return _add_view(store, shoot_id, finished=finished, brackets=inputs, label=meta["case_id"], meta=meta)


def create_shoot_from_defect_cases(store: ShootStore, title: str, case_dirs: Iterable[str | Path],
                                   source: dict | None = None) -> Shoot:
    case_dirs = [Path(c) for c in case_dirs]
    shoot = store.create(title, source={"kind": "autohdr_defect", "cases": [c.name for c in case_dirs], **(source or {})})
    for c in case_dirs:
        ingest_autohdr_defect_case(store, shoot.id, c)
    return store.load(shoot.id)


def create_shoot_from_files(store: ShootStore, title: str, spec: list[dict]) -> Shoot:
    """Uploaded files -> shoot. `spec` is one dict per view: {"brackets": [paths], "finished": path | None}."""
    shoot = store.create(title, source={"kind": "upload"})
    for i, v in enumerate(spec, start=1):
        brackets = [Path(p) for p in v.get("brackets") or []]
        finished = Path(v["finished"]) if v.get("finished") else None
        if finished is None and not brackets:
            raise ValueError(f"upload view {i} has neither brackets nor a finished photo")
        _add_view(store, shoot.id, finished=finished, brackets=brackets, label=f"View {i}",
                  meta={"source": "upload"})
    return store.load(shoot.id)


# ---- AutoHDR customer set ------------------------------------------------------------------------------
def ingest_customer_case(store: ShootStore, shoot_id: str, case_dir: str | Path) -> str:
    """customer/cases/<id>/ -> one view. The `input/` folder holds the finished photo (and inputs, if any);
    `answers/` is eval-only and never read here."""
    case_dir = Path(case_dir)
    inp = case_dir / "input" if (case_dir / "input").is_dir() else case_dir
    case = _read_case(inp)
    finished = inp / Path(case["current_image"]).name
    inputs = [inp / Path(p).name for p in case.get("inputs", [])]
    meta = {
        "source": "autohdr_customer",
        "case_id": case.get("case_id", case_dir.name),
        "request": case.get("request", ""),
        "instructions": case.get("instructions", []),
        "editing_context": case.get("editing_context", ""),
        "shoot_group_id": case.get("shoot_group_id"),
        "requested_output_dimensions": case.get("requested_output_dimensions"),
    }
    return _add_view(store, shoot_id, finished=finished, brackets=[p for p in inputs if p.exists()],
                     label=meta["case_id"], meta=meta)


# ---- editor requests -----------------------------------------------------------------------------------
def _download(url: str, cache_dir: Path) -> Path:
    import httpx

    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?")[0]).suffix.lower() or ".jpg"
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        raise ValueError(f"refusing to cache {url}: not an image URL")
    path = cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + suffix)
    if path.exists() and path.stat().st_size > 0:
        return path
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if not ctype.startswith("image/"):
            raise ValueError(f"{url} returned {ctype or 'no content-type'}, not an image")
        data = r.content
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    with Image.open(tmp) as im:  # validate before caching
        im.verify()
    os.replace(tmp, path)
    return path


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            import ast

            return [str(x) for x in ast.literal_eval(s)]
        return [s] if s else []
    return [str(x) for x in v]


def ingest_editor_request(store: ShootStore, shoot_id: str, record: dict, cache_dir: str | Path) -> str:
    """One record of customer-requests.json -> one view. The edited photo is the finished photo; `inputs`
    are kept as brackets (display previews, so EVs are rough) and request references go to meta."""
    cache_dir = Path(cache_dir)
    if not record.get("edited_photo"):
        raise ValueError(f"request {record.get('id')} has no edited_photo URL")
    finished = _download(record["edited_photo"], cache_dir)
    inputs = [_download(u, cache_dir) for u in _as_list(record.get("inputs"))]
    refs = [str(_download(u, cache_dir)) for u in _as_list(record.get("request_references"))]
    meta = {
        "source": "editor_request",
        "case_id": record.get("id"),
        "request": record.get("request", ""),
        "request_kind": record.get("kind"),
        "request_references": refs,
    }
    return _add_view(store, shoot_id, finished=finished, brackets=inputs, label=record.get("id", ""), meta=meta)


# ---- demo presets --------------------------------------------------------------------------------------
# Defect cases the QC analysis handles, chosen from eval/out/qc_defects.json: a wrong-view fusion (two
# rooms overlaid), same-view misalignment ghosting, fire added to a non-fireplace, an unrequested fire, a
# false removal (medium confidence, the agent confirms it visually), a neutralised blue wall (medium
# confidence) and two clean controls that must stay unchanged.
QC_TOUR_CASES = [
    "defect_train_0044",  # ghosting_grouping: two camera positions fused into one photo
    "defect_train_0026",  # ghosting_grouping: camera moved between brackets, edges doubled
    "defect_train_0014",  # fireplace: fire added in a gramophone alcove
    "defect_train_0042",  # fireplace: unrequested fire added to an empty firebox
    "defect_train_0016",  # camera_person_removal: stop sign erased
    "defect_train_0030",  # paint_color: blue wall neutralised to grey
    "defect_train_0038",  # clean control
    "defect_train_0039",  # clean control
]

# The demo shoot for the live demo and the Loom: defect cases the QC pass fixes reliably in the 63-case eval
# (grouping split, ghosting, an erased stop sign) plus a clean control, and saved customer-request photos
# whose requests passed from chat (README Results). Not one property; one place to show every capability.
SHOWCASE_CASES = ["defect_train_0044", "defect_train_0026", "defect_train_0016", "defect_train_0038"]
SHOWCASE_REQUESTS = [
    "request-4603f63f34e776d189",  # remove the grey car
    "request-868a9e8901c4cdc9a0",  # remove white trash bin on the side
    "request-dab278eb9a3bc3ffd6",  # change the grey walls to the colour in the reference image
    "request-0e142faf3b55390045",  # add the reference image on the TV screen
]

CHAT_REQUEST_KEYWORDS = ["wall", "sky", "lawn", "grass", "tv", "declutter", "remove", "twilight", "fire"]


def build_demo_shoot(store: ShootStore, preset: str, settings: Settings | None = None) -> Shoot:
    """`qc-tour`: defect cases spanning grouping, removal, fire and paint plus clean controls.
    `chat`: finished photos from one property in the customer set (shared shoot_group_id), for chat edits.
    `requests`: a handful of real editor requests (downloaded once into data/cache/editor-requests).
    `showcase`: the live-demo shoot, reliable QC cases plus customer-request photos (see SHOWCASE_CASES)."""
    settings = settings or get_settings()
    if preset == "qc-tour":
        root = resolve_path(settings.autohdr_defect_dir, "AUTOHDR_DEFECT_DIR")
        dirs = [root / c for c in QC_TOUR_CASES if (root / c).is_dir()]
        if not dirs:
            raise FileNotFoundError(f"none of the qc-tour cases exist under {root}")
        return create_shoot_from_defect_cases(store, "QC tour: AutoHDR defect cases", dirs, {"preset": preset})
    if preset == "chat":
        root = resolve_path(settings.autohdr_customer_dir, "AUTOHDR_CUSTOMER_DIR")
        groups: dict[str, list[Path]] = {}
        for case_dir in sorted((root / "cases").iterdir()):
            cj = case_dir / "input" / "case.json"
            if cj.exists():
                gid = json.loads(cj.read_text(encoding="utf-8")).get("shoot_group_id") or case_dir.name
                groups.setdefault(gid, []).append(case_dir)
        if not groups:
            raise FileNotFoundError(f"no customer cases under {root / 'cases'}")
        gid, dirs = max(groups.items(), key=lambda kv: (len(kv[1]), kv[0]))
        shoot = store.create(f"Chat demo: property {gid}", source={"kind": "autohdr_customer", "preset": preset,
                                                                    "shoot_group_id": gid})
        for d in dirs[:8]:
            ingest_customer_case(store, shoot.id, d)
        return store.load(shoot.id)
    if preset == "requests":
        path = resolve_path(settings.autohdr_editor_requests, "AUTOHDR_EDITOR_REQUESTS")
        records = json.loads(path.read_text(encoding="utf-8"))["records"]
        picked, seen = [], set()
        for kw in CHAT_REQUEST_KEYWORDS:
            for r in records:
                if r["id"] not in seen and re.search(rf"\b{kw}", r.get("request", ""), re.IGNORECASE):
                    picked.append(r)
                    seen.add(r["id"])
                    break
        shoot = store.create("Editor requests", source={"kind": "editor_requests", "preset": preset})
        cache = settings.data_dir / "cache" / "editor-requests"
        for r in picked[:6]:
            ingest_editor_request(store, shoot.id, r, cache)
        return store.load(shoot.id)
    if preset == "showcase":
        root = resolve_path(settings.autohdr_defect_dir, "AUTOHDR_DEFECT_DIR")
        path = resolve_path(settings.autohdr_editor_requests, "AUTOHDR_EDITOR_REQUESTS")
        by_id = {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["records"]}
        missing = [c for c in SHOWCASE_CASES if not (root / c).is_dir()] + [r for r in SHOWCASE_REQUESTS if r not in by_id]
        if missing:
            raise FileNotFoundError(f"showcase inputs not found: {', '.join(missing)}")
        shoot = create_shoot_from_defect_cases(
            store, "Demo shoot: QC and customer requests", [root / c for c in SHOWCASE_CASES], {"preset": preset}
        )
        cache = settings.data_dir / "cache" / "editor-requests"
        for rid in SHOWCASE_REQUESTS:
            ingest_editor_request(store, shoot.id, by_id[rid], cache)
        return store.load(shoot.id)
    raise ValueError(f"unknown demo preset {preset!r}; use 'qc-tour', 'chat', 'requests' or 'showcase'")
