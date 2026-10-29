"""Turn each planned shot into the clip that will play, under a fixed policy the agent cannot bypass.

- depth and still shots render locally from the photo.
- A generated shot asks the clip backend for a clip, then checks it against the photo(s). A failed check
  triggers at most one regeneration, with the failure named in the prompt; a second failure, or a
  backend that cannot serve the request (not recorded, not signed in, refused), falls back to a depth
  move of the same photo(s).

Every decision is a trace event: `media` for each clip produced or served from cache, `check` for each
generated clip, `retry` before a regeneration or a fallback, and `shot` with the outcome. Shots run on a
thread pool of `settings.film_workers`; the grok backend serialises its own calls.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..agent.usage import usage_event
from ..config import Settings
from ..store import ShootStore
from ..trace import TraceRun
from .check import Judge, check_clip, claude_judge
from .media import DepthRequest, Media, MediaError, Photo, StillRequest, clip_request
from .spec import Attempt, Shot, ShotResult

if TYPE_CHECKING:  # tools/ imports this module through tools/film.py; import it lazily at run time
    from ..tools.base import ToolContext

MAX_GENERATIONS = 2  # the first clip and one regeneration naming its failure
NO_RETRY = {"not_cached", "unavailable", "moderation", "allowance", "zdr"}  # a second call cannot succeed


@dataclass
class FilmContext:
    store: ShootStore
    shoot_id: str
    trace: TraceRun
    settings: Settings
    media: Media
    judge: Judge | None = claude_judge
    call_id: str | None = None
    photos: dict[str, Photo] = field(default_factory=dict)
    versions: dict[str, tuple[str, str]] = field(default_factory=dict)  # view -> (version id, blob) read

    def tool_ctx(self) -> ToolContext:
        from ..tools.base import ToolContext

        return ToolContext(self.store, self.shoot_id, self.trace, self.settings, call_id=self.call_id)

    def photo(self, view_id: str) -> Photo:
        """The view's current head as a Photo (cached per run)."""
        if view_id not in self.photos:
            view = self.store.load(self.shoot_id).view(view_id)
            self.photos[view_id] = Photo.from_file(self.store.blob_path(self.shoot_id, view.head_version.blob))
            self.versions[view_id] = (view.head, view.head_version.blob)
        return self.photos[view_id]


def _emit_media(ctx: FilmContext, view: str, index: int, res) -> None:
    ctx.trace.emit("media", view=view, call_id=ctx.call_id, shot=index, fingerprint=res.fingerprint,
                   backend=res.backend, cached=res.cached, seconds=res.seconds)


def _rejection(reasons: list[str], judge: dict | None) -> str:
    """The failure to name in the regeneration prompt: the judge's own words when it saw a change, since
    they name the object, else the first signal."""
    if judge and judge.get("changes"):
        return "; ".join(f"{c['object']} {c['change'].replace('_', ' ')}" for c in judge["changes"])
    return reasons[0] if reasons else "the clip did not match the photo"


def _depth(ctx: FilmContext, index: int, shot: Shot) -> list[str]:
    fps = []
    for view in [shot.view] + ([shot.end_view] if shot.end_view else []):
        photo = ctx.photo(view)
        res = ctx.media.make(DepthRequest(image=photo.sha, move=shot.move, parallax_px=ctx.settings.depth_parallax_px),
                             {photo.sha: photo})
        _emit_media(ctx, view, index, res)
        fps.append(res.fingerprint)
    return fps


def _check(ctx: FilmContext, index: int, attempt_no: int, shot: Shot, fp: str, path: Path) -> Attempt:
    first = ctx.photo(shot.view)
    last = ctx.photo(shot.end_view) if shot.end_view else None
    started = time.perf_counter()
    result = check_clip(path, first.rgb(), last.rgb() if last else None, judge=ctx.judge)
    judge = result.judge
    if judge and judge.get("model"):
        usage: dict[str, Any] = usage_event(judge["model"], {"input_tokens": judge.get("input_tokens", 0),
                                                             "output_tokens": judge.get("output_tokens", 0)})
        if judge.get("usd") is not None:
            usage["usd"] = judge["usd"]
            usage.pop("unpriced", None)
        ctx.trace.emit("usage", view=shot.view, purpose="judge_clip", **usage)
    from ..tools._common import put_preview

    sheet = put_preview(ctx.tool_ctx(), result.sheet, long_edge=1568)
    compact = {k: v for k, v in result.signals.items() if k != "judge"}
    ctx.trace.emit("check", view=shot.view, call_id=ctx.call_id, shot=index, attempt=attempt_no, clip=fp,
                   passed=result.passed, failures=result.reasons, judge=judge, signals=compact, images=[sheet],
                   ms=int((time.perf_counter() - started) * 1000))
    return Attempt(fingerprint=fp, passed=result.passed, reasons=result.reasons, judge=judge, sheet=sheet)


