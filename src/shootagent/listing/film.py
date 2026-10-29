"""The listing film as the buyer page and the disclosure need it, read from the files the film pass writes
(film/assemble.py on feat/film):

    <shoot>/film/manifest.json      shots with source photos, motion source, checks and per-language times
    <shoot>/film/spec.json          the film spec; used for shots while no manifest exists yet
    <shoot>/film/<lang>/film.mp4    one film per language
    <shoot>/film/<lang>/cover.png

Reading files rather than importing the film package keeps this module independent of it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..store import ShootStore

LANG_RE = re.compile(r"^[a-z]{2}(-[a-z0-9]{2,8})?$")


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def film_dir(store: ShootStore, shoot_id: str) -> Path:
    return store.shoot_dir(shoot_id) / "film"


def film_file(store: ShootStore, shoot_id: str, lang: str, name: str) -> Path | None:
    """Path of film.mp4 or cover.png for a language, or None when the language code is malformed."""
    if not LANG_RE.match(lang) or name not in ("film.mp4", "cover.png"):
        return None
    return film_dir(store, shoot_id) / lang / name


def _attempt(a: dict) -> dict:
    judge = a.get("judge") or {}
    return {
        "passed": bool(a.get("passed")),
        "reasons": [str(r) for r in a.get("reasons") or []],
        "changes": [
            {k: c.get(k) for k in ("object", "change", "first_frame", "where")}
            for c in judge.get("changes") or []
            if isinstance(c, dict)
        ],
    }


def read_film(store: ShootStore, shoot_id: str) -> dict | None:
    """Shots and language variants of the latest film, or None before the first film run."""
    root = film_dir(store, shoot_id)
    manifest, spec = _json(root / "manifest.json"), _json(root / "spec.json")
    if manifest is None and spec is None:
        return None
    shoot = store.load(shoot_id)
    rooms = {v.id: v.room for v in shoot.views}
    shots: list[dict] = []
    if manifest is not None:
        for s in manifest.get("shots") or []:
            photos = s.get("source_photos") or []
            views = [p.get("view") for p in photos]
            shots.append({
                "index": s.get("index", len(shots)),
                "view": views[0] if views else None,
                "end_view": views[1] if len(views) > 1 else None,
                "room": rooms.get(views[0]) if views else None,
                "labels": s.get("labels") or {},
                "planned": s.get("planned_motion"),
                "source": s.get("motion_source"),
                "fell_back": bool(s.get("fell_back")),
                "ai_label": bool(s.get("ai_label")),
                "note": s.get("note") or "",
                "times": s.get("times") or {},
                "attempts": [_attempt(a) for a in s.get("attempts") or []],
            })
        langs = list((manifest.get("languages") or {}).keys())
        disclosure = (manifest.get("disclosure") or {}).get("label") or {}
    else:
        narrations = spec.get("narrations") or {}
        for i, s in enumerate(spec.get("shots") or []):
            res = s.get("result") or {}
            labels = {lang: n["labels"][i] for lang, n in narrations.items() if i < len(n.get("labels") or [])}
            shots.append({
                "index": i,
                "view": s.get("view"),
                "end_view": s.get("end_view"),
                "room": rooms.get(s.get("view")),
                "labels": labels or {(spec.get("languages") or ["en"])[0]: s.get("label") or ""},
                "planned": s.get("motion"),
                "source": res.get("source"),
                "fell_back": bool(res.get("fell_back")),
                "ai_label": res.get("source") == "generated",
                "note": res.get("note") or "",
                "times": {},
                "attempts": [_attempt(a) for a in res.get("attempts") or []],
            })
        langs = list(spec.get("languages") or [])
        disclosure = spec.get("disclosure") or {}
    variants = {}
    for lang in langs:
        if not LANG_RE.match(lang) or not (root / lang / "film.mp4").is_file():
            continue
        meta = ((manifest or {}).get("languages") or {}).get(lang) or {}
        variants[lang] = {
            "duration_s": meta.get("duration_s"),
            "captions": meta.get("captions"),
            "cover": (root / lang / "cover.png").is_file(),
        }
    return {
        "complete": manifest is not None,
        "languages": [lang for lang in langs if lang in variants],
        "variants": variants,
        "shots": shots,
        "disclosure_label": disclosure,
    }
