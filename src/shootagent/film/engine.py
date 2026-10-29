"""Drive Reel Studio's engine (reel-studio-app/engine, MIT) as a subprocess in its own Python 3.11 venv.

The engine is built for talking-head videos: a source video with speech, a transcript with word ids, and a
cut made of word-id ranges. The film fits that shape once its picture track is muxed with the narration:
the narration is the speech, the whole transcript is kept, and the engine adds what it is good at, namely
captions from its own transcript, the music mixed under the voice to -14 LUFS, a cover and a verify
report (sync, loudness, frame count, black frames, caption placement).

CLI contract (reel-studio-app docs/architecture.md section 4): `python -m reelengine <cmd> --project <dir>`;
stdout is JSON lines ending in {"event": "done", "result": ...} or {"event": "error", ...}; exit 1 on error.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..config import Settings

ENGLISH_MODEL = "small.en"  # the engine's default
MULTILINGUAL_MODEL = "small"  # what the engine switches to for any other language


class EngineError(RuntimeError):
    pass


class ReelEngine:
    def __init__(self, python: Path, models: Path | None, root: Path):
        self.python = Path(python)
        self.models = Path(models) if models else None
        self.root = Path(root)

    @classmethod
    def from_settings(cls, settings: Settings, root: Path) -> ReelEngine | None:
        """None when REELENGINE_PYTHON is not set; an error when it is set but wrong."""
        if not settings.reelengine_python:
            return None
        py = Path(settings.reelengine_python)
        if not py.is_file():
            raise EngineError(f"REELENGINE_PYTHON={py} does not exist; point it at the reelengine venv's python.")
        return cls(py, settings.reelengine_models, root)

    def model_for(self, language: str) -> str:
        return ENGLISH_MODEL if language.startswith("en") else MULTILINGUAL_MODEL

    def has_model(self, language: str) -> bool:
        if self.models is None:
            return True  # the engine's own default folder; it reports MODEL_MISSING itself
        d = self.models / self.model_for(language)
        return (d / "model.bin").is_file() and (d / "config.json").is_file()

    def run(self, *args: str, timeout: float = 900) -> dict[str, Any]:
        env = dict(os.environ)
        env.update({"REELENGINE_ROOT": str(self.root), "PYTHONIOENCODING": "utf-8"})
        if self.models:
            env["REELENGINE_MODELS"] = str(self.models)
        for name, tool in (("REELENGINE_FFMPEG", "ffmpeg"), ("REELENGINE_FFPROBE", "ffprobe")):
            found = shutil.which(tool)
            if found and name not in env:
                env[name] = found
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run([str(self.python), "-m", "reelengine", *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise EngineError(f"reelengine {args[0]} did not finish within {timeout:.0f} s") from None
        except OSError as e:
            raise EngineError(f"reelengine {args[0]} could not start: {e}") from None
        done, error = None, None
        for line in proc.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "done":
                done = ev.get("result") or {}
            elif ev.get("event") == "error":
                error = ev
        if error or proc.returncode != 0 or done is None:
            detail = (f"{error.get('code')}: {error.get('message')}" if error else proc.stderr.strip()[-600:])
            raise EngineError(f"reelengine {args[0]} failed: {detail}")
        return done

    def init(self, project: Path, title: str, media: Path) -> dict:
        if project.exists():
            shutil.rmtree(project)  # our own scratch project under film/work; the engine refuses to overwrite
        project.parent.mkdir(parents=True, exist_ok=True)
        return self.run("init", "--project", str(project), "--title", title, str(media))

    def transcribe(self, project: Path, language: str) -> list[dict]:
        self.run("transcribe", "--project", str(project), "--language", language)
        data = json.loads((project / "work" / "transcript.json").read_text(encoding="utf-8"))
        return data["words"]

    def render(self, project: Path) -> dict:
        return self.run("render", "--project", str(project), timeout=1800)

    def frames(self, project: Path, times: list[float]) -> dict:
        return self.run("frames", "--project", str(project), "--times", ",".join(f"{t:.2f}" for t in times))
