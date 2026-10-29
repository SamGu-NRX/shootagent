"""One reel, start to finish: voice -> plan -> (cards | photos | score) in parallel -> frames into ffmpeg -> cover.

Frames are pure functions of time (plan.py fixes every timing up front, layers.py animations are pure), so they
render on a thread pool in any order; a bounded window of frames in flight keeps memory flat while ffmpeg
encodes them in order. The mix is built before the first frame: score (ducked about 10 dB under the voice and
cut out after a record scratch), voice lines and effects, mastered to -14 LUFS with peaks under -1 dBFS.
"""
from __future__ import annotations

import bisect
import os
import shutil
import subprocess
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..context import LabContext
from ..schema import CardSpec, Reel, ReelConcept
from . import audio, camera, cards, endings, kinetic, music, sfx, transitions
from . import voice as tts
from .captions import Captions, cues_for_line
from .fonts import clean
from .layers import Overlay
from .look import look_for
from .plan import Plan, make_plan, timeline

HOOK_POS = {"sticker": "top", "label": "bottom", "typewriter": "center"}


@dataclass
class Settings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    workers: int = min(8, os.cpu_count() or 4)

    @property
    def preset(self) -> str:  # small test renders do not need medium's compression
        return "medium" if self.width >= 720 else "veryfast"


@dataclass
class Job:
    ctx: LabContext
    concept: ReelConcept
    reel_id: str
    label: str
    kind: str
    voice: bool
    cfg: Settings
    warnings: list[str] = field(default_factory=list)

    def progress(self, msg: str) -> None:
        self.ctx.progress("render", f"Reel {self.reel_id}: {msg}")


def render(job: Job) -> Reel:
    t_start = time.monotonic()
    ctx, concept, cfg = job.ctx, job.concept, job.cfg
    W, H, fps = cfg.width, cfg.height, cfg.fps
    shoot = ctx.store.load(ctx.shoot_id)
    usable = [v for v in shoot.views if v.head]
    if not usable:
        raise ValueError("The shoot has no photos to put in a reel.")
    ordered = [v for v in usable if v.approved] + [v for v in usable if not v.approved]
    view_ids = [v.id for v in ordered]
    out_dir = ctx.lab_dir / "reels"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = ctx.lab_dir.resolve() / "studio"

    job.progress("voiceover")
    clips = _voice(job, cache)
    plan = make_plan(concept, view_ids, {k: c.seconds for k, c in clips.items()})
    job.warnings += plan.warnings
    look = look_for(concept.music.mood)
    cv = kinetic.Canvas(W, H, captions=bool(clips))
    listing = shoot.listing

    card_jobs, card_kinds = _card_jobs(plan, concept, listing.price, look, job)
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        job.progress("cards, photos and score")
        f_cards = pool.submit(cards.render_all, card_jobs, cache / "cards")
        needed = sorted({s.view for s in plan.shots})
        f_src = {v: pool.submit(_source, ctx, shoot.view(v), W, H) for v in needed}
        n_frames = max(1, round(plan.total * fps))
        f_mix = pool.submit(_mix, plan, concept, clips, n_frames / fps)
        srcs = {v: f.result() for v, f in f_src.items()}
        cams = _cameras(plan, srcs)
        overlays, grade_at = _overlays(plan, concept, listing, cv, look, job)
        captions = Captions(_cues(plan, clips), cv, look)
        errors = f_cards.result()
        _attach_cards(card_jobs, errors, overlays, plan, cv, look, job)
        mix = f_mix.result()
    finally:
        pool.shutdown(wait=True)

    video = out_dir / f"{job.reel_id}.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        wav = audio.write_wav(Path(tmp) / "mix.wav", mix)
        job.progress("frames")
        frame = _frame_fn(plan, srcs, cams, overlays, grade_at, captions, cv, fps)
        _encode(job, frame, n_frames, wav, video, Path(tmp))
    poster = out_dir / f"{job.reel_id}.jpg"
    _cover(plan, srcs, cams, concept, look, cv).save(poster, "JPEG", quality=92, subsampling=0)

    spoken = set(clips)
    script = " ".join(clean(getattr(line, "text", "")) for line in plan.lines)
    return Reel(id=job.reel_id, label=job.label, kind=job.kind, concept=concept.id, title=concept.title,
                video=f"reels/{video.name}", poster=f"reels/{poster.name}", seconds=round(n_frames / fps, 3),
                timeline=timeline(plan, concept, spoken, card_kinds), voiceover=script or None,
                has_voice=bool(clips), music=f"{concept.music.mood}, {int(concept.music.bpm)} bpm",
                cards=sorted(set(card_kinds.values()), key=list(card_kinds.values()).index),
                warnings=job.warnings, render_ms=int((time.monotonic() - t_start) * 1000))


