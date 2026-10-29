"""Film tools: the agent plans shots, writes and translates the script; deterministic code does the rest.

Every tool reads and writes one record, <shoot>/film/spec.json (film/spec.py). The agent chooses the shot
order, each shot's motion and camera direction, and the words; `prepare_shots` applies the generate,
check, regenerate-once, fall-back policy (film/shots.py), `narrate` speaks each language with one TTS
request, and `assemble_film` renders and finishes each language (film/assemble.py) and writes the manifest.
"""

from __future__ import annotations

import json
from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ..film.assemble import assemble_language, film_dir, write_manifest
from ..film.media import Media, MediaError
from ..film.narration import script_problems, speech_request
from ..film.shots import FilmContext, default_judge, prepare_shots as _prepare
from ..film.spec import FilmSpec, Narration, Rejected, Shot
from ..film.video import duration
from ._common import ToolError, contact_sheet, put_preview
from .base import ToolContext, ToolInput, ToolResult, tool


def spec_path(ctx: ToolContext):
    return ctx.store.shoot_dir(ctx.shoot_id) / "film" / "spec.json"


def load_spec(ctx: ToolContext) -> FilmSpec:
    spec = FilmSpec.load(spec_path(ctx))
    if spec is None:
        raise ToolError("There is no film spec yet; call plan_film first.")
    return spec


def _drop_manifest(ctx: ToolContext) -> None:
    """A new plan or script makes every assembled film stale; the API lists films from the manifest."""
    (ctx.store.shoot_dir(ctx.shoot_id) / "film" / "manifest.json").unlink(missing_ok=True)


def film_context(ctx: ToolContext) -> FilmContext:
    return FilmContext(ctx.store, ctx.shoot_id, ctx.trace, ctx.settings, Media(ctx.settings), default_judge(ctx.settings),
                       call_id=ctx.call_id)


def scope_of(ctx: ToolContext) -> list[str]:
    """Views this film may use: the run's scope, recorded in film/scope.json when the run starts."""
    path = ctx.store.shoot_dir(ctx.shoot_id) / "film" / "scope.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))["views"]
    return [v.id for v in ctx.store.load(ctx.shoot_id).views if v.head]


# ---- plan ---------------------------------------------------------------------------------------------------

class ShotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: str
    end_view: str | None = Field(None, description="Generated shots only: a second view the clip moves into "
                                                   "(through a doorway, say). Its photo becomes the clip's last frame.")
    motion: Literal["generated", "depth", "still"] = Field(
        description="generated: an AI clip, checked against the photo and replaced by a depth move if it changes the "
                    "room. depth: a parallax move from the photo's own pixels, invents nothing. still: slow push-in.")
    camera: str | None = Field(None, max_length=300, description="Generated shots: the camera move in one sentence, "
                               "e.g. 'Slow steady dolly forward toward the kitchen island'. Plain camera language only; "
                               "fixed wording that forbids changing the room is added for you.")
    aspect: Literal["9:16", "16:9", "source"] = Field("9:16", description="Generated clip frame. Use 9:16 unless a "
                                                      "recorded clip you are reusing says otherwise.")
    move: Literal["dolly", "truck"] = Field("dolly", description="Depth move (also the fallback for a generated shot): "
                                            "dolly pushes forward, truck slides sideways across a wide room.")
    label: str = Field(max_length=28, description="On-screen room label, e.g. 'Primary suite'.")
    end_label: str | None = Field(None, max_length=28, description="Shots with end_view: the end view's room label. "
                                  "If the clip falls back, each photo plays on its own under its own label.")
    seconds: float = Field(4.0, ge=2.0, le=8.0, description="Planned length; the narration sets the final length.")


class PlanFilmIn(ToolInput):
    shots: list[ShotIn] = Field(min_length=3, max_length=12, description="Shots in walk-through order.")


