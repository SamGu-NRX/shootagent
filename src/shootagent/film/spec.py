"""Film spec: the JSON the agent edits through the film tools. `assemble.py` renders it deterministically.

A film is an ordered list of shots. Each shot shows one photo (or moves from one photo to another) with
one motion source:

- generated: an image-to-video clip (Grok Imagine), checked against the photo before it may play;
- depth: a camera move rendered from the photo's own pixels with estimated depth (invents nothing);
- still: a slow push-in on the photo.

The narration is one script per language, split into one segment per shot, so each shot plays while its
segment is spoken. Shots, their outcomes and the narrations all live here, so the manifest and every
re-render read one record.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..store import Listing

MotionSource = Literal["generated", "depth", "still"]
DepthMove = Literal["dolly", "truck"]
Aspect = Literal["9:16", "16:9", "source"]

# California AB 723 (2024) requires a real-estate ad that uses a digitally altered image to say so. A
# generated clip can show things the photo does not, so it carries this label while it plays; depth moves
# and stills reproject or crop the photo's own pixels and carry none.
DISCLOSURE = {"en": "AI-generated motion", "es": "Movimiento generado por IA"}
END_CARD = {"en": "SCHEDULE A TOUR", "es": "AGENDA TU VISITA"}


class Attempt(BaseModel):
    """One generated clip and its check."""

    fingerprint: str | None = None  # media cache key; None when generation itself failed
    backend: str | None = None
    prompt: str | None = None
    cached: bool = False
    passed: bool = False
    reasons: list[str] = Field(default_factory=list)  # why the check failed, or why generation failed
    judge: dict | None = None
    sheet: str | None = None  # blob of the contact sheet the check looked at
    seconds: float | None = None  # generation time, when it was generated in this run


class ShotResult(BaseModel):
    """What actually plays for a shot, decided by `film.shots.prepare_shot`."""

    source: MotionSource
    clips: list[str]  # media fingerprints, in play order: one, or two when a two-photo shot fell back
    attempts: list[Attempt] = Field(default_factory=list)
    fell_back: bool = False
    note: str = ""
    photos: list[dict] = Field(default_factory=list)  # [{view, version, blob}] the clips were made from
    depth_checks: list[Attempt] = Field(default_factory=list)  # checks of the stronger depth move, per photo


class Shot(BaseModel):
    view: str
    end_view: str | None = None  # a generated clip that moves from `view` to this view
    motion: MotionSource
    camera: str | None = None  # camera direction for a generated clip, e.g. "Slow steady dolly forward toward the island"
    aspect: Aspect = "9:16"  # generated clip frame; "source" keeps the photo's own aspect
    move: DepthMove = "dolly"  # depth move, also used when a generated clip falls back
    label: str = ""  # on-screen room label in the first language
    end_label: str = ""  # the end view's room label, shown when a fallback plays the end photo on its own
    seconds: float = Field(4.0, ge=1.5, le=8.0)  # planned length; the narration decides the final length
    result: ShotResult | None = None


class Narration(BaseModel):
    language: str
    segments: list[str]  # one per shot, in shot order
    labels: list[str]  # on-screen room label per shot in this language
    end_labels: list[str] = Field(default_factory=list)  # per shot: the end view's label, "" without an end view
    audio: str | None = None  # media fingerprint of the spoken narration
    backend: str | None = None

    @property
    def text(self) -> str:
        return " ".join(s.strip() for s in self.segments if s.strip())


class Rejected(BaseModel):
    """A generated clip rejected under an earlier plan in this run. Kept so the manifest and the disclosure page
    still list it after the agent re-plans that shot (generated to depth, say)."""

    view: str
    end_view: str | None = None
    label: str = ""
    attempt: Attempt


class FilmSpec(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    shots: list[Shot] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=lambda: ["en"])  # the first is the script's own language
    narrations: dict[str, Narration] = Field(default_factory=dict)
    # A track id from film/music/tracks.json, or "synth" for the generated bed in reel/music.py.
    music: str = "cinematic-calm"
    disclosure: dict[str, str] = Field(default_factory=lambda: dict(DISCLOSURE))
    listing: Listing = Field(default_factory=Listing)
    rejected: list[Rejected] = Field(default_factory=list)

    def disclosure_text(self, language: str) -> str:
        return self.disclosure.get(language) or self.disclosure.get(language.split("-")[0]) or self.disclosure["en"]

    @classmethod
    def load(cls, path: Path) -> FilmSpec | None:
        return cls.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=1), encoding="utf-8")
        tmp.replace(path)
