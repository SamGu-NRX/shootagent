#!/usr/bin/env python3
"""Speak a script with Gemini 3.8 Flash TTS on the free API key and save a WAV.

The free tier allows few requests a day (community reports 10 to 100), so a
whole narration goes out as one request and is split afterwards; a finished
output is never requested again (the file name is a hash of the inputs).

Usage:
  tts.py --voice Charon --style "..." --text-file narration.txt --out audio/vo
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODEL = "gemini-3.8-flash-tts"


def api_key() -> str:
    out = subprocess.run(
        ["security", "find-generic-password", "-a", subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip(),
         "-s", "gemini-api-key-spurs-film", "-w"],
        capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit("tts: Gemini key not found in Keychain (service gemini-api-key-spurs-film)")
    return out.stdout.strip()


def speak(text: str, voice: str, style: str) -> bytes:
    body = {
        "model": MODEL,
        "input": [{"type": "user_input", "content": [
            {"type": "text", "text": text, "annotations": [{"type": "speech_metadata", "style": style}]}]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": api_key()})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"tts: HTTP {e.code}: {e.read().decode(errors='ignore')[:800]}")
    for step in data.get("steps", []):
        if step.get("type") == "model_output":
            for c in step.get("content", []):
                if c.get("type") == "audio" and c.get("data"):
                    return base64.b64decode(c["data"])
    sys.exit(f"tts: no audio in response: {json.dumps(data)[:800]}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--voice", required=True)
    p.add_argument("--style", required=True)
    p.add_argument("--text-file", required=True)
    p.add_argument("--out", required=True, help="output directory")
    a = p.parse_args()
    text = Path(a.text_file).read_text().strip()
    fp = hashlib.sha256(json.dumps([MODEL, a.voice, a.style, text]).encode()).hexdigest()[:10]
    out = Path(a.out) / f"{Path(a.text_file).stem}-{a.voice}-{fp}.wav"
    if out.exists():
        print(out)
        return 0
    audio = speak(text, a.voice, a.style)
    out.parent.mkdir(parents=True, exist_ok=True)
    if audio[:4] == b"RIFF":
        out.write_bytes(audio)
    else:  # raw 16-bit PCM, 24 kHz mono
        raw = out.with_suffix(".pcm")
        raw.write_bytes(audio)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(raw), str(out)],
                       check=True)
        raw.unlink()
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