@tool(
    "plan_film",
    "Set the film's shots in walk-through order: one per photo you use, each with its motion source, camera "
    "direction (generated shots) and room label. Replanning clears earlier clips and scripts.",
    kind="shoot",
)
def plan_film(ctx: ToolContext, args: PlanFilmIn) -> ToolResult:
    scope = set(scope_of(ctx))
    problems = []
    for i, s in enumerate(args.shots):
        for v in [s.view] + ([s.end_view] if s.end_view else []):
            if v not in scope:
                problems.append(f"shot {i}: view {v} is not in this film's views ({', '.join(sorted(scope))})")
        if s.motion == "generated" and not (s.camera or "").strip():
            problems.append(f"shot {i}: a generated shot needs a camera direction")
        if s.end_view and s.motion != "generated":
            problems.append(f"shot {i}: end_view applies only to generated shots")
        if s.end_view and not (s.end_label or "").strip():
            problems.append(f"shot {i}: give end_label, the room label for {s.end_view}")
    if problems:
        raise ToolError("; ".join(problems))
    old = FilmSpec.load(spec_path(ctx))
    shoot = ctx.store.load(ctx.shoot_id)
    rejected = list(old.rejected) if old else []
    for s in (old.shots if old else []):
        for a in (s.result.attempts if s.result else []):
            if not a.passed and all(r.attempt.fingerprint != a.fingerprint or r.attempt.prompt != a.prompt for r in rejected):
                rejected.append(Rejected(view=s.view, end_view=s.end_view, label=s.label, attempt=a))
    spec = FilmSpec(shots=[Shot(**{**s.model_dump(), "end_label": (s.end_label or "").strip()}) for s in args.shots],
                    listing=shoot.listing, rejected=rejected,
                    languages=old.languages if old else ["en"])
    spec.save(spec_path(ctx))
    _drop_manifest(ctx)
    kinds = {k: sum(s.motion == k for s in spec.shots) for k in ("generated", "depth", "still")}
    return ToolResult(summary=f"Planned {len(spec.shots)} shots ({kinds['generated']} generated, {kinds['depth']} depth, "
                              f"{kinds['still']} still), about {sum(s.seconds for s in spec.shots):.0f} s.",
                      data={"shots": [s.model_dump(exclude={"result"}) for s in spec.shots], "languages": spec.languages})


# ---- shots --------------------------------------------------------------------------------------------------

class PrepareShotsIn(ToolInput):
    shots: list[int] | None = Field(None, description="Shot indices to (re)make; default every shot without a clip.")


@tool(
    "prepare_shots",
    "Make every planned shot's clip, in parallel. Generated clips are checked against their photos; a clip that "
    "changes the room is regenerated once with the failure named, then replaced by a depth move. Returns what "
    "each shot will play and why.",
    kind="shoot",
)
def prepare_shots(ctx: ToolContext, args: PrepareShotsIn) -> ToolResult:
    spec = load_spec(ctx)
    wanted = args.shots if args.shots is not None else [i for i, s in enumerate(spec.shots) if s.result is None]
    bad = [i for i in wanted if not 0 <= i < len(spec.shots)]
    if bad:
        raise ToolError(f"No shots {bad}; the plan has shots 0-{len(spec.shots) - 1}.")
    fctx = film_context(ctx)
    results = _prepare(fctx, [(i, spec.shots[i]) for i in wanted])
    spec = load_spec(ctx)  # a concurrent replan would have replaced the file; write onto the current one
    rows, errors, sheets = [], [], []
    for i, res in sorted(results.items()):
        if isinstance(res, Exception):
            errors.append(f"shot {i}: {type(res).__name__}: {res}")
            continue
        spec.shots[i].result = res
        rows.append({"shot": i, "view": spec.shots[i].view, "plays": res.source, "fell_back": res.fell_back,
                     "note": res.note, "checks": [{"passed": a.passed, "reasons": a.reasons} for a in res.attempts]})
        sheets += [a.sheet for a in res.attempts if a.sheet]
    spec.save(spec_path(ctx))
    fell = sum(r["fell_back"] for r in rows)
    summary = (f"Prepared {len(rows)} shots: " + ", ".join(f"{r['shot']}={r['plays']}" for r in rows)
               + (f"; {fell} generated clip(s) failed the check and fell back to depth" if fell else "") + ".")
    if errors:
        summary += " Failed: " + "; ".join(errors)
    return ToolResult(summary=summary, data={"shots": rows, "errors": errors}, images=sheets[:4], ok=not errors)


