"""Media backends behind one interface, with a fingerprint cache in front of all of them.

A request describes what to make: a generated clip, a depth move, a still move or a spoken narration.
Its fingerprint is a hash of the request with every input photo replaced by a hash of its decoded pixels,
so the same photo gives the same fingerprint on the Mac and the PC whatever its path or file format.
`Media.make` returns the cached file when the fingerprint is already in `data/media/`; otherwise it asks
the backend for that request kind and caches the result. Nothing is generated twice.

Backends:
- grok: the grok CLI's Imagine tools, one call at a time, with the exact arguments verified in grok's
  session log (port of lab/tools/film.py on explore/media-tests). Needs a signed-in `grok` (the Mac).
- replay: serves recorded clips and audio by fingerprint from `film_replay_dir` and fails on anything
  else. The PC, the tests and a no-network demo use it in place of grok and Gemini.
- depth: depth parallax from the photo (film/depth.py), the fallback that invents nothing.
- still: a slow push-in on the photo.
- tts: Gemini 3.8 Flash TTS, one request per narration (port of lab/tools/tts.py).

`settings.film_media` picks grok and tts ("live") or replay ("replay") for clips and speech; depth and
still always render locally.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel

from ..config import Settings
from .spec import Aspect, DepthMove

# ---- requests -----------------------------------------------------------------------------------------------

# Bump a renderer's version when its output for the same request changes, so old cache entries stop matching.
DEPTH_RENDERER = "parallax-v1"
STILL_RENDERER = "push-v1"
CLIP_SECONDS = 6  # Grok Imagine's shortest clip; depth and still moves match it


class ClipRequest(BaseModel):
    kind: Literal["clip"] = "clip"
    mode: Literal["video", "ref"]  # grok image_to_video (one photo, its own aspect) or reference_to_video
    prompt: str
    aspect: Aspect
    duration: int = CLIP_SECONDS
    resolution: str = "720p"
    images: list[str]  # pixel hashes: [first] or [first, last]


class DepthRequest(BaseModel):
    kind: Literal["depth"] = "depth"
    image: str
    move: DepthMove
    parallax_px: float
    seconds: float = CLIP_SECONDS
    renderer: str = DEPTH_RENDERER


class StillRequest(BaseModel):
    kind: Literal["still"] = "still"
    image: str
    seconds: float = CLIP_SECONDS
    renderer: str = STILL_RENDERER


class SpeechRequest(BaseModel):
    kind: Literal["speech"] = "speech"
    model: str
    voice: str
    style: str
    language: str
    text: str


Request = ClipRequest | DepthRequest | StillRequest | SpeechRequest
EXT = {"clip": "mp4", "depth": "mp4", "still": "mp4", "speech": "wav"}


def fingerprint(req: Request) -> str:
    return hashlib.sha256(json.dumps(req.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def pixel_sha(rgb: np.ndarray) -> str:
    """Hash of decoded pixels (and shape): a JPEG and a lossless PNG of the same image hash the same."""
    h = hashlib.sha256(f"{rgb.shape}".encode())
    h.update(np.ascontiguousarray(rgb).tobytes())
    return h.hexdigest()[:16]


def load_rgb(path: Path) -> np.ndarray:
    """RGB uint8 with EXIF orientation applied, the way store.get_image reads a photo."""
    with Image.open(path) as im:
        return np.asarray(ImageOps.exif_transpose(im).convert("RGB"))


@lru_cache(maxsize=256)
def _sha_of_file(path: str, mtime_ns: int, size: int) -> str:
    return pixel_sha(load_rgb(Path(path)))


@dataclass(frozen=True)
class Photo:
    """An input photo: the file a backend may read, and the pixel hash that goes into fingerprints."""

    path: Path
    sha: str

    @classmethod
    def from_file(cls, path: Path) -> Photo:
        st = Path(path).stat()
        return cls(Path(path).resolve(), _sha_of_file(str(Path(path).resolve()), st.st_mtime_ns, st.st_size))

    def rgb(self) -> np.ndarray:
        return load_rgb(self.path)


# ---- prompts ------------------------------------------------------------------------------------------------

# The fixed wording around the agent's camera direction, taken from the four lab calls on explore/media-tests
# (lab/shots/log.jsonl), so those recorded clips are found again by fingerprint. The guard text asks Imagine
# not to change the room; the clip checker is what enforces it.
VIDEO_GUARD = ", keep the room exactly as photographed, no people, no text."
REF_PREFIX = "Smooth steady gimbal shot for a real-estate walkthrough. "
REF_GUARD = (" Keep every wall, window, door, fixture and piece of furniture exactly as in the photos. Do not add or "
             "remove doors, windows or objects. No people, no text or lettering, no camera shake.")


RETRY_MARK = " A previous attempt was rejected because:"


def clip_mode(aspect: Aspect, has_end: bool) -> Literal["video", "ref"]:
    """image_to_video keeps the photo's aspect; a 9:16 or 16:9 frame, or an end photo, needs reference_to_video."""
    return "video" if aspect == "source" and not has_end else "ref"


