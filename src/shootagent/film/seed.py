"""Seed the replay backend from the media experiment on branch explore/media-tests.

That experiment made four Grok Imagine clips (T1-T4) and two Gemini narrations (E1 English, E2 Spanish)
from the finished photos of AutoHDR property property_c3ec3b9cd3bbe0b378 (defect cases 0054-0060). This
module copies them out of git into the replay folder and indexes each under the fingerprint the live
pipeline would compute for the same request, so a film run on a machine without Grok or Gemini is served
the recorded media instead.

Each clip's request is rebuilt from its call in lab/shots/log.jsonl: the agent's camera direction (CAMERA
below) wrapped in media.clip_prompt's fixed wording must reproduce the logged prompt exactly, or seeding
stops. Photos enter by pixel hash, read from the dataset's edited.jpg, which is what the lab copied into
lab/inputs/ (git-ignored there, so the copy itself cannot be compared).

The lab's TTS style prompt was not recorded, so the speech entries are indexed under the pipeline's own
style (narration.STYLE) with the lab's voice and text.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import Settings
from .media import Photo, SpeechRequest, clip_request, fingerprint
from .narration import STYLE

BRANCH = "origin/explore/media-tests"
REPO = Path(__file__).resolve().parents[3]

# Camera directions as the agent would write them; media.clip_prompt adds the fixed wording.
CLIPS = [
    {"id": "T1", "file": "lab/shots/T1/ref-8b56910d4c9a.mp4", "aspect": "16:9", "first": "0054", "last": "0055",
     "camera": "The camera glides from the primary bedroom toward the open doorway on the right and through it into the primary bathroom."},
    {"id": "T2", "file": "lab/shots/T2/ref-1dc6c6948ff1.mp4", "aspect": "9:16", "first": "0057", "last": "0058",
     "camera": "In the dining room, the camera turns gently from the windows toward the corner under the chandelier."},
    {"id": "T3", "file": "lab/shots/T3/video-fd8792a87044.mp4", "aspect": "source", "first": "0058", "last": None,
     "camera": "Slow, steady orbit to the left around the dining table, real-estate walkthrough"},
    {"id": "T4", "file": "lab/shots/T4/video-2a84f10fca79.mp4", "aspect": "source", "first": "0060", "last": None,
     "camera": "Slow steady dolly forward toward the round table"},
]
SPEECH = [
    {"id": "E1", "file": "lab/audio/E1-en-Sulafat-be7b613035.m4a", "text": "lab/audio/text/E1-en.txt", "language": "en"},
    {"id": "E2", "file": "lab/audio/E2-es-Sulafat-6c8f5855ce.m4a", "text": "lab/audio/text/E2-es.txt", "language": "es"},
]


def _git_show(ref_path: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(REPO), "show", ref_path], capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"git show {ref_path} failed: {proc.stderr.decode(errors='replace').strip()}. "
                           "Run `git fetch origin explore/media-tests` first.")
    return proc.stdout


def seed_replay(settings: Settings, branch: str = BRANCH) -> list[dict]:
    """Write <replay_dir>/index.json and the media files; returns the index entries."""
    if not settings.autohdr_defect_dir:
        raise RuntimeError("Set AUTOHDR_DEFECT_DIR: the replay index keys clips by the pixels of the dataset photos.")
    root = settings.replay_dir
    (root / "clips").mkdir(parents=True, exist_ok=True)
    (root / "speech").mkdir(parents=True, exist_ok=True)
    logged = {e["id"]: e for e in map(json.loads, _git_show(f"{branch}:lab/shots/log.jsonl").decode().splitlines())}

    def photo(case: str) -> Photo:
        path = Path(settings.autohdr_defect_dir) / f"defect_train_{case}" / "edited.jpg"
        if not path.is_file():
            raise RuntimeError(f"{path} is missing; the replay seed needs defect cases 0054-0060")
        return Photo.from_file(path)

    entries = []
    for c in CLIPS:
        req = clip_request(c["camera"], c["aspect"], photo(c["first"]), photo(c["last"]) if c["last"] else None,
                           guard="lab-v1")
        log = logged[c["id"]]
        if req.prompt != log["args"]["prompt"] or req.mode != log["kind"]:
            raise RuntimeError(f"{c['id']}: the rebuilt request does not match the logged call "
                               f"({req.mode!r} {req.prompt!r} vs {log['kind']!r} {log['args']['prompt']!r})")
        dest = root / "clips" / f"{c['id']}.mp4"
        dest.write_bytes(_git_show(f"{branch}:{c['file']}"))
        entries.append({"fingerprint": fingerprint(req), "file": f"clips/{dest.name}", "request": req.model_dump(),
                        "source": f"{branch}:{c['file']} (lab call {c['id']}, {log['seconds']} s on the Mac)"})
    for s in SPEECH:
        text = _git_show(f"{branch}:{s['text']}").decode("utf-8").strip()
        req = SpeechRequest(model=settings.tts_model, voice=settings.tts_voice, style=STYLE, language=s["language"],
                            text=text)
        dest = root / "speech" / f"{s['id']}.m4a"
        dest.write_bytes(_git_show(f"{branch}:{s['file']}"))
        entries.append({"fingerprint": fingerprint(req), "file": f"speech/{dest.name}", "request": req.model_dump(),
                        "source": f"{branch}:{s['file']} (lab take {s['id']}; recorded with the lab's own style prompt)"})
    (root / "index.json").write_text(json.dumps({"entries": entries}, indent=1, ensure_ascii=False), encoding="utf-8")
    return entries
