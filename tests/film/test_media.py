"""Media cache and backends: fingerprints, replay, and grok's argument check."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from shootagent.config import Settings
from shootagent.film import media as m


def _photo_files(tmp_path: Path) -> tuple[Path, Path]:
    rng = np.random.default_rng(3)
    img = Image.fromarray(rng.integers(0, 255, (40, 60, 3), dtype=np.uint8))
    a, b = tmp_path / "a" / "photo.png", tmp_path / "b" / "same-pixels.png"
    for p in (a, b):
        p.parent.mkdir(parents=True)
        img.save(p)
    return a, b


def test_same_pixels_at_another_path_give_the_same_fingerprint(tmp_path):
    """Fails if a recorded clip is not found again on another machine, where the photo has another path or name."""
    a, b = _photo_files(tmp_path)
    pa, pb = m.Photo.from_file(a), m.Photo.from_file(b)
    assert pa.path != pb.path
    assert m.fingerprint(m.clip_request("Slow dolly", "source", pa)) == m.fingerprint(m.clip_request("Slow dolly", "source", pb))
    assert m.fingerprint(m.clip_request("Slow dolly", "source", pa)) != m.fingerprint(m.clip_request("Slow pan", "source", pa))


def _replay_settings(tmp_path: Path, recorded: m.ClipRequest) -> Settings:
    root = tmp_path / "replay"
    (root / "clips").mkdir(parents=True)
    (root / "clips" / "T.mp4").write_bytes(b"recorded clip")
    entry = {"fingerprint": m.fingerprint(recorded), "file": "clips/T.mp4", "request": recorded.model_dump()}
    (root / "index.json").write_text(json.dumps({"entries": [entry]}))
    return Settings(_env_file=None, data_dir=tmp_path / "data", film_replay_dir=root, film_media="replay")


def test_replay_serves_the_recorded_clip_once_and_refuses_any_other_request(tmp_path):
    """Fails if replay serves a clip for a request it never recorded (a regeneration would replay the same
    rejected clip), or if a served clip is not cached under its fingerprint."""
    a, _ = _photo_files(tmp_path)
    photo = m.Photo.from_file(a)
    recorded = m.clip_request("Slow dolly", "source", photo)
    media = m.Media(_replay_settings(tmp_path, recorded))

    first = media.make(recorded, {photo.sha: photo})
    assert first.path.read_bytes() == b"recorded clip" and not first.cached
    assert media.make(recorded, {photo.sha: photo}).cached

    retry = m.clip_request("Slow dolly", "source", photo, rejected="pendant lights appeared")
    with pytest.raises(m.MediaError) as err:
        media.make(retry, {photo.sha: photo})
    assert err.value.category == "not_cached"


@pytest.fixture
def grok_session(tmp_path, monkeypatch):
    """A fake grok CLI: `run` reports a session whose event log holds `calls`, and one output video."""
    sessions = tmp_path / "home" / ".grok" / "sessions"
    monkeypatch.setattr(m.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/grok")
    monkeypatch.setattr(m.GrokBackend, "MIN_GAP_S", 0)

    def install(calls: list[dict]):
        sdir = sessions / "proj" / "sess-1"
        (sdir / "videos").mkdir(parents=True, exist_ok=True)
        (sdir / "videos" / "1.mp4").write_bytes(b"video")
        lines = [{"params": {"update": {"sessionUpdate": "tool_call", "title": "image_to_video", "rawInput": c}}} for c in calls]
        (sdir / "updates.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
        out = json.dumps({"sessionId": "sess-1", "text": "/x/videos/1.mp4", "total_cost_usd": 0.05})
        monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))

    return install


def test_grok_clip_is_refused_when_grok_altered_the_arguments(tmp_path, grok_session):
    """Fails if a clip made from arguments grok's own model rewrote (an off-prompt clip) is accepted."""
    a, _ = _photo_files(tmp_path)
    photo = m.Photo.from_file(a)
    req = m.clip_request("Slow dolly", "source", photo)
    backend = m.GrokBackend(Settings(_env_file=None, data_dir=tmp_path / "data"))
    sent = backend.tool_args(req, {photo.sha: photo})

    grok_session([{**sent, "prompt": sent["prompt"] + " Make it cinematic."}])
    with pytest.raises(m.MediaError) as err:
        backend.produce(req, {photo.sha: photo}, tmp_path / "work")
    assert err.value.category == "altered"

    grok_session([sent])
    assert backend.produce(req, {photo.sha: photo}, tmp_path / "work").path.read_bytes() == b"video"