def clip_prompt(mode: str, camera: str, rejected: str | None = None) -> str:
    camera = camera.strip()
    text = f"{camera}{VIDEO_GUARD}" if mode == "video" else f"{REF_PREFIX}{camera}{REF_GUARD}"
    if rejected:
        text += f"{RETRY_MARK} {rejected[:400]}. Do not repeat that."
    return text


def clip_request(camera: str, aspect: Aspect, first: Photo, last: Photo | None = None,
                 rejected: str | None = None) -> ClipRequest:
    mode = clip_mode(aspect, last is not None)
    return ClipRequest(mode=mode, prompt=clip_prompt(mode, camera, rejected), aspect=aspect,
                       images=[first.sha] + ([last.sha] if last else []))


# ---- errors -------------------------------------------------------------------------------------------------

class MediaError(RuntimeError):
    """A request could not be served. `category` says what to do: not_cached, unavailable, moderation,
    allowance, zdr, altered, unknown."""

    def __init__(self, message: str, category: str = "unknown"):
        super().__init__(message)
        self.category = category


@dataclass
class Produced:
    path: Path
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaResult:
    fingerprint: str
    path: Path
    backend: str
    cached: bool
    seconds: float | None = None  # time to produce, when produced now
    meta: dict[str, Any] = field(default_factory=dict)


# ---- cache --------------------------------------------------------------------------------------------------