# ---- voice ----------------------------------------------------------------------------------------------------

def _voice(job: Job, cache: Path) -> dict[str, tts.Clip]:
    c = job.concept
    if not job.voice or c.voice.voice == "none":
        return {}
    lines = {"hook": c.hook.voiceover, **{f"b{k}": b.voiceover for k, b in enumerate(c.beats)},
             "end": c.ending.voiceover}
    lines = {k: v for k, v in lines.items() if v and clean(v)}
    if not lines:
        return {}
    try:
        return tts.synthesize(lines, c.voice.voice, c.voice.direction, cache / "voice")
    except Exception as e:  # no `say`, no voice installed, a failed line: the reel still renders, silently
        job.warnings.append(f"Rendered without a voiceover: {e}")
        return {}


# ---- cards ----------------------------------------------------------------------------------------------------

def _card_jobs(plan: Plan, concept: ReelConcept, price: str | None, look, job: Job):
    jobs: list[cards.CardJob] = []
    kinds: dict[int, str] = {}
    for k, b in enumerate(plan.beats):
        if not b.card or b.card.kind == "none":
            continue
        s = plan.beat_shot(k)
        t0 = s.start + max(0.15, s.t_in / 2 + 0.05)
        if k == 0:
            t0 = max(t0, min(plan.hook_end, s.end - 1.2))
        last = k == len(plan.beats) - 1 and concept.ending.style != "loop"
        t1 = plan.ending_start if last else s.end + s.t_out / 2
        region = cards.region_for(b.text_pos if b.text else None)
        params = cards.params_for(b.card, price, look, t1 - t0, region)
        if params is None:
            job.warnings.append(f"Beat {k + 1}: the {b.card.kind} card has nothing to show and was left out.")
            continue
        jobs.append(cards.CardJob(b.card.kind, params, t0, t1, b.card, region))
        kinds[k] = b.card.kind
    if concept.ending.style == "price_reveal" and price:
        text = clean(concept.ending.text)
        spec = CardSpec(kind="price_tag", value=price, text=text if len(text) <= 40 else None)
        region = {"x": 70, "y": 520, "w": 840, "h": 620}
        t0 = plan.ending_start + 0.35
        params = cards.params_for(spec, price, look, plan.total - t0 + 0.2, region)
        if params:
            jobs.append(cards.CardJob("price_tag", params, t0, plan.total + 0.5, spec, region))
            kinds[-1] = "price_tag"
    return jobs, kinds


def _attach_cards(jobs, errors, overlays, plan: Plan, cv, look, job: Job) -> None:
    beat_shot = {s.beat: i for i, s in enumerate(plan.shots) if s.key.startswith("b")}
    for cj, err in zip(jobs, errors):
        # The ending's price tag rides on the shot under the ending; a beat's card on its own shot.
        i = len(plan.shots) - 1 if cj.t0 >= plan.ending_start else beat_shot.get(_beat_of(plan, cj.t0), 0)
        layer = None
        if err is None and cj.path is not None:
            try:
                layer = cards.load(cj.path, cv.W, cv.H, cj.t0, cj.t1)
            except Exception as e:
                err = f"card could not be decoded: {e}"
        if layer is None:
            job.warnings.append(f"The {cj.kind} card was drawn without Reel Studio ({err}).")
            layer = cards.fallback(cj, cv, look)
        overlays[i].insert(0, layer)


def _beat_of(plan: Plan, t: float) -> int:
    starts = [plan.beat_shot(k).start for k in range(len(plan.beats))]
    return max(0, bisect.bisect_right(starts, t + 1e-6) - 1)


