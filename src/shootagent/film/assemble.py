"""Assemble one language's film from the spec: picture track timed to the narration, then the finish.

1. Timing. The narration audio is transcribed (Reel Studio's engine, faster-whisper word timestamps) and
   each script segment is located in it (narration.segment_starts). Without the engine, segments are timed
   by word count.
2. Picture. Each shot's clip is fitted to its segment's length: played at 0.67x to 1.6x so a 6 s clip fills
   a 4-9 s segment, then trimmed or held on its last frame. Its overlay (room label; the AI-motion label on
   generated clips only; the end card on the last shot) fades in on top. Shots cut on the narration's
   segment boundaries, and the segments are joined with the narration as the sound track.
3. Finish. Reel Studio's engine captions the film from its own transcript, mixes the generated music bed
   under the voice to -14 LUFS, makes the cover and writes its verify report. Without the engine (or
   without a speech model for the language), ffmpeg mixes the music and normalises loudness, and the
   film has no captions; the manifest says which finisher ran.

The engine keeps the speech from 0.10 s before the first word to 0.35 s after the last (its cut.py), so
the film starts on the hook and ends on the call to action; the end card shows over the last shot.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..reel.music import SR
from .music import TRACKS, prepare_bed
from ..reel.render import _tracked, font
from .beats import beat_times, snap_cuts
from .engine import EngineError, ReelEngine
from .media import CardRequest, MediaError
from .narration import proportional_starts, segment_starts
from .shots import FilmContext
from .spec import END_CARD, FilmSpec
from .video import duration, run_ffmpeg, size

FRAME_HEIGHT = 0.94  # film/depth.py work_image: the 9:16 window spans 94% of a landscape photo's height
RATE_MIN, RATE_MAX = 0.67, 1.6  # playback speed range for fitting a clip to its segment; outside it motion looks wrong
END_CARD_S = 3.0
MAX_HOLD_S = 4.5  # the last shot holds at most this long; the rest of its line plays over the closing card
BEAT_WINDOW_S = 0.36  # half a beat of the 84 BPM bed: every cut can reach a beat, none moves further
CAPTION_POSITION = 0.60  # fraction of height; at 0.66-0.75 the engine's own verify said captions sit under app buttons


def film_dir(fctx: FilmContext) -> Path:
    return fctx.store.shoot_dir(fctx.shoot_id) / "film"


# ---- overlays -----------------------------------------------------------------------------------------------

def overlay_image(w: int, h: int, label: str, disclosure: str | None, end_lines: list[str] | None) -> Image.Image:
    """RGBA overlay for one shot: room label top-left, AI-motion pill top-right, optional end card."""
    text = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(text)
    x, y = int(w * 0.08), int(h * 0.105)
    if label:
        d.line([(x, y - 22), (x + 44, y - 22)], fill=(255, 255, 255, 230), width=3)
        _tracked(d, (x, y), label.upper(), font("Inter", 30, 600), (255, 255, 255, 255), tracking=5)
    if end_lines:
        cy = int(h * 0.40)
        for i, line in enumerate(end_lines):
            if i == 0:
                _tracked(d, (w // 2, cy), line, font("Inter", 34, 650), (255, 255, 255, 255), 6, anchor_center=True)
                cy += 70
            else:
                d.text((w // 2, cy), line, font=font("Inter", 34, 450), fill=(255, 255, 255, 230), anchor="ma")
                cy += 52
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow.putalpha(text.split()[3].filter(ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.45)))
    out = Image.alpha_composite(_scrim(w, h, top=bool(label), band_y=0.43 if end_lines else None), shadow)
    out = Image.alpha_composite(out, text)
    if disclosure:
        f = font("Inter", 24, 550)
        tw = f.getlength(disclosure)
        pad_x, pad_y, th = 16, 9, 24
        x1 = int(w * 0.92)
        x0 = int(x1 - tw - 2 * pad_x)
        y0 = y - 4
        pill = ImageDraw.Draw(out)
        pill.rounded_rectangle([x0, y0, x1, y0 + th + 2 * pad_y], radius=(th + 2 * pad_y) // 2, fill=(0, 0, 0, 140))
        pill.text((x0 + pad_x, y0 + pad_y - 2), disclosure, font=f, fill=(255, 255, 255, 240))
    return out


def _scrim(w: int, h: int, top: bool, band_y: float | None) -> Image.Image:
    """Soft darkening behind white type: a fade from the top edge for the room label, and a horizontal band
    behind the end card. Without it the label vanished against sky and white walls in the first films."""
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)
    alpha = np.zeros(h, np.float32)
    if top:
        alpha = np.maximum(alpha, 0.40 * np.clip(1 - y / 0.22, 0, 1) ** 1.6)
    if band_y is not None:
        alpha = np.maximum(alpha, 0.32 * np.exp(-(((y - band_y) / 0.07) ** 2)))
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 3] = (alpha * 255).astype(np.uint8)[:, None]
    return Image.fromarray(rgba, "RGBA")


CARD_ACCENT = "#D8B98A"  # warm sand, from the photos' wood and linen; Reel Studio's default blue fought the rooms
CARD_TEXT = {
    "en": {"bd": "bd", "ba": "ba", "sqft": "square feet of living space", "sqft_short": "sq ft"},
    "es": {"bd": "rec", "ba": "baños", "sqft": "pies cuadrados de espacio habitable", "sqft_short": "pies²"},
}


def card_params(spec: FilmSpec, language: str) -> dict[str, dict]:
    """Params for the opening price card and the mid-film stat card, from the listing fields only; a card
    whose field is missing is left out."""
    li = spec.listing
    words = CARD_TEXT.get(language.split("-")[0], CARD_TEXT["en"])
    street, _, area = (li.address or "").partition(",")
    area = area.strip()
    out: dict[str, dict] = {}
    if li.price:
        facts = [f"{li.beds:g} {words['bd']}" if li.beds is not None else "",
                 f"{li.baths:g} {words['ba']}" if li.baths is not None else "", area]
        out["title"] = {"template": "price-tag", "params": {
            # The street part names the home ("Sample Home" on the demo's sample listing); the headline is a sentence.
            "value": li.price[:12], "name": (street.strip() if area else li.headline or "")[:30],
            "text": " · ".join(f for f in facts if f)[:40], "theme": "dark", "accentColor": CARD_ACCENT}}
    if li.sqft:
        out["stat"] = {"template": "stat-count-up", "params": {
            "value": f"{li.sqft:,}", "text": words["sqft"], "theme": "dark", "accentColor": CARD_ACCENT}}
    return out


def film_cards(fctx: FilmContext, spec: FilmSpec, language: str, starts: list[float], total: float,
               work: Path) -> tuple[dict[int, tuple[Path, float]], list[str]]:
    """The price card over the first shot (it is the hook) and the stat card over the shot nearest the middle.
    Reel Studio's templates render through fctx.media (cached); without them PIL draws a still card."""
    edges = starts + [total]
    spans = [b - a for a, b in zip(edges, edges[1:])]
    want = card_params(spec, language)
    place = {"title": (0, 0.15)}
    if len(spec.shots) > 2:
        mid = min(range(1, len(spec.shots) - 1), key=lambda i: abs((edges[i] + edges[i + 1]) / 2 - total / 2))
        place["stat"] = (mid, 0.3)
    cards, notes = {}, []
    for name, card in want.items():
        if name not in place:
            continue
        i, t_in = place[name]
        dur = round(max(1.2, min(3.0, spans[i] - t_in - 0.2)), 2)
        req = CardRequest(template=card["template"], params={**card["params"], "duration": dur})
        try:
            cards[i] = (fctx.media.make(req).path, t_in)
        except MediaError as e:
            png = work / f"card-{name}.png"
            _pil_card(spec.width, spec.height, card).save(png)
            cards[i] = (png, t_in)
            notes.append(f"the {name} card was drawn with PIL because Reel Studio's renderer was unavailable ({e})")
    return cards, notes