def prepare_shot(ctx: FilmContext, index: int, shot: Shot) -> ShotResult:
    view = shot.view
    if shot.motion == "still":
        photo = ctx.photo(view)
        res = ctx.media.make(StillRequest(image=photo.sha), {photo.sha: photo})
        _emit_media(ctx, view, index, res)
        out = ShotResult(source="still", clips=[res.fingerprint])
    elif shot.motion == "depth":
        out = ShotResult(source="depth", clips=_depth(ctx, index, shot))
    else:
        out = _generated(ctx, index, shot)
    out.photos = [{"view": v, "version": ctx.versions[v][0], "blob": ctx.versions[v][1]}
                  for v in [shot.view] + ([shot.end_view] if shot.end_view else [])]
    ctx.trace.emit("shot", view=view, call_id=ctx.call_id, shot=index, end_view=shot.end_view, planned=shot.motion,
                   source=out.source, fell_back=out.fell_back, clips=out.clips, note=out.note)
    return out


def _generated(ctx: FilmContext, index: int, shot: Shot) -> ShotResult:
    if not (shot.camera or "").strip():
        raise ValueError(f"shot {index} is generated but has no camera direction")
    first = ctx.photo(shot.view)
    last = ctx.photo(shot.end_view) if shot.end_view else None
    photos = {p.sha: p for p in (first, last) if p}
    attempts: list[Attempt] = []
    rejected: str | None = None
    note = ""
    for n in range(1, MAX_GENERATIONS + 1):
        req = clip_request(shot.camera, shot.aspect, first, last, rejected=rejected)
        try:
            res = ctx.media.make(req, photos)
        except MediaError as e:
            attempts.append(Attempt(backend=ctx.media.backend_name("clip"), prompt=req.prompt, reasons=[str(e)]))
            note = (note + "; " if note else "") + f"generation {n} unavailable ({e.category})"
            ctx.trace.emit("retry", view=shot.view, call_id=ctx.call_id, shot=index, attempt=n,
                           max_attempts=MAX_GENERATIONS, reason=str(e), next="depth fallback")
            break
        _emit_media(ctx, shot.view, index, res)
        try:
            att = _check(ctx, index, n, shot, res.fingerprint, res.path)
        except Exception as e:  # noqa: BLE001 - a clip the checker cannot read must not play
            att = Attempt(fingerprint=res.fingerprint, reasons=[f"the clip could not be checked ({type(e).__name__}: {e})"])
            ctx.trace.emit("check", view=shot.view, call_id=ctx.call_id, shot=index, attempt=n, clip=res.fingerprint,
                           passed=False, failures=att.reasons, judge=None, signals={}, images=[], ms=0)
        att.backend, att.prompt, att.cached, att.seconds = res.backend, req.prompt, res.cached, res.seconds
        attempts.append(att)
        if att.passed:
            return ShotResult(source="generated", clips=[res.fingerprint], attempts=attempts,
                              note="passed the clip check" + (" on regeneration" if n > 1 else ""))
        rejected = _rejection(att.reasons, att.judge)
        last_try = n == MAX_GENERATIONS
        note = f"generated clip {n} rejected: {rejected}"
        ctx.trace.emit("retry", view=shot.view, call_id=ctx.call_id, shot=index, attempt=n,
                       max_attempts=MAX_GENERATIONS, reason="; ".join(att.reasons),
                       next="depth fallback" if last_try else "regenerate with the failure named in the prompt")
    return ShotResult(source="depth", clips=_depth(ctx, index, shot), attempts=attempts, fell_back=True,
                      note=note + "; fell back to a depth move")


def prepare_shots(ctx: FilmContext, shots: list[tuple[int, Shot]]) -> dict[int, ShotResult | Exception]:
    """Prepare shots in parallel. A shot that raises is returned as its exception so the others still finish."""

    def one(item):
        i, shot = item
        try:
            return i, prepare_shot(ctx, i, shot)
        except Exception as e:  # noqa: BLE001 - reported per shot to the agent
            ctx.trace.emit("error", view=shot.view, message=f"shot {i}: {type(e).__name__}: {e}")
            return i, e

    workers = max(1, min(ctx.settings.film_workers, len(shots)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(one, shots))