# ---- photos, cameras, overlays ----------------------------------------------------------------------------------

def _source(ctx: LabContext, view, W: int, H: int) -> camera.Source:
    return camera.prepare(ctx.store.get_image(ctx.shoot_id, view.head_version.blob), W, H)


def _cameras(plan: Plan, srcs: dict[str, camera.Source]) -> list:
    cams = []
    for s in plan.shots:
        span = s.vis1 - s.vis0
        punch_at = (s.start - s.vis0) / span if s.motion == "punch_in" else 0.0
        if s.key == "loop":
            first = plan.shots[0]
            base = camera.move(srcs[first.view], first.motion, first.focus, first.vis1 - first.vis0,
                               (first.start - first.vis0) / (first.vis1 - first.vis0) if first.motion == "punch_in"
                               else 0.0)
            # p = 1 is the reel's end: the camera arrives on the opening frame exactly as the loop restarts it.
            cams.append(camera.loop_lead_in(base, srcs[s.view], span, first.vis1 - first.vis0))
        else:
            cams.append(camera.move(srcs[s.view], s.motion, s.focus, span, punch_at))
    return cams


def _overlays(plan: Plan, concept: ReelConcept, listing, cv, look, job: Job):
    shots = plan.shots
    overlays: list[list] = [[] for _ in shots]
    ticks = plan.grid.ticks
    hook = concept.hook
    first = shots[0]
    # plan.hook_end is the first shot's end when nothing else is on it; then the hook rides out the transition.
    hook_out = first.vis1 if plan.hook_end >= first.end else plan.hook_end
    if clean(hook.text):
        overlays[0].append(kinetic.build(hook.style, hook.text, HOOK_POS.get(hook.style, "center"), 0.04, hook_out, cv,
                                         look, ticks, plan.numbers.get(-1)))
    last_beat = len(plan.beats) - 1
    for k, b in enumerate(plan.beats):
        if not b.text or not clean(b.text):
            continue
        i = next(j for j, s in enumerate(shots) if s.beat == k and s.key.startswith("b"))
        s = shots[i]
        t_in = s.start + max(0.12, s.t_in / 2 + 0.04)
        if k == 0:
            t_in = max(t_in, plan.hook_end + 0.08)
        t_out = plan.ending_start if (k == last_beat and concept.ending.style != "loop") else s.vis1
        if t_out - t_in < 0.35:
            job.warnings.append(f"Beat {k + 1}: no room left for its text after the hook.")
            continue
        overlays[i].append(kinetic.build(b.text_style or "bold_center", b.text, b.text_pos, t_in, t_out, cv, look,
                                         ticks, plan.numbers.get(k)))
    style, text, t0, t1 = concept.ending.style, concept.ending.text, plan.ending_start, plan.total
    end_i = len(shots) - 1
    grade_at = (end_i, endings.grade(style, t0))
    if style == "end_card":
        overlays[end_i].append(endings.end_card(text, listing, t0, t1 + 1, cv, look))
    elif style == "question":
        overlays[end_i].append(endings.question(text, t0, t1 + 1, cv, look))
    elif style == "punchline":
        overlays[end_i].append(endings.punchline(text, t0, t1, cv, look))
    elif style == "price_reveal":
        if len(clean(text)) > 40 or not listing.price:
            overlays[end_i].append(endings.price_line(text, t0, t1, cv, look))
    elif style == "loop":
        overlays[end_i].append(endings.loop_text(text, t0, t1, cv, look, ticks))
    return overlays, grade_at


def _cues(plan: Plan, clips: dict[str, tts.Clip]):
    lines = sorted((ln for ln in plan.lines if ln.key in clips), key=lambda ln: ln.at)
    out = []
    for j, ln in enumerate(lines):
        words = [(w.text, ln.at + w.t0, ln.at + w.t1) for w in clips[ln.key].words]
        nxt = lines[j + 1].at if j + 1 < len(lines) else None
        out += cues_for_line(words, nxt)
    return out


# ---- frames -----------------------------------------------------------------------------------------------------