class MediaCache:
    """`<root>/<fingerprint>.<ext>` plus `<fingerprint>.json` with the request and how it was made."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def find(self, fp: str) -> Path | None:
        for p in self.root.glob(f"{fp}.*"):
            if p.suffix != ".json" and not p.name.endswith(".part"):
                return p
        return None

    def meta(self, fp: str) -> dict | None:
        p = self.root / f"{fp}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def put(self, fp: str, src: Path, ext: str, meta: dict) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        dest = self.root / f"{fp}.{ext}"
        tmp = self.root / f".{fp}.{uuid.uuid4().hex[:6]}.part"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
        tmp_meta = self.root / f".{fp}.{uuid.uuid4().hex[:6]}.json.part"
        tmp_meta.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_meta, self.root / f"{fp}.json")
        return dest

    def metas(self) -> list[dict]:
        out = []
        for p in sorted(self.root.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out


# ---- backends -----------------------------------------------------------------------------------------------

class ReplayBackend:
    """Recorded media by fingerprint. `<dir>/index.json` is {"entries": [{fingerprint, file, request, source}]}
    with `file` relative to the folder; `shootagent film seed-replay` writes it."""

    name = "replay"

    def __init__(self, root: Path):
        self.root = Path(root)

    def entries(self) -> list[dict]:
        idx = self.root / "index.json"
        if not idx.is_file():
            return []
        return json.loads(idx.read_text(encoding="utf-8")).get("entries", [])

    def produce(self, req: Request, photos: dict[str, Photo], workdir: Path) -> Produced:
        fp = fingerprint(req)
        for e in self.entries():
            if e["fingerprint"] == fp:
                path = self.root / e["file"]
                if not path.is_file():
                    raise MediaError(f"replay index lists {e['file']} but {path} is missing; rerun seed-replay", "not_cached")
                return Produced(path, {"source": e.get("source")})
        raise MediaError(
            f"no recorded {req.kind} for this request in {self.root} (fingerprint {fp}). Replay serves only what was "
            "recorded; set FILM_MEDIA=live on a machine with grok and GEMINI_API_KEY to generate it.",
            "not_cached",
        )


class GrokBackend:
    """Imagine through the grok CLI. Calls run one at a time with a minimum gap: the subscription allowance is
    shared and bursts buy nothing. Grok's headless mode puts a model between us and the tool, so after every
    call the session's event log must show exactly one call with exactly our arguments; anything else is an
    error, because an off-prompt clip that lands in the film is worse than a failed one."""

    name = "grok"
    TOOLS = {"video": "image_to_video", "ref": "reference_to_video"}
    MIN_GAP_S = 20  # lab/tools/film.py: no evidence yet for how fast the allowance tolerates calls
    TIMEOUT_S = 900
    _lock = threading.Lock()
    _last_call = 0.0

    def __init__(self, settings: Settings):
        self.bin = settings.grok_bin
        self.runs_dir = (settings.data_dir / "grok-runs").resolve()
        self.sessions = Path.home() / ".grok" / "sessions"

    def available(self) -> str | None:
        """None when usable, else what is missing."""
        return None if shutil.which(self.bin) else (
            f"`{self.bin}` is not on PATH. Install the grok CLI and sign in, or set FILM_MEDIA=replay.")

    @staticmethod
    def tool_args(req: ClipRequest, photos: dict[str, Photo]) -> dict:
        paths = [str(photos[s].path) for s in req.images]
        if req.mode == "video":
            return {"image": paths[0], "prompt": req.prompt, "duration": req.duration, "resolution_name": req.resolution}
        args: dict[str, Any] = {"prompt": req.prompt, "aspect_ratio": req.aspect, "duration": req.duration,
                                "resolution_name": req.resolution, "first_frame": paths[0]}
        if len(paths) > 1:
            args["last_frame"] = paths[1]
        return args

    def produce(self, req: Request, photos: dict[str, Photo], workdir: Path) -> Produced:
        if not isinstance(req, ClipRequest):
            raise TypeError(f"grok makes clips, not {req.kind}")
        missing = self.available()
        if missing:
            raise MediaError(missing, "unavailable")
        args = self.tool_args(req, photos)
        with GrokBackend._lock:
            wait = self.MIN_GAP_S - (time.time() - GrokBackend._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                return self._call(req.mode, args)
            finally:
                GrokBackend._last_call = time.time()

    def _call(self, mode: str, args: dict) -> Produced:
        tool = self.TOOLS[mode]
        prompt = (f"Call {tool} exactly once with exactly these JSON arguments and nothing else:\n"
                  f"{json.dumps(args, ensure_ascii=False)}\n"
                  "Do not change, shorten, or improve any argument. Reply with only the returned absolute path.")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "-p", prompt, "--tools", tool, "--max-turns", "3", "--always-approve",
               "--output-format", "json", "--disable-web-search", "--no-subagents"]
        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=self.runs_dir, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  timeout=self.TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise MediaError(f"grok did not finish within {self.TIMEOUT_S} s", "unknown") from None
        seconds = round(time.time() - started, 1)
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise MediaError(f"grok exited {proc.returncode} without JSON output: {proc.stderr.strip()[-500:]}") from None
        text = result.get("text", "") or ""
        meta = {"session": result.get("sessionId"), "seconds": seconds, "agent_cost_usd": result.get("total_cost_usd"),
                "tool_args": args}
        session = self._session_dir(result.get("sessionId"))
        calls = self._tool_calls(session, tool)
        if not calls:
            category = classify_failure(text)
            raise MediaError(f"grok did not call {tool} ({category}): {text[-300:]}", category)
        if len(calls) != 1:
            raise MediaError(f"grok called {tool} {len(calls)} times; expected once (session {meta['session']})", "altered")
        if calls[0] != args:
            raise MediaError(f"grok altered the arguments: sent {json.dumps(args)}, used {json.dumps(calls[0])}", "altered")
        files = sorted((session / "videos").glob("*")) if (session / "videos").is_dir() else []
        if len(files) != 1:
            errors = " ".join(ln for ln in (session / "updates.jsonl").read_text(errors="ignore").splitlines()
                              if '"error"' in ln)[-2000:]
            category = classify_failure(text + errors)
            raise MediaError(f"expected one video in {session / 'videos'}, found {len(files)} ({category})", category)
        return Produced(files[0], meta)

    def _session_dir(self, session_id: str | None) -> Path:
        matches = list(self.sessions.glob(f"*/{session_id}")) if session_id else []
        if len(matches) != 1:
            raise MediaError(f"expected one grok session folder for {session_id}, found {len(matches)}")
        return matches[0]

    @staticmethod
    def _tool_calls(session: Path, tool: str) -> list[dict]:
        calls = []
        with (session / "updates.jsonl").open(encoding="utf-8") as f:
            for line in f:
                upd = json.loads(line).get("params", {}).get("update", {})
                if upd.get("sessionUpdate") == "tool_call" and upd.get("title") == tool:
                    calls.append(upd.get("rawInput", {}))
        return calls


def classify_failure(text: str) -> str:
    t = text.lower()
    if "zero data retention" in t or "zdr" in t:
        return "zdr"  # video is blocked while Grok privacy mode is on: run `grok`, then /privacy, and opt in
    if any(k in t for k in ("moderat", "content policy", "not allowed", "violat", "unsafe")):
        return "moderation"  # rewrite the prompt rather than retrying
    if any(k in t for k in ("rate limit", "usage limit", "quota", "allowance", "too many requests")):
        return "allowance"  # check /usage in grok and resume later
    return "unknown"


class GeminiTts:
    """Gemini 3.8 Flash TTS over the Interactions API, one request per whole narration."""

    name = "gemini-tts"
    URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

    def __init__(self, settings: Settings):
        self.key = settings.gemini_api_key

    def produce(self, req: Request, photos: dict[str, Photo], workdir: Path) -> Produced:
        if not isinstance(req, SpeechRequest):
            raise TypeError(f"tts makes speech, not {req.kind}")
        if not self.key:
            raise MediaError("GEMINI_API_KEY is not set. Set it (the Mac fills it from the Keychain) or use FILM_MEDIA=replay.",
                             "unavailable")
        body = {
            "model": req.model,
            "input": [{"type": "user_input", "content": [
                {"type": "text", "text": req.text, "annotations": [{"type": "speech_metadata", "style": req.style}]}]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": req.voice}]},
        }
        http = urllib.request.Request(self.URL, data=json.dumps(body).encode(), method="POST",
                                      headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        started = time.time()
        try:
            with urllib.request.urlopen(http, timeout=300) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise MediaError(f"Gemini TTS returned HTTP {e.code}: {e.read().decode(errors='ignore')[:500]}",
                             "allowance" if e.code == 429 else "unknown") from None
        audio = next((base64.b64decode(c["data"]) for step in data.get("steps", []) if step.get("type") == "model_output"
                      for c in step.get("content", []) if c.get("type") == "audio" and c.get("data")), None)
        if audio is None:
            raise MediaError(f"Gemini TTS returned no audio: {json.dumps(data)[:300]}")
        workdir.mkdir(parents=True, exist_ok=True)
        out = workdir / f"tts-{uuid.uuid4().hex[:8]}.wav"
        if audio[:4] == b"RIFF":
            out.write_bytes(audio)
        else:  # raw 16-bit PCM, 24 kHz mono
            from .video import run_ffmpeg

            raw = out.with_suffix(".pcm")
            raw.write_bytes(audio)
            run_ffmpeg(["-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(raw), str(out)], "wrapping TTS audio")
            raw.unlink()
        return Produced(out, {"seconds": round(time.time() - started, 1)})


class DepthBackend:
    name = "depth"

    def produce(self, req: Request, photos: dict[str, Photo], workdir: Path) -> Produced:
        from .depth import render_depth_clip

        assert isinstance(req, DepthRequest)
        out = workdir / f"depth-{uuid.uuid4().hex[:8]}.mp4"
        stats = render_depth_clip(photos[req.image].rgb(), req.move, req.parallax_px, out, req.seconds)
        return Produced(out, stats)


class StillBackend:
    """A slow eased push-in drawn with the reel renderer's sub-pixel camera (reel/render.py)."""

    name = "still"

    def produce(self, req: Request, photos: dict[str, Photo], workdir: Path) -> Produced:
        from ..reel.render import frame_at, move, prepare_source
        from ..reel.spec import Slide
        from .video import VideoWriter

        assert isinstance(req, StillRequest)
        w, h, fps = 1080, 1920, 30
        src = prepare_source(photos[req.image].rgb(), h)
        cam = move(Slide(view="-", motion="push_in"), src.shape[1], src.shape[0], w, h)
        n = round(req.seconds * fps)
        out = workdir / f"still-{uuid.uuid4().hex[:8]}.mp4"
        with VideoWriter(out, w, h, fps) as vw:
            for k in range(n):
                vw.write(frame_at(src, cam(k / max(1, n - 1)), w, h))
        return Produced(out, {"frames": n})


