#!/usr/bin/env python3
"""Make the clips and narrations in requests.json, one call at a time, and verify every call.

Copied into each generation handoff folder by `shootagent film-export-requests`. It needs Python 3.9+, a
signed-in `grok` on PATH (or GROK_BIN) for clips, and the Gemini key in the macOS Keychain for narration;
nothing else.

Clips: port of lab/tools/film.py (explore/media-tests) and shootagent.film.media.GrokBackend. grok's
headless mode puts a model between us and the Imagine tool, so after each call the session's own event log
must show exactly one call with exactly our arguments; a mismatch is a failure, never a clip.
Narration: port of lab/tools/tts.py. The exported request body is posted as is. The key is read in-process
from the Keychain (`security find-generic-password -a "$USER" -s gemini-api-key-spurs-film -w`) and is never
printed, logged or written; error text from the API is scrubbed of it before it is shown.

    python3 run_grok.py              # everything missing: narrations, then first clip attempts, then second ones
    python3 run_grok.py --first-only # narrations and first clip attempts only (half the clip allowance)
    python3 run_grok.py --tts-only   # narrations only
    python3 run_grok.py --only S01-A1 TTS-en
    python3 run_grok.py --dry-run    # print what would be sent; reads no key and makes no call

Writes clips/<fingerprint>.mp4 and audio/<fingerprint>.wav and appends one line per call to results.jsonl.
Rerunning skips files already on disk. Each 6 s clip took 41-65 s and about 2% of the SuperGrok weekly
allowance in the lab.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = {"video": "image_to_video", "ref": "reference_to_video"}
IMAGE_KEYS = ("image", "first_frame", "last_frame")
MIN_GAP_S = 20  # lab/tools/film.py: no evidence for how fast the allowance tolerates calls; bursts buy nothing
TIMEOUT_S = 900
SESSIONS = Path.home() / ".grok" / "sessions"
TTS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
KEYCHAIN_SERVICE = "gemini-api-key-spurs-film"


def body_sha(body: dict) -> str:
    """Same as shootagent.film.handoff.body_sha: what import checks the posted body against."""
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def gemini_key() -> str | None:
    try:
        proc = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER") or getpass.getuser(),
                               "-s", KEYCHAIN_SERVICE, "-w"], capture_output=True, text=True)
    except OSError:
        return None
    key = proc.stdout.strip()
    return key if proc.returncode == 0 and key else None


def speak(body: dict, key: str, dest: Path) -> dict:
    """Post one TTS body; write the audio to dest as WAV. Returns {seconds}."""
    req = urllib.request.Request(TTS_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="ignore")[:600].replace(key, "***")
        raise RuntimeError(f"{'allowance' if e.code == 429 else 'unknown'}: HTTP {e.code}: {text}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"unknown: {str(e.reason).replace(key, '***')}") from None
    audio = next((base64.b64decode(c["data"]) for step in data.get("steps", []) if step.get("type") == "model_output"
                  for c in step.get("content", []) if c.get("type") == "audio" and c.get("data")), None)
    if audio is None:
        raise RuntimeError("unknown: no audio in the response")
    dest.parent.mkdir(exist_ok=True)
    tmp = dest.with_suffix(".part")
    if audio[:4] == b"RIFF":
        tmp.write_bytes(audio)
    else:  # raw 16-bit PCM, 24 kHz mono (lab/tools/tts.py)
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(audio)
    os.replace(tmp, dest)
    return {"seconds": round(time.time() - started, 1)}


def classify(text: str) -> str:
    t = text.lower()
    if "zero data retention" in t or "zdr" in t:
        return "zdr"  # video is blocked while Grok privacy mode is on: run `grok`, then /privacy, and opt in
    if any(k in t for k in ("moderat", "content policy", "not allowed", "violat", "unsafe")):
        return "moderation"
    if any(k in t for k in ("rate limit", "usage limit", "quota", "allowance", "too many requests")):
        return "allowance"
    return "unknown"


def absolute_args(args: dict) -> dict:
    out = dict(args)
    for k in IMAGE_KEYS:
        if k in out:
            p = (HERE / out[k]).resolve()
            if not p.is_file():
                raise SystemExit(f"missing input image {p}")
            out[k] = str(p)
    return out


def tool_calls(session: Path, tool: str) -> list[dict]:
    calls = []
    with (session / "updates.jsonl").open(encoding="utf-8") as f:
        for line in f:
            upd = json.loads(line).get("params", {}).get("update", {})
            if upd.get("sessionUpdate") == "tool_call" and upd.get("title") == tool:
                calls.append(upd.get("rawInput", {}))
    return calls


def call_grok(grok: str, mode: str, args: dict) -> tuple[Path, dict]:
    tool = TOOLS[mode]
    prompt = (f"Call {tool} exactly once with exactly these JSON arguments and nothing else:\n"
              f"{json.dumps(args, ensure_ascii=False)}\n"
              "Do not change, shorten, or improve any argument. Reply with only the returned absolute path.")
    runs = HERE / ".grok-runs"
    runs.mkdir(exist_ok=True)
    started = time.time()
    proc = subprocess.run([grok, "-p", prompt, "--tools", tool, "--max-turns", "3", "--always-approve",
                           "--output-format", "json", "--disable-web-search", "--no-subagents"],
                          cwd=runs, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT_S)
    meta = {"seconds": round(time.time() - started, 1)}
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"unknown: grok exited {proc.returncode} without JSON: {proc.stderr.strip()[-400:]}") from None
    text = result.get("text", "") or ""
    meta.update(session=result.get("sessionId"), agent_cost_usd=result.get("total_cost_usd"))
    matches = list(SESSIONS.glob(f"*/{meta['session']}")) if meta["session"] else []
    if len(matches) != 1:
        raise RuntimeError(f"unknown: expected one session folder for {meta['session']}, found {len(matches)}")
    session = matches[0]
    calls = tool_calls(session, tool)
    if not calls:
        raise RuntimeError(f"{classify(text)}: grok did not call {tool}: {text[-300:]}")
    if len(calls) != 1:
        raise RuntimeError(f"altered: grok called {tool} {len(calls)} times")
    if calls[0] != args:
        raise RuntimeError(f"altered: grok changed the arguments: used {json.dumps(calls[0])}")
    videos = sorted((session / "videos").glob("*")) if (session / "videos").is_dir() else []
    if len(videos) != 1:
        errors = " ".join(ln for ln in (session / "updates.jsonl").read_text(errors="ignore").splitlines() if '"error"' in ln)
        raise RuntimeError(f"{classify(text + errors)}: expected one video in {session / 'videos'}, found {len(videos)}")
    return videos[0], meta


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--first-only", action="store_true")
    ap.add_argument("--tts-only", action="store_true")
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    reqs = json.loads((HERE / "requests.json").read_text(encoding="utf-8"))["requests"]
    tts = [r for r in reqs if r.get("kind") == "speech" and not (HERE / r["audio"]).is_file()]
    clips = sorted((r for r in reqs if r.get("kind", "clip") == "clip"), key=lambda r: (r["attempt"], r["shot"]))
    clips = [r for r in clips if not (HERE / r["clip"]).is_file()]
    if a.first_only:
        clips = [r for r in clips if r["attempt"] == 1]
    if a.tts_only:
        clips = []
    if a.only:
        tts = [r for r in tts if r["id"] in a.only]
        clips = [r for r in clips if r["id"] in a.only]
    print(f"{len(tts)} narration(s) and {len(clips)} clip(s) to make", flush=True)
    if tts:
        if a.dry_run:
            for r in tts:
                print(r["id"], "TTS", r["request"]["voice"], json.dumps(r["request"]["text"]))
        else:
            key = gemini_key()
            if key is None:
                print(f"Gemini key not found in the Keychain (service {KEYCHAIN_SERVICE}); narration skipped", flush=True)
            for r in tts if key else []:
                entry = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": r["id"], "fingerprint": r["fingerprint"],
                         "kind": "speech", "body_sha256": body_sha(r["body"])}
                try:
                    meta = speak(r["body"], key, HERE / r["audio"])
                except RuntimeError as e:
                    entry.update(status="failed", error=str(e))
                    print(f"{r['id']}: FAILED {e}", flush=True)
                else:
                    entry.update(status="ok", sha256=sha256(HERE / r["audio"]), **meta)
                    print(f"{r['id']}: ok in {meta['seconds']} s -> {r['audio']}", flush=True)
                with (HERE / "results.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            del key
    grok = os.environ.get("GROK_BIN") or shutil.which("grok")
    if clips and not grok and not a.dry_run:
        raise SystemExit("grok is not on PATH; install it and sign in, or set GROK_BIN")
    last = 0.0
    for r in clips:
        args = absolute_args(r["args"])
        if a.dry_run:
            print(r["id"], TOOLS[r["mode"]], json.dumps(args))
            continue
        wait = MIN_GAP_S - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        entry = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": r["id"], "fingerprint": r["fingerprint"], "sent": args}
        try:
            video, meta = call_grok(grok, r["mode"], args)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            last = time.time()
            msg = str(e)
            entry.update(status="failed", error=msg)
            with (HERE / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            print(f"{r['id']}: FAILED {msg}", flush=True)
            if msg.startswith("allowance"):
                print("allowance reached; stopping", flush=True)
                return 2
            continue
        last = time.time()
        dest = HERE / r["clip"]
        dest.parent.mkdir(exist_ok=True)
        tmp = dest.with_suffix(".part")
        shutil.copyfile(video, tmp)
        os.replace(tmp, dest)
        entry.update(status="ok", sha256=sha256(dest), **meta)
        with (HERE / "results.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"{r['id']}: ok in {meta['seconds']} s -> {r['clip']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