def _pil_card(w: int, h: int, card: dict) -> Image.Image:
    p = card["params"]
    lines = [(p.get("name", ""), font("Inter", 34, 500)), (p["value"], font("Fraunces", 110, 500)),
             (p.get("text", ""), font("Inter", 38, 500))]
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([90, 230, w - 90, 640], radius=28, fill=(28, 26, 24, 230))
    y = 280
    for text, f in lines:
        if text:
            d.text((w // 2, y), text, font=f, fill=(255, 255, 255, 255) if f.size != 110 else CARD_ACCENT, anchor="ma")
            y += int(f.size * 1.35)
    return img


def closing_card(clip: Path, frames: int, spec: FilmSpec, language: str, work: Path) -> Path:
    """`frames` of the last clip's final frame, blurred and dimmed, slowly pushing in, under the end card with
    the listing facts: the film's last word is about the listing, so the picture is too."""
    still = work / "closing-still.png"
    run_ffmpeg(["-sseof", "-0.2", "-i", str(clip), "-frames:v", "1", "-vf",
                f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=increase,crop={spec.width}:{spec.height}",
                "-update", "1", str(still)], "taking the closing frame")
    card = work / "closing-card.png"
    overlay_image(spec.width, spec.height, "", None, end_card_lines(spec, language, facts=True)).save(card)
    out = work / "closing.mp4"
    secs = frames / spec.fps
    graph = (f"[0:v]scale={int(spec.width * 1.06)}:-2,zoompan=z='1+0.0006*on':d={frames}:s={spec.width}x{spec.height}"
             f":fps={spec.fps},gblur=sigma=18,eq=brightness=-0.12:saturation=0.85,setsar=1[bg];"
             f"[1:v]format=rgba,fade=t=in:st=0.1:d=0.4:alpha=1[o];[bg][o]overlay=0:0:format=auto,format=yuv420p[out]")
    run_ffmpeg(["-loop", "1", "-framerate", str(spec.fps), "-t", f"{secs + 0.5:.2f}", "-i", str(still),
                "-loop", "1", "-framerate", str(spec.fps), "-i", str(card), "-filter_complex", graph, "-map", "[out]",
                "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-r", str(spec.fps), "-pix_fmt", "yuv420p", str(out)], "drawing the closing card")
    return out


def end_card_lines(spec: FilmSpec, language: str, facts: bool = False) -> list[str]:
    lines = [END_CARD.get(language.split("-")[0], END_CARD["en"])]
    li = spec.listing
    words = CARD_TEXT.get(language.split("-")[0], CARD_TEXT["en"])
    fact_line = " · ".join(x for x in (f"{li.beds:g} {words['bd']}" if li.beds is not None else "",
                                       f"{li.baths:g} {words['ba']}" if li.baths is not None else "",
                                       f"{li.sqft:,} {words['sqft_short']}" if li.sqft else "") if x)
    if facts and fact_line:
        lines.append(fact_line)
    if spec.listing.price:
        lines.append(spec.listing.price)
    if spec.listing.address:
        lines.append(spec.listing.address)
    return lines


# ---- picture ------------------------------------------------------------------------------------------------

def frame_counts(starts: list[float], total: float, fps: int) -> list[int]:
    """Frames per shot so that the shots tile [0, total] exactly on the frame grid."""
    edges = [round(s * fps) for s in starts] + [round(total * fps)]
    return [max(1, b - a) for a, b in zip(edges, edges[1:])]


def fit_segment(clip: Path, frames: int, fps: int, w: int, h: int, overlays: list[tuple[Path, float]], out: Path) -> None:
    """Fit `clip` to `frames` and lay each (layer, start second) on top: a PNG fades in; a .mov is a
    transparent full-frame layer (Reel Studio card) that plays from its start and then lets the shot through."""
    clip_s = duration(clip)
    span = frames / fps
    rate = min(RATE_MAX, max(RATE_MIN, clip_s / span))
    cw, ch = size(clip)
    if cw / ch > w / h + 0.02:
        # A landscape clip (Grok returns the photo's 3:2 for single-frame requests even when 9:16 is asked
        # for) gets the window the depth move of the same photo uses: centred, FRAME_HEIGHT of the height.
        frame = (f"crop=w='trunc(ih*{FRAME_HEIGHT}*{w}/{h}/2)*2':h='trunc(ih*{FRAME_HEIGHT}/2)*2',"
                 f"scale={w}:{h}:flags=lanczos")
    else:
        frame = f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}"
    graph = [f"[0:v:0]setpts=(PTS-STARTPTS)/{rate:.4f},{frame},"
             f"fps={fps},tpad=stop_mode=clone:stop_duration={span + 1:.2f},setsar=1[v0]"]
    inputs = ["-i", str(clip)]
    for k, (layer, t_in) in enumerate(overlays, start=1):
        if layer.suffix == ".mov":
            inputs += ["-i", str(layer)]
            graph.append(f"[{k}:v]setpts=PTS-STARTPTS+{t_in:.3f}/TB[o{k}]")
            graph.append(f"[v{k - 1}][o{k}]overlay=0:0:format=auto:eof_action=pass[v{k}]")
            continue
        inputs += ["-loop", "1", "-framerate", str(fps), "-i", str(layer)]
        graph.append(f"[{k}:v]format=rgba,fade=t=in:st={t_in:.2f}:d=0.4:alpha=1[o{k}]")
        graph.append(f"[v{k - 1}][o{k}]overlay=0:0:format=auto[v{k}]")
    graph.append(f"[v{len(overlays)}]format=yuv420p[out]")
    run_ffmpeg([*inputs, "-filter_complex", ";".join(graph), "-map", "[out]", "-frames:v", str(frames), "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(fps), "-pix_fmt", "yuv420p", str(out)],
               f"fitting {clip.name} to {frames} frames")


def build_picture(fctx: FilmContext, spec: FilmSpec, language: str, starts: list[float], total: float,
                  audio: Path, work: Path, cards: dict[int, tuple[Path, float]] | None = None) -> Path:
    """`cards` maps a shot index to a (layer, start second) card shown on that shot; its room label is left
    out, because both sit in the top band of the frame."""
    narration = spec.narrations[language]
    counts = frame_counts(starts, total, spec.fps)
    cards = cards or {}
    parts: list[Path] = []
    for i, (shot, n) in enumerate(zip(spec.shots, counts)):
        if shot.result is None:
            raise ValueError(f"shot {i} has no clip yet; run prepare_shots first")
        generated = shot.result.source == "generated"
        last = i == len(spec.shots) - 1
        clips = [fctx.media.path(fp) for fp in shot.result.clips]
        # A two-photo shot that fell back plays each photo on its own, each under its own room's label.
        labels = [narration.labels[i]]
        if len(clips) > 1:
            end = narration.end_labels[i] if i < len(narration.end_labels) else ""
            labels.append(end or shot.end_label or narration.labels[i])
        closing = 0
        if last and n > MAX_HOLD_S * spec.fps + spec.fps:
            # A long last line (the call to action) plays over a closing card instead of holding the room.
            closing = n - int(MAX_HOLD_S * spec.fps)
            n -= closing
        split = [n // len(clips)] * len(clips)
        split[-1] += n - sum(split)
        for k, (clip, frames) in enumerate(zip(clips, split)):
            part = work / f"shot-{i:02d}-{k}.mp4"
            png = work / f"overlay-{i:02d}-{k}.png"
            card = cards.get(i) if k == 0 else None
            overlay_image(spec.width, spec.height, "" if card else labels[min(k, len(labels) - 1)],
                          spec.disclosure_text(language) if generated else None, None).save(png)
            overlays = [(png, 0.2)] + ([card] if card else [])
            if last and k == len(clips) - 1 and not closing:
                # The end card comes in for the last END_CARD_S of the film, under the spoken call to action.
                end_png = work / "end-card.png"
                overlay_image(spec.width, spec.height, "", None, end_card_lines(spec, language)).save(end_png)
                overlays.append((end_png, max(0.2, frames / spec.fps - END_CARD_S)))
            fit_segment(clip, frames, spec.fps, spec.width, spec.height, overlays, part)
            parts.append(part)
        if closing:
            parts.append(closing_card(clips[-1], closing, spec, language, work))
    listing = work / "parts.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    source = work / "source.mov"
    # Re-encoded, not stream-copied: with a generated clip among the parts, the copied join decoded fine on its
    # own, but Reel Studio's trim then saw its timestamps restart at that part and cut the film 7.5 s short
    # (run 20261028-234917-3ece1f, verify: duration and sound/picture sync failed).
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-r", str(spec.fps), "-pix_fmt", "yuv420p",
                "-c:a", "copy", "-movflags", "+faststart", str(source)], "joining the shots with the narration")
    return source


# ---- finish -------------------------------------------------------------------------------------------------

def _engine_finish(engine: ReelEngine, source: Path, language: str, title: str, cover_at: float, bed: Path,
                   work: Path, timing_project: Path) -> dict[str, Any]:
    project = work / "final"
    engine.init(project, title, source)
    # The timing project's source carries the same audio stream, copied bit for bit, so its transcript (word
    # times and ids) is this source's transcript too; reusing it saves a second transcription.
    shutil.copyfile(timing_project / "work" / "transcript.json", project / "work" / "transcript.json")
    words = json.loads((project / "work" / "transcript.json").read_text(encoding="utf-8"))["words"]
    if not words:
        raise EngineError("the engine heard no words in the narration")
    timing_transcript = json.loads((timing_project / "project.json").read_text(encoding="utf-8"))["transcript"]
    assets = project / "work" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bed, assets / "bed.wav")
    pj = project / "project.json"
    proj = json.loads(pj.read_text(encoding="utf-8"))
    # Keep every word, and every pause between them: the picture is cut to the narration's own timing, so
    # trimming a pause would cut a shot short. 5 s is the schema's maximum and longer than a narration's pauses.
    proj["cut"] = {"keep": [{"from": words[0]["id"], "to": words[-1]["id"]}], "gapCap": 5.0, "minSaving": 0.08}
    proj["transcript"] = timing_transcript
    proj["captions"]["enabled"] = True
    proj["captions"]["style"]["position"] = CAPTION_POSITION
    proj["music"].update({"enabled": True, "track": "work/assets/bed.wav", "startAt": 0.0, "level": "under-voice",
                          "fadeOut": 1.0})
    proj["cover"] = {"atSourceTime": round(cover_at, 3), "source": "s1", "text": None}
    pj.write_text(json.dumps(proj, indent=2, ensure_ascii=False), encoding="utf-8")
    result = engine.render(project)
    segs = result.get("segmentsDetail") or []
    return {"file": Path(result["file"]), "cover": Path(result["cover"]) if result.get("cover") else None,
            "verify": json.loads(Path(result["file"]).with_name("verify.json").read_text(encoding="utf-8"))
            if Path(result["file"]).with_name("verify.json").is_file() else result.get("verify"),
            "head": float(segs[0]["src_start"]) if segs else 0.0, "duration": float(result.get("duration") or 0),
            "captions": int(result.get("captions") or 0), "timings": result.get("timings")}


