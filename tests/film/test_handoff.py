"""Generation handoff: export on the PC, run on the Mac, import back into replay."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

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
