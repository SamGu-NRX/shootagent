"""Portable shoot bundle: export on one machine, import into a fresh data dir on another."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "agent"))
from fakes import make_shoot  # noqa: E402

from shootagent.bundle import BundleError, export_shoot, import_shoot  # noqa: E402
from shootagent.store import ShootStore  # noqa: E402


@pytest.fixture
def shoot(tmp_path):
    store, sid = make_shoot(tmp_path / "pc" / "shoots")
    d = store.shoot_dir(sid)
    (d / "runs" / "r1.jsonl").write_text('{"kind": "run_start"}\n')
    for rel, data in {"film/manifest.json": b"{}", "film/en/film.mp4": b"\x00mp4", "listing/state.json": b"{}",
                      "film/work/en/source.mp4": b"scratch", "cache/x.w360.jpg": b"resized", ".env": b"KEY=secret"}.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(data)
    return store, sid


def test_a_shoot_round_trips_into_a_fresh_data_dir_without_scratch_or_secrets(shoot, tmp_path):
    """Fails if the Mac's copy differs from the PC's in any file a served run reads, or if scratch, caches or an
    env file travel with it."""
    store, sid = shoot
    zpath = tmp_path / "shoot.zip"
    export_shoot(store.shoot_dir(sid), zpath)
    mac = tmp_path / "mac" / "shoots"
    assert import_shoot(zpath, mac)["shoot_id"] == sid

    src, dst = store.shoot_dir(sid), mac / sid
    kept = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    assert "film/en/film.mp4" in kept and "runs/r1.jsonl" in kept and "listing/state.json" in kept
    assert not any(k.startswith(("film/work/", "cache/")) or k == ".env" for k in kept)
    assert all((src / k).read_bytes() == (dst / k).read_bytes() for k in kept)
    assert ShootStore(mac).load(sid).views == store.load(sid).views


def _rewrite(zpath: Path, out: Path, change) -> None:
    with zipfile.ZipFile(zpath) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            name, data = change(info.filename, zin.read(info))
            zout.writestr(name, data)


def test_a_damaged_or_path_escaping_bundle_is_refused_and_leaves_nothing(shoot, tmp_path):
    """Fails if a bundle with altered bytes, or an entry that would land outside the shoot folder, is imported."""
    store, sid = shoot
    zpath = tmp_path / "shoot.zip"
    export_shoot(store.shoot_dir(sid), zpath)
    mac = tmp_path / "mac" / "shoots"

    bad = tmp_path / "bad.zip"
    _rewrite(zpath, bad, lambda n, d: (n, d + b"!" if n.endswith("film.mp4") else d))
    with pytest.raises(BundleError, match="hash"):
        import_shoot(bad, mac)

    def escape(n, d):
        if n == "bundle.json":
            m = json.loads(d)
            m["files"].append({"path": "../../evil.txt", "size": 1, "sha256": "0" * 64})
            return n, json.dumps(m)
        return n, d

    _rewrite(zpath, bad, escape)
    with zipfile.ZipFile(bad, "a") as z:
        z.writestr("shoot/../../evil.txt", "x")
    with pytest.raises(BundleError, match="unsafe"):
        import_shoot(bad, mac)
    assert not (mac / sid).exists() and not list(mac.glob(".import-*"))
    assert not (tmp_path / "evil.txt").exists()