def _ffmpeg_finish(source: Path, bed: Path, cover_at: float, work: Path) -> dict[str, Any]:
    out = work / "film-ffmpeg.mp4"
    graph = ("[0:a]asplit=2[v1][v2];[1:a]volume=0.3[m];"
             "[m][v1]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck];"
             "[v2][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    run_ffmpeg(["-i", str(source), "-i", str(bed), "-filter_complex", graph, "-map", "0:v:0", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)],
               "mixing the music under the narration")
    cover = work / "cover.png"
    run_ffmpeg(["-ss", f"{cover_at:.2f}", "-i", str(out), "-frames:v", "1", str(cover)], "taking the cover frame")
    return {"file": out, "cover": cover, "verify": None, "head": 0.0, "duration": duration(out), "captions": 0,
            "timings": None}


def assemble_language(fctx: FilmContext, spec: FilmSpec, language: str) -> dict[str, Any]:
    """Render the film for one language. Returns the variant record that goes into the manifest."""
    started = time.perf_counter()
    narration = spec.narrations[language]
    if not narration.audio:
        raise ValueError(f"the {language} narration has no audio yet; call narrate first")
    audio = fctx.media.path(narration.audio)
    total = duration(audio)
    root = film_dir(fctx)
    work = root / "work" / language
    work.mkdir(parents=True, exist_ok=True)
    engine = ReelEngine.from_settings(fctx.settings, root / "work" / "reelstudio")
    notes: list[str] = []
    captions_ok = engine is not None and engine.has_model(language)
    if engine is None:
        notes.append("REELENGINE_PYTHON is not set, so ffmpeg finished the film without captions")
    elif not captions_ok:
        notes.append(f"no {engine.model_for(language)!r} speech model in {engine.models}, so ffmpeg finished the "
                     f"{language} film without captions")

    voice = work / "voice.wav"
    # The Spanish take came back with a +2.1 dBFS true peak: level every narration (-19 LUFS, the English take's
    # own level) with a -2 dBTP ceiling before it is mixed. The voice stays PCM through to the engine (a .mov
    # source): from an AAC source the engine's cut voice came out 40-60 ms shorter than the picture, which
    # failed its sound/picture sync check on the Spanish film. 0.6 s of padding keeps the cut off the file's end.
    run_ffmpeg(["-i", str(audio), "-vn", "-af", "loudnorm=I=-19:TP=-2:LRA=11,apad=pad_dur=0.6", "-c:a", "pcm_s16le",
                "-ar", "48000", str(voice)], "encoding the voice")
    total = duration(voice)  # the picture is cut to the voice actually muxed
    timing_s = time.perf_counter()
    if captions_ok:
        black = work / "timing.mov"
        run_ffmpeg(["-f", "lavfi", "-i", f"color=black:s=320x568:r={spec.fps}", "-i", str(voice), "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-c:a", "copy",
                    str(black)], "making the timing video")
        try:
            engine.init(work / "timing", f"timing {language}", black)
            words = engine.transcribe(work / "timing", language)
        except EngineError as e:
            notes.append(f"Reel Studio's engine could not transcribe the narration ({e}), so segments are timed by "
                         "word count and ffmpeg finished the film without captions")
            captions_ok = False
    if captions_ok:
        starts = segment_starts(narration.segments, words, total)
        timing_method = "transcript"
    else:
        starts = proportional_starts(narration.segments, total)
        timing_method = "word count"
    timing_s = time.perf_counter() - timing_s

    # The engine keeps speech from 0.10 s before the first word (its cut.py), and the music starts there, so a
    # beat at music time b falls at source time b + head.
    head = round(max(0.0, words[0]["s"] - 0.10) * spec.fps) / spec.fps if captions_ok and words else 0.0
    bed, bed_samples, music_track, music_notes = prepare_bed(spec.music, total, work / "bed.wav")
    notes += music_notes
    beats = beat_times(bed_samples, SR)
    narration_starts = starts
    starts = snap_cuts(starts, [b + head for b in beats], window=BEAT_WINDOW_S)

    pic_s = time.perf_counter()
    cards, card_notes = film_cards(fctx, spec, language, starts, total, work)
    notes += card_notes
    source = build_picture(fctx, spec, language, starts, total, voice, work, cards=cards)
    pic_s = time.perf_counter() - pic_s

    cover_at = (starts[0] + (starts[1] if len(starts) > 1 else total)) / 2
    fin_s = time.perf_counter()
    finisher = "reelengine"
    if captions_ok:
        try:
            fin = _engine_finish(engine, source, language, f"film {fctx.shoot_id} {language}", max(cover_at, 0.5),
                                 bed, work, work / "timing")
        except EngineError as e:
            notes.append(f"Reel Studio's engine failed ({e}), so ffmpeg finished the film without captions")
            finisher = "ffmpeg"
            fin = _ffmpeg_finish(source, bed, cover_at, work)
    else:
        finisher = "ffmpeg"
        fin = _ffmpeg_finish(source, bed, cover_at, work)
    fin_s = time.perf_counter() - fin_s

    out_dir = root / language
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fin["file"], out_dir / "film.mp4")
    if fin["cover"] and Path(fin["cover"]).is_file():
        shutil.copyfile(fin["cover"], out_dir / "cover.png")
    if fin["verify"] is not None:
        (out_dir / "verify.json").write_text(json.dumps(fin["verify"], indent=1), encoding="utf-8")
    head, film_len = fin["head"], fin["duration"] or total
    edges = starts + [total]
    shot_times = [[round(max(0.0, a - head), 3), round(min(film_len, max(0.0, b - head)), 3)]
                  for a, b in zip(edges, edges[1:])]
    verify = fin["verify"] or {}
    return {
        "language": language,
        "film": f"{language}/film.mp4",
        "cover": f"{language}/cover.png" if (out_dir / "cover.png").is_file() else None,
        "duration_s": round(film_len, 2),
        "narration": {"text": narration.text, "audio": narration.audio, "backend": narration.backend,
                      "audio_s": round(total, 2), "timing": timing_method},
        "finisher": finisher,
        "captions": fin["captions"] > 0,
        "caption_cues": fin["captions"],
        "verify": {"ok": verify.get("ok"), "fails": verify.get("fails"), "warns": verify.get("warns"),
                   "lufs": verify.get("lufs")} if verify else None,
        "shot_times": shot_times,
        "music": {"track": music_track, "title": TRACKS.get(music_track, {}).get("title", "generated bed")},
        "cuts": {"narration_s": [round(s, 3) for s in narration_starts], "on_beat_s": [round(s, 3) for s in starts],
                 "beats_found": len(beats), "head_s": head},
        "cards": {str(k): v[0].name for k, v in cards.items()},
        "notes": notes,
        "timings_s": {"timing": round(timing_s, 1), "picture": round(pic_s, 1), "finish": round(fin_s, 1),
                      "total": round(time.perf_counter() - started, 1)},
    }