# ---- the facade ---------------------------------------------------------------------------------------------

class Media:
    # One lock per fingerprint, shared by every Media in the process: two shots asking for the same depth
    # fallback, or two runs for the same narration, wait for one result instead of making it twice.
    _locks: dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, settings: Settings):
        self.settings = settings
        self.mode = settings.film_media
        self.cache = MediaCache(settings.media_cache_dir)
        self.replay = ReplayBackend(settings.replay_dir)
        self.workdir = settings.data_dir / "media-work"
        self._backends: dict[str, Any] = {"depth": DepthBackend(), "still": StillBackend()}
        if self.mode == "live":
            self._backends.update(clip=GrokBackend(settings), speech=GeminiTts(settings))
        else:
            self._backends.update(clip=self.replay, speech=self.replay)

    def backend_name(self, kind: str) -> str:
        return self._backends[kind].name

    def make(self, req: Request, photos: dict[str, Photo] | None = None) -> MediaResult:
        fp = fingerprint(req)
        with Media._locks_guard:
            lock = Media._locks.setdefault(fp, threading.Lock())
        with lock:
            return self._make(req, fp, photos)

    def _make(self, req: Request, fp: str, photos: dict[str, Photo] | None) -> MediaResult:
        hit = self.cache.find(fp)
        if hit is not None:
            meta = self.cache.meta(fp) or {}
            return MediaResult(fp, hit, meta.get("backend", "cache"), cached=True, meta=meta.get("meta", {}))
        backend = self._backends[req.kind]
        started = time.perf_counter()
        produced = backend.produce(req, photos or {}, self.workdir)
        seconds = round(time.perf_counter() - started, 2)
        meta = {"fingerprint": fp, "request": req.model_dump(), "backend": backend.name, "seconds": seconds,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "meta": produced.meta}
        path = self.cache.put(fp, produced.path, produced.path.suffix.lstrip(".") or EXT[req.kind], meta)
        if produced.path.parent == self.workdir:
            produced.path.unlink(missing_ok=True)
        return MediaResult(fp, path, backend.name, cached=False, seconds=seconds, meta=produced.meta)

    def path(self, fp: str) -> Path:
        p = self.cache.find(fp)
        if p is None:
            raise FileNotFoundError(f"media {fp} is not in {self.cache.root}")
        return p

    def known_clips(self, sha: str) -> list[dict]:
        """Clip requests already made (cache) or recorded (replay) whose first frame is this photo."""
        seen, out = set(), []
        for source, metas in (("cache", self.cache.metas()), ("replay", self.replay.entries())):
            for m in metas:
                req = m.get("request") or {}
                if (req.get("kind") == "clip" and req.get("images", [None])[0] == sha and m["fingerprint"] not in seen
                        and RETRY_MARK not in req.get("prompt", "")):
                    seen.add(m["fingerprint"])
                    out.append({"source": source, "fingerprint": m["fingerprint"], **req})
        return out

    def known_speech(self) -> list[dict]:
        seen, out = set(), []
        for source, metas in (("cache", self.cache.metas()), ("replay", self.replay.entries())):
            for m in metas:
                req = m.get("request") or {}
                if req.get("kind") == "speech" and m["fingerprint"] not in seen:
                    seen.add(m["fingerprint"])
                    out.append({"source": source, "fingerprint": m["fingerprint"], **req})
        return out