# ---- script -------------------------------------------------------------------------------------------------

class WriteScriptIn(ToolInput):
    segments: list[str] = Field(description="One narration segment per shot, in shot order. The first is the hook "
                                            "(about 2 s, at most 8 English words); the last ends with a call to action.")


@tool(
    "write_script",
    "Write the narration in the film's first language: one segment per shot, spoken while that shot plays. "
    "Describe only what the photos show; beds, baths, size and price only if the listing gives them.",
    kind="shoot",
)
def write_script(ctx: ToolContext, args: WriteScriptIn) -> ToolResult:
    spec = load_spec(ctx)
    lang = spec.languages[0]
    problems = script_problems(args.segments, lang, spec.listing, len(spec.shots))
    if problems:
        raise ToolError("Rewrite the script: " + "; ".join(problems) + ".")
    spec.narrations[lang] = Narration(language=lang, segments=[s.strip() for s in args.segments],
                                      labels=[s.label for s in spec.shots], end_labels=[s.end_label for s in spec.shots])
    for other in spec.languages[1:]:
        spec.narrations.pop(other, None)  # translations of an older script no longer match
    spec.save(spec_path(ctx))
    _drop_manifest(ctx)
    return ToolResult(summary=f"Script saved ({lang}, {len(spec.narrations[lang].text.split())} words).",
                      data={"language": lang, "text": spec.narrations[lang].text})


class TranslateIn(ToolInput):
    language: str = Field(description="Language code, e.g. 'es'.")
    segments: list[str] = Field(description="The script translated, one segment per shot, same order.")
    labels: list[str] = Field(description="The room labels translated, one per shot.")
    end_labels: list[str] | None = Field(None, description="The end labels translated, one per shot ('' for shots "
                                         "without an end view). Required when any shot has an end view.")


@tool(
    "translate_script",
    "Add the script in another of the film's languages: a natural translation of each segment and room label "
    "for local buyers, with the same facts and no new ones.",
    kind="shoot",
)
def translate_script(ctx: ToolContext, args: TranslateIn) -> ToolResult:
    spec = load_spec(ctx)
    lang = args.language.strip().lower()
    if lang not in spec.languages[1:]:
        raise ToolError(f"This film's other languages are {spec.languages[1:] or 'none'}; {lang!r} is not one of them.")
    if spec.languages[0] not in spec.narrations:
        raise ToolError("Write the first-language script with write_script before translating it.")
    problems = script_problems(args.segments, lang, spec.listing, len(spec.shots), hook=False)
    if len(args.labels) != len(spec.shots):
        problems.append(f"give one label per shot ({len(spec.shots)})")
    end_labels = [x.strip() for x in (args.end_labels or [""] * len(spec.shots))]
    if len(end_labels) != len(spec.shots):
        problems.append(f"give one end label per shot ({len(spec.shots)}), '' where a shot has no end view")
    else:
        problems += [f"shot {i}: translate its end label" for i, s in enumerate(spec.shots) if s.end_view and not end_labels[i]]
    if problems:
        raise ToolError("Revise the translation: " + "; ".join(problems) + ".")
    spec.narrations[lang] = Narration(language=lang, segments=[s.strip() for s in args.segments],
                                      labels=[s.strip() for s in args.labels], end_labels=end_labels)
    spec.save(spec_path(ctx))
    return ToolResult(summary=f"Saved the {lang} script ({len(spec.narrations[lang].text.split())} words).",
                      data={"language": lang, "text": spec.narrations[lang].text})


# ---- narrate ------------------------------------------------------------------------------------------------

class NarrateIn(ToolInput):
    languages: list[str] | None = Field(None, description="Default: every language with a script.")


@tool(
    "narrate",
    "Speak each language's script with one TTS request (or the recorded take of exactly that text).",
    kind="shoot",
)
def narrate(ctx: ToolContext, args: NarrateIn) -> ToolResult:
    spec = load_spec(ctx)
    langs = args.languages or [lang for lang in spec.languages if lang in spec.narrations]
    missing = [lang for lang in langs if lang not in spec.narrations]
    if missing:
        raise ToolError(f"No script for {', '.join(missing)}; write or translate it first.")
    media = Media(ctx.settings)
    out, errors = {}, []
    for lang in langs:
        n = spec.narrations[lang]
        try:
            res = media.make(speech_request(ctx.settings, lang, n.text))
        except MediaError as e:
            errors.append(f"{lang}: {e}")
            continue
        ctx.trace.emit("media", call_id=ctx.call_id, language=lang, fingerprint=res.fingerprint, backend=res.backend,
                       cached=res.cached, seconds=res.seconds)
        n.audio, n.backend = res.fingerprint, res.backend
        out[lang] = round(duration(res.path), 1)
    spec.save(spec_path(ctx))
    summary = "Narrated " + ", ".join(f"{k} ({v} s)" for k, v in out.items()) if out else "No narration was made"
    if errors:
        summary += ". Failed: " + "; ".join(errors)
    return ToolResult(summary=summary + ".", data={"seconds": out, "errors": errors}, ok=not errors)


# ---- assemble -----------------------------------------------------------------------------------------------

class AssembleIn(ToolInput):
    languages: list[str] | None = Field(None, description="Default: every narrated language.")


@tool(
    "assemble_film",
    "Render each narrated language: shots timed to the narration, labels, captions, music under the voice, cover "
    "and verify, plus the film's manifest. Returns five frames per language so you can check the result.",
    kind="shoot",
)
def assemble_film(ctx: ToolContext, args: AssembleIn) -> ToolResult:
    spec = load_spec(ctx)
    langs = args.languages or [lang for lang in spec.languages if spec.narrations.get(lang) and spec.narrations[lang].audio]
    if not langs:
        raise ToolError("No language has narration audio yet; call narrate first.")
    unready = [i for i, s in enumerate(spec.shots) if s.result is None]
    if unready:
        raise ToolError(f"Shots {unready} have no clip yet; call prepare_shots first.")
    fctx = film_context(ctx)
    manifest_path = film_dir(fctx) / "manifest.json"
    variants: dict = {}
    if manifest_path.is_file():
        variants = {k: v for k, v in json.loads(manifest_path.read_text(encoding="utf-8")).get("languages", {}).items()
                    if k in spec.languages and k not in langs}
    images = []
    for lang in langs:
        variants[lang] = assemble_language(fctx, spec, lang)
        variants[lang]["url"] = f"/api/shoots/{ctx.shoot_id}/film/{lang}.mp4"
        images.append(put_preview(ctx, _strip(film_dir(fctx) / lang / "film.mp4", lang), long_edge=1568))
    write_manifest(fctx, spec, variants)
    lines = []
    for lang in langs:
        v = variants[lang]
        verify = v["verify"] or {}
        lines.append(f"{lang}: {v['duration_s']} s, finished by {v['finisher']}, captions {'on' if v['captions'] else 'off'}"
                     + (f", verify {'ok' if verify.get('ok') else 'failed: ' + ', '.join(verify.get('fails') or [])}" if verify else ""))
    return ToolResult(summary="Assembled " + "; ".join(lines) + ".",
                      data={"languages": {k: variants[k] for k in langs}, "manifest": f"/api/shoots/{ctx.shoot_id}/film/manifest.json"},
                      images=images)