# ---- manifest -----------------------------------------------------------------------------------------------

def write_manifest(fctx: FilmContext, spec: FilmSpec, variants: dict[str, dict]) -> dict:
    """film/manifest.json: each shot's source photo(s), motion source and check result, and each language's
    film. Generated clips are listed with every attempt, so a rejected clip stays on record."""
    shoot = fctx.store.load(fctx.shoot_id)
    shots = []
    placed: set[int] = set()
    for i, shot in enumerate(spec.shots):
        res = shot.result
        used = {p["view"]: p for p in (res.photos if res else [])}  # the versions the clips were made from
        photos = []
        for vid in [shot.view] + ([shot.end_view] if shot.end_view else []):
            v = shoot.view(vid)
            snap = used.get(vid, {"version": v.head, "blob": v.head_version.blob})
            photos.append({"view": vid, "version": snap["version"], "blob": snap["blob"], "room": v.room,
                           "case": v.meta.get("case_id")})
        attempts = [a.model_dump(exclude_none=True) for a in (res.attempts if res else [])]
        # Clips rejected under an earlier plan of this shot's photo stay on record for the disclosure page.
        seen = {(a.get("fingerprint"), a.get("prompt")) for a in attempts}
        for r in spec.rejected:
            if id(r) not in placed and shot.view in (r.view, r.end_view) and (r.attempt.fingerprint, r.attempt.prompt) not in seen:
                seen.add((r.attempt.fingerprint, r.attempt.prompt))
                placed.add(id(r))
                attempts.append({**r.attempt.model_dump(exclude_none=True), "earlier_plan": True,
                                 "planned": {"view": r.view, "end_view": r.end_view, "label": r.label}})
        last = res.attempts[-1] if res and res.attempts else None
        shots.append({
            "index": i,
            "labels": {lang: n.labels[i] for lang, n in spec.narrations.items() if i < len(n.labels)},
            "end_labels": {lang: n.end_labels[i] for lang, n in spec.narrations.items()
                           if i < len(n.end_labels) and n.end_labels[i]},
            "source_photos": photos,
            "planned_motion": shot.motion,
            "motion_source": res.source if res else None,
            "fell_back": res.fell_back if res else False,
            "ai_label": bool(res and res.source == "generated"),
            "clips": res.clips if res else [],
            "check": {"passed": last.passed, "reasons": last.reasons, "judge": last.judge} if last else None,
            "attempts": attempts,
            "depth_checks": [a.model_dump(exclude_none=True) for a in (res.depth_checks if res else [])],
            "note": res.note if res else "",
            "times": {lang: v["shot_times"][i] for lang, v in variants.items() if i < len(v["shot_times"])},
        })
    manifest = {
        "shoot": fctx.shoot_id,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "media_mode": fctx.settings.film_media,
        "disclosure": {"law": "California AB 723", "label": spec.disclosure,
                       "rule": "shown on screen while a generated clip plays; depth moves and stills carry none"},
        "music": spec.music,
        "shots": shots,
        # Rejected clips whose photos the final plan no longer uses.
        "rejected_elsewhere": [r.model_dump(exclude_none=True) for r in spec.rejected if id(r) not in placed],
        "languages": variants,
    }
    (film_dir(fctx) / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest
