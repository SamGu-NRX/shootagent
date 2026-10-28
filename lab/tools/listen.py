#!/usr/bin/env python3
"""Ask Gemini 3.8 Flash (free tier) to listen to audio or watch video and answer a question.

I cannot hear the mix, so this is the stand-in: a multimodal model's report on
pacing, naturalness, levels and timing. Treat its answers as one listener's
notes, not measurements; loudness and peaks come from ffmpeg.

Usage:
  listen.py --question "..." [--models a,b] file1.mp3 [file2.wav ...]
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-omni-1.1-flash", "gemini-3-flash-preview", "gemini-2.5-flash"]  # tried in order on 503/429
URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
MAX_INLINE = 19 * 1024 * 1024


def api_key() -> str:
    user = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    out = subprocess.run(["security", "find-generic-password", "-a", user, "-s", "gemini-api-key-spurs-film", "-w"],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit("listen: Gemini key not found in Keychain (service gemini-api-key-spurs-film)")
    return out.stdout.strip()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--question", required=True)
    p.add_argument("--models", default=",".join(MODELS), help="comma-separated, tried in order")
    p.add_argument("files", nargs="+")
    a = p.parse_args()
    parts = [{"text": a.question}]
    total = 0
    for f in a.files:
        path = Path(f)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        total += len(data) * 4 // 3
        if total > MAX_INLINE:
            sys.exit(f"listen: inline payload over {MAX_INLINE} bytes; compress the files first")
        parts.append({"text": f"File: {path.name}"})
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0.2}}
    key = api_key()
    data = None
    for model in a.models.split(","):
        req = urllib.request.Request(URL.format(model), data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.loads(r.read())
            print(f"[{model}]", file=sys.stderr)
            break
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="ignore")[:400]
            if e.code in (429, 503):
                print(f"listen: {model} busy ({e.code}), trying next", file=sys.stderr)
                continue
            sys.exit(f"listen: HTTP {e.code}: {msg}")
        except (TimeoutError, urllib.error.URLError) as e:
            # an overloaded model can hold the request open past the timeout instead of answering 429
            print(f"listen: {model} did not answer ({e}), trying next", file=sys.stderr)
            continue
    if data is None:
        sys.exit("listen: every model was busy; try again later")
    texts = [pt.get("text", "") for c in data.get("candidates", []) for pt in c.get("content", {}).get("parts", [])]
    print("\n".join(t for t in texts if t).strip() or json.dumps(data)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