def _shade(W: int, H: int) -> np.ndarray:
    """A soft top and bottom darkening (reel/render.py's scrim, lighter): depth, and room for type."""
    y = np.linspace(0, 1, H)
    top = 1 - 0.20 * np.clip(1 - y / 0.30, 0, 1) ** 2
    bottom = 1 - 0.30 * np.clip((y - 0.62) / 0.38, 0, 1) ** 2
    col = np.clip(top * bottom * 255, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(np.repeat(np.repeat(col[:, None], W, axis=1)[..., None], 3, axis=2))


def _frame_fn(plan: Plan, srcs, cams, overlays, grade_at, captions: Captions, cv, fps: int):
    shots = plan.shots
    starts = [s.start for s in shots]
    shade = _shade(cv.W, cv.H)
    grade_i, grade = grade_at
    jolt = 9 * cv.k

    def shot_frame(i: int, t: float) -> np.ndarray:
        s = shots[i]
        p = min(1.0, max(0.0, (t - s.vis0) / max(1e-3, s.vis1 - s.vis0)))
        off = camera.shake(t, s.start, jolt) if s.drop else (0.0, 0.0)
        img = camera.render(srcs[s.view], cams[i](p), off)
        img = cv2.multiply(img, shade, scale=1 / 255)
        if grade is not None and i == grade_i and t >= plan.ending_start:
            img = grade(img, t)
        for ov in overlays[i]:
            ov.draw(img, t)
        return img

    def direction(i: int) -> int:
        m = shots[i].motion
        return -1 if m == "pan_left" else 1 if m == "pan_right" else (1 if i % 2 else -1)

    def frame(k: int) -> bytes:
        t = k / fps
        i = max(0, bisect.bisect_right(starts, t) - 1)
        s = shots[i]
        if i > 0 and s.t_in > 0 and t < s.start + s.t_in / 2:
            u = (t - s.vis0) / s.t_in
            img = transitions.blend(s.transition, u, lambda: shot_frame(i - 1, t), lambda: shot_frame(i, t),
                                    s.t_in, fps, direction(i))
        elif i + 1 < len(shots) and shots[i + 1].t_in > 0 and t >= shots[i + 1].vis0:
            nxt = shots[i + 1]
            u = (t - nxt.vis0) / nxt.t_in
            img = transitions.blend(nxt.transition, u, lambda: shot_frame(i, t), lambda: shot_frame(i + 1, t),
                                    nxt.t_in, fps, direction(i + 1))
        else:
            img = shot_frame(i, t)
        img = np.ascontiguousarray(img)
        captions.draw(img, t)
        return img.tobytes()

    return frame


def _encode(job: Job, frame, n_frames: int, wav: Path, out: Path, tmp: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not on PATH. Install ffmpeg to render reels.")
    cfg = job.cfg
    part = out.with_name(out.stem + ".part.mp4")
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{cfg.width}x{cfg.height}", "-r", str(cfg.fps), "-i", "-",
           "-i", str(wav), "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", cfg.preset, "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{n_frames / cfg.fps:.3f}", "-movflags", "+faststart", str(part)]
    # stderr to a file: an unread pipe that fills up would block ffmpeg, and then us (as in reel/render.py).
    err_path = tmp / "ffmpeg.log"
    with err_path.open("wb") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        last_pct = -1
        try:
            with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
                window: deque = deque()
                nxt = 0
                for k in range(n_frames):
                    while nxt < n_frames and len(window) < cfg.workers * 3:
                        window.append(pool.submit(frame, nxt))
                        nxt += 1
                    proc.stdin.write(window.popleft().result())
                    if k % 15 == 0:
                        job.ctx.check_cancel()
                    pct = int(100 * (k + 1) / n_frames) // 10 * 10
                    if pct != last_pct and pct % 20 == 0:
                        last_pct = pct
                        job.progress(f"frames {pct}%")
                for f in window:
                    f.cancel()
            proc.stdin.close()
            if proc.wait(timeout=300) != 0:
                raise RuntimeError(f"ffmpeg failed: {err_path.read_text(errors='replace')[-400:]}")
        except BaseException as e:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            part.unlink(missing_ok=True)
            if isinstance(e, BrokenPipeError):
                raise RuntimeError(f"ffmpeg exited early: {err_path.read_text(errors='replace')[-400:]}") from e
            raise
    os.replace(part, out)


# ---- audio ------------------------------------------------------------------------------------------------------

def _mix(plan: Plan, concept: ReelConcept, clips: dict[str, tts.Clip], seconds: float) -> np.ndarray:
    n = int(round(seconds * audio.SR))
    m = concept.music
    score = music.score(m.mood, m.bpm, seconds, plan.drop, plan.grid.phase, seed=7)
    score = np.pad(score, ((0, max(0, n - len(score))), (0, 0)))[:n]
    gain = np.ones(n)
    # Duck about 10 dB under every spoken line, with short ramps either side.
    duck = 10 ** (-10 / 20)
    ramp = int(0.09 * audio.SR)
    for ln in plan.lines:
        if ln.key not in clips:
            continue
        a = int((ln.at - 0.06) * audio.SR)
        b = int((ln.at + clips[ln.key].seconds + 0.12) * audio.SR)
        _dip(gain, a, b, duck, ramp)
    for a, b in plan.stops:  # a record scratch stops the music, which comes back on the beat
        _dip(gain, int(a * audio.SR), int(b * audio.SR), 0.0, int(0.012 * audio.SR))
    out = score * gain[:, None]
    for ln in plan.lines:
        if ln.key in clips:
            audio.place(out, ln.at, clips[ln.key].samples, 1.0, 0.0)
    for j, e in enumerate(plan.effects):
        sig, anchor = sfx.make(e.kind, 1000 + j * 7 + sfx.KINDS.index(e.kind), e.length)
        start = max(0.0, e.t - anchor)
        i = int(round(start * audio.SR))
        if i < n:
            k = min(len(sig), n - i)
            out[i:i + k] += sig[:k] * sfx.GAIN[e.kind] * e.gain
    tail = 0.25 if concept.ending.style == "loop" else 1.2
    out = audio.master(out, -14.0, ceiling=0.79)  # -2 dBFS: AAC and inter-sample peaks add about 1 dB
    return audio.fade(out, 0.004, min(tail, seconds / 4))


def _dip(gain: np.ndarray, a: int, b: int, level: float, ramp: int) -> None:
    n = len(gain)
    a, b = max(0, a), min(n, b)
    if b <= a:
        return
    gain[a:b] = np.minimum(gain[a:b], level)
    r0 = max(0, a - ramp)
    if a > r0:
        gain[r0:a] = np.minimum(gain[r0:a], np.linspace(1.0, level, a - r0))
    r1 = min(n, b + ramp)
    if r1 > b:
        gain[b:r1] = np.minimum(gain[b:r1], np.linspace(level, 1.0, r1 - b))


# ---- cover ------------------------------------------------------------------------------------------------------

def _cover(plan: Plan, srcs, cams, concept: ReelConcept, look, cv) -> Image.Image:
    """The hook's hero frame with the hook set large and clean, kept in the middle so a grid crop keeps it."""
    s = plan.shots[0]
    img = camera.render(srcs[s.view], cams[0](0.35))
    y = np.linspace(0, 1, cv.H)
    band = 1 - 0.26 * np.exp(-((y - 0.46) / 0.2) ** 2)
    edge = 1 - 0.22 * np.clip((y - 0.7) / 0.3, 0, 1) ** 2 - 0.15 * np.clip(1 - y / 0.25, 0, 1) ** 2
    img = np.clip(img.astype(np.float32) * (band * edge)[:, None, None], 0, 255).astype(np.uint8)
    hook = concept.hook
    text = clean(hook.text) and hook.text
    if text:
        if hook.style == "sticker":
            ov: Overlay = kinetic.sticker(text, "center", 0.0, 9.0, cv, look)
        elif hook.style == "countdown":
            ov = kinetic.countdown(text, "center", 0.0, 9.0, cv, look, plan.numbers.get(-1))
        else:
            ov = kinetic.bold_center(text, "center", 0.0, 9.0, cv, look, scale=1.22)
        ov.draw(img, 2.0)
    return Image.fromarray(img)
