"""Generation handoff: export on the PC, run on the Mac, import back into replay."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from fakes import make_shoot, settings  # noqa: E402

from shootagent.film import handoff, handoff_runner  # noqa: E402
from shootagent.film.media import ClipRequest, Media, Photo  # noqa: E402
from shootagent.film.spec import Shot  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

SHOTS = [Shot(view="v01", motion="generated", camera="Slow push toward the table", aspect="9:16", label="Dining"),
         Shot(view="v02", motion="depth", label="Bath")]


def _mac_makes(folder: Path, entry: dict, tamper: str | None = None) -> None:
    """What run_grok.py leaves behind for one request: the clip and a verified results line."""
    clip = folder / entry["clip"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x112:r=24:d=1",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    sent = {k: (str((folder / v).resolve()) if k in handoff.IMAGE_KEYS else v) for k, v in entry["args"].items()}
    if tamper == "args":
        sent["prompt"] += " Make it cinematic."
    line = {"id": entry["id"], "fingerprint": entry["fingerprint"], "status": "ok", "sent": sent,
            "sha256": "0" * 64 if tamper == "sha" else handoff_runner.sha256(clip), "session": "s", "seconds": 50}
    with (folder / "results.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def test_exported_clips_come_back_into_replay_and_serve_their_own_request(tmp_path):
    """Fails if a clip made on the Mac is not served for the request the PC will make, or if a clip whose
    arguments or bytes differ from what was recorded is accepted."""
    store, sid = make_shoot(tmp_path / "shoots")
    folder = tmp_path / "handoff"
    entries = handoff.export_requests(store, sid, SHOTS, folder)
    assert [e["id"] for e in entries] == ["S00-A1", "S00-A2"]  # generated shots only, two attempts each
    assert (folder / "run_grok.py").is_file() and len(list((folder / "inputs").glob("*.jpg"))) == 1

    _mac_makes(folder, entries[0])
    _mac_makes(folder, entries[1], tamper="args")
    s = settings(tmp_path, film_media="replay")
    result = handoff.import_media(s, folder)
    assert [x["id"] for x in result["imported"]] == ["S00-A1"]
    assert result["skipped"] == [("S00-A2", "the arguments sent differ from the request")]

    photo = Photo.from_file(store.blob_path(sid, store.load(sid).view("v01").head_version.blob))
    served = Media(s).make(ClipRequest(**entries[0]["request"]), {photo.sha: photo})
    assert served.backend == "replay" and served.path.read_bytes() == (folder / entries[0]["clip"]).read_bytes()


def test_runner_refuses_a_clip_from_arguments_grok_rewrote(tmp_path, monkeypatch):
    """Fails if the standalone runner keeps a clip grok made from arguments its own model changed."""
    sessions = tmp_path / "sessions"
    sdir = sessions / "p" / "sess"
    (sdir / "videos").mkdir(parents=True)
    (sdir / "videos" / "1.mp4").write_bytes(b"v")
    args = {"image": "/x/a.jpg", "prompt": "Slow push", "duration": 6, "resolution_name": "720p"}
    upd = {"params": {"update": {"sessionUpdate": "tool_call", "title": "image_to_video",
                                 "rawInput": {**args, "prompt": "Slow cinematic push"}}}}
    (sdir / "updates.jsonl").write_text(json.dumps(upd))
    monkeypatch.setattr(handoff_runner, "SESSIONS", sessions)
    monkeypatch.setattr(handoff_runner, "HERE", tmp_path)
    out = json.dumps({"sessionId": "sess", "text": "done"})
    monkeypatch.setattr(handoff_runner.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    with pytest.raises(RuntimeError, match="^altered"):
        handoff_runner.call_grok("grok", "video", args)


def test_narration_comes_back_into_replay_and_the_key_never_leaves_the_process(tmp_path, monkeypatch, capsys):
    """Fails if the runner prints or writes the Gemini key (including inside an API error), or if the narration
    it made is not served for the PC's own speech request."""
    import base64
    import io
    import urllib.error
    import wave

    from shootagent.film.media import SpeechRequest

    store, sid = make_shoot(tmp_path / "shoots")
    folder = tmp_path / "handoff"
    speech = SpeechRequest(model="gemini-3.8-flash-tts", voice="Sulafat", style="warm", language="en", text="Welcome home.")
    handoff.export_requests(store, sid, SHOTS, folder, speech=[speech])

    key = "SECRET-KEY-123"
    pcm = (np.sin(np.arange(24000) / 10) * 8000).astype("<i2").tobytes()
    reply = json.dumps({"steps": [{"type": "model_output", "content": [{"type": "audio", "data": base64.b64encode(pcm).decode()}]}]})

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(handoff_runner, "HERE", folder)
    monkeypatch.setattr(handoff_runner.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, key + "\n", ""))
    monkeypatch.setattr(handoff_runner.urllib.request, "urlopen", lambda req, timeout: Resp(reply.encode()))
    monkeypatch.setattr(sys, "argv", ["run_grok.py", "--tts-only"])
    assert handoff_runner.main() == 0

    def leaky(req, timeout):
        raise urllib.error.HTTPError(handoff_runner.TTS_URL, 400, "bad", {}, io.BytesIO(f"bad key {key}".encode()))

    monkeypatch.setattr(handoff_runner.urllib.request, "urlopen", leaky)
    with pytest.raises(RuntimeError) as err:
        handoff_runner.speak({"x": 1}, key, folder / "audio" / "x.wav")
    out = capsys.readouterr()
    assert key not in out.out + out.err + str(err.value) + (folder / "results.jsonl").read_text(encoding="utf-8")

    monkeypatch.undo()  # import probes the audio with ffprobe through the real subprocess.run
    with wave.open(str(next((folder / "audio").glob("*.wav"))), "rb") as w:
        assert w.getframerate() == 24000 and w.getnframes() == 24000
    s = settings(tmp_path, film_media="replay")
    result = handoff.import_media(s, folder)
    assert [x["id"] for x in result["imported"]] == ["TTS-en"]
    assert Media(s).make(speech).backend == "replay"