def _strip(path, label: str) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    tiles = []
    for frac in (0.04, 0.25, 0.5, 0.75, 0.97):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * frac))
        ok, frame = cap.read()
        if ok:
            tiles.append((f"{label} {frac:.0%}", cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    cap.release()
    if not tiles:
        raise ToolError(f"The {label} film rendered but no frames could be read back from {path}.")
    return contact_sheet(tiles, cols=len(tiles), tile_w=300)


# ---- run framing --------------------------------------------------------------------------------------------

def start_film(ctx: ToolContext, scope: list[str], languages: list[str]) -> None:
    """Record the run's views and languages before the agent's first turn."""
    root = ctx.store.shoot_dir(ctx.shoot_id) / "film"
    root.mkdir(parents=True, exist_ok=True)
    (root / "scope.json").write_text(json.dumps({"views": scope}), encoding="utf-8")
    shoot = ctx.store.load(ctx.shoot_id)
    spec = FilmSpec.load(spec_path(ctx)) or FilmSpec()
    if spec.languages != languages:
        spec.narrations = {k: v for k, v in spec.narrations.items() if k in languages and k == languages[0]}
    spec.languages, spec.listing = languages, shoot.listing
    spec.rejected = []  # rejections belong to the run that made them; this run starts its own film
    spec.save(spec_path(ctx))


def film_opening(ctx: ToolContext, scope: list[str], languages: list[str]) -> list[str]:
    """Opening lines for a film run: languages, media mode, and the clips and narrations already on hand."""
    from ..film.narration import STYLE

    media = Media(ctx.settings)
    fctx = film_context(ctx)
    shoot = ctx.store.load(ctx.shoot_id)
    sha_to_view = {fctx.photo(v).sha: v for v in scope}
    lines = [
        f"Make the listing film from the views in scope, narrated in: {', '.join(languages)} "
        f"(write the script in {languages[0]}" + (", then translate it)." if len(languages) > 1 else ")."),
        "Listing fields (the only source for beds, baths, size and price): "
        + (shoot.listing.model_dump_json(exclude_none=True) if shoot.listing.model_dump(exclude_none=True) else "none given"),
    ]
    if ctx.settings.film_media == "replay":
        lines.append("Media mode: replay. This machine cannot generate: only the recorded clips and narrations listed "
                     "below exist. A generated shot with any other camera direction fails and falls back to depth, and a "
                     "narration whose text differs from a recording by one character has no audio.")
    else:
        lines.append("Media mode: live. Generated clips come from Grok Imagine (about a minute each, one at a time); "
                     "narration from Gemini TTS. Clips listed below are already made and cost nothing to reuse.")
    for v in scope:
        view = shoot.view(v)
        known = media.known_clips(fctx.photo(v).sha)
        desc = f"{v} ({view.room or view.label or 'room not labelled'})"
        if known:
            items = []
            for k in known:
                end = sha_to_view.get(k["images"][1]) if len(k["images"]) > 1 else None
                if len(k["images"]) > 1 and end is None:
                    continue  # its end photo is not in this film
                items.append(f"camera={_camera_of(k)!r}, aspect={k['aspect']!r}" + (f", end_view={end!r}" if end else ""))
            if items:
                desc += ": recorded generated clips: " + "; ".join(items)
        lines.append(desc)
    speech = [s for s in media.known_speech() if s["voice"] == ctx.settings.tts_voice and s["model"] == ctx.settings.tts_model
              and s["style"] == STYLE and s["language"] in languages]
    for s in speech:
        lines.append(f"Recorded narration ({s['language']}), usable only verbatim: {s['text']}")
    return lines


def _camera_of(req: dict) -> str:
    """The camera direction inside a recorded clip prompt (the inverse of media.clip_prompt)."""
    from ..film.media import REF_GUARD, REF_PREFIX, VIDEO_GUARD

    p = req["prompt"]
    if req["mode"] == "video":
        return p.removesuffix(VIDEO_GUARD)
    return p.removeprefix(REF_PREFIX).removesuffix(REF_GUARD)
