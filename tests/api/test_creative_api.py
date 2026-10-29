"""HTTP contract for the creative lab (docs/api.md, "Creative lab"): start with fake phases and poll to the end,
list, cancel and resume, re-simulate, and lab files with Range requests and no way out of the lab folder."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "creative"))
from test_lab import Fakes, stop_labs  # noqa: E402  (the same fake phases the runner's tests use)

from shootagent.agent.loop import RunManager
from shootagent.api.app import create_app
from shootagent.config import Settings
from shootagent.creative import lab
from shootagent.store import Listing, ShootStore

BASE = "/api/shoots/s1/creative"


def make_settings(tmp_path, **kw) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, **{"anthropic_api_key": "test", **kw})


@pytest.fixture
def fakes(monkeypatch):
    f = Fakes()
    monkeypatch.setattr(lab, "PHASES", f.phases())
    yield f
    stop_labs()


@pytest.fixture
def env(tmp_path, fakes):
    store = ShootStore(tmp_path / "shoots")
    store.create("Test shoot", shoot_id="s1")
    store.mutate("s1", lambda s: setattr(s, "listing", Listing(address="Unit 4B")))
    app = create_app(settings=make_settings(tmp_path), store=store, client=object(), manager=RunManager())
    return TestClient(app), store, fakes


def poll(http: TestClient, lab_id: str, until=("done", "failed", "cancelled"), timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        state = http.get(f"{BASE}/{lab_id}").json()
        if state["status"] in until or time.monotonic() > deadline:
            return state
        time.sleep(0.05)


def start(http: TestClient, **body) -> str:
    r = http.post(BASE, json=body)
    assert r.status_code == 200, r.text
    return r.json()["lab_id"]


def test_start_poll_and_list(env):
    http, store, _ = env
    lab_id = start(http, render_single_agent=True)
    state = poll(http, lab_id)
    assert state["status"] == "done" and state["id"] == lab_id and state["shoot_id"] == "s1"
    assert [r["id"] for r in state["reels"]] == ["A", "B", "C", "S"]
    assert [s["reel"] for s in state["simulations"]] == ["A", "B", "C", "S"]
    assert state["options"]["render_single_agent"] is True and state["options"]["finalists"] == 2
    assert all(p["status"] == "done" for p in state["phases"]) and state["evaluation"]["verdict"]
    rows = http.get(BASE).json()
    assert [(r["id"], r["status"], r["reels"], r["phase"]) for r in rows] == [(lab_id, "done", 4, "simulate")]
    assert set(rows[0]) >= {"id", "created", "status", "phase", "reels", "usd"}


def test_a_body_is_optional_and_partial(env):
    http, *_ = env
    r = http.post(BASE)
    assert r.status_code == 200
    state = poll(http, r.json()["lab_id"])
    assert state["status"] == "done" and state["options"]["finalists"] == 2 and state["options"]["seed"] == 7


def test_lab_files_support_range_requests(env):
    http, *_ = env
    lab_id = start(http)
    poll(http, lab_id)
    url = f"{BASE}/{lab_id}/files/reels/B.mp4"
    full = http.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes" and len(full.content) == 16384
    part = http.get(url, headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.headers["content-range"] == "bytes 100-199/16384"
    assert part.content == full.content[100:200]
    tail = http.get(url, headers={"Range": "bytes=16000-"})
    assert tail.status_code == 206 and tail.content == full.content[16000:]
    assert http.get(url, headers={"Range": "bytes=99999-"}).status_code == 416
    poster = http.get(f"{BASE}/{lab_id}/files/reels/B.jpg")
    assert poster.status_code == 200 and poster.headers["content-type"] == "image/jpeg"
    assert http.get(f"{BASE}/{lab_id}/files/reels/Z.mp4").json()["error"]["code"] == "file_not_found"


def test_lab_files_cannot_leave_the_lab_folder(env):
    http, store, _ = env
    lab_id = start(http)
    poll(http, lab_id)
    folder = lab.lab_dir(store, "s1", lab_id)
    (folder / "reels" / "link.json").symlink_to(store.shoot_dir("s1") / "shoot.json")
    for bad in ("..%2F..%2Fshoot.json", "reels%2F..%2F..%2F..%2Fshoot.json", "%2e%2e%2f%2e%2e%2fshoot.json",
                "reels%5C..%5C..%5C..%5Cshoot.json", "%2Fetc%2Fpasswd", "reels/link.json", ".state.json"):
        r = http.get(f"{BASE}/{lab_id}/files/{bad}")
        assert r.status_code in (400, 404), bad
        assert b'"views"' not in r.content and b"root:" not in r.content, bad
        assert r.json()["error"]["code"] in ("bad_path", "file_not_found"), bad


def test_errors_use_the_api_error_shape(env, tmp_path):
    http, *_ = env
    assert http.get(f"{BASE}/nope").json()["error"]["code"] == "lab_not_found"
    assert http.get("/api/shoots/nope/creative").json()["error"]["code"] == "shoot_not_found"
    r = http.post(BASE, json={"finalists": 50})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_options"
    r = http.post(BASE, json={"rounds": "many"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    for verb in ("cancel", "resume", "simulate"):
        assert http.post(f"{BASE}/nope/{verb}").status_code == 404


def test_without_claude_credentials_a_lab_does_not_start(tmp_path, fakes):
    store = ShootStore(tmp_path / "shoots")
    store.create("Test shoot", shoot_id="s1")
    settings = make_settings(tmp_path, anthropic_api_key=None, anthropic_auth_token=None)
    http = TestClient(create_app(settings=settings, store=store, manager=RunManager()))
    r = http.post(BASE)
    assert r.status_code == 503 and r.json()["error"]["code"] == "agent_unavailable"
    assert http.get(BASE).json() == []


def test_cancel_then_resume(env):
    http, _, fakes = env
    fakes.hold = "critique"
    lab_id = start(http)
    assert fakes.held.wait(5)
    running = http.get(f"{BASE}/{lab_id}").json()
    assert running["status"] == "running" and running["phase"] == "critique"
    r = http.post(BASE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "lab_conflict"
    assert http.post(f"{BASE}/{lab_id}/resume").status_code == 409  # still running
    assert http.post(f"{BASE}/{lab_id}/cancel").json() == {"ok": True}
    assert poll(http, lab_id)["status"] == "cancelled"
    assert http.post(f"{BASE}/{lab_id}/cancel").json() == {"ok": False}
    fakes.hold = None
    r = http.post(f"{BASE}/{lab_id}/resume")
    assert r.status_code == 200 and r.json() == {"lab_id": lab_id}
    state = poll(http, lab_id)
    assert state["status"] == "done" and fakes.calls.count("write_brief") == 1
    assert http.post(f"{BASE}/{lab_id}/resume").status_code == 409  # finished: nothing to resume


def test_simulate_recomputes_and_saves(env):
    http, _, fakes = env
    lab_id = start(http)
    before = poll(http, lab_id)
    r = http.post(f"{BASE}/{lab_id}/simulate", json={"followers": 20000, "hours": 24, "seed": 3})
    assert r.status_code == 200
    sims = r.json()
    assert [s["reel"] for s in sims] == ["A", "B", "C"]
    assert all(s["followers"] == 20000 and s["hours"] == 24 and s["seed"] == 3 for s in sims)
    assert all(len(s["series"]) == 25 for s in sims)
    after = http.get(f"{BASE}/{lab_id}").json()
    assert after["simulations"] == sims and after["options"]["followers"] == 20000
    assert after["feed"] == before["feed"]  # only the launch changed
    partial = http.post(f"{BASE}/{lab_id}/simulate", json={"seed": 4}).json()
    assert all(s["followers"] == 20000 and s["hours"] == 24 and s["seed"] == 4 for s in partial)
    r = http.post(f"{BASE}/{lab_id}/simulate", json={"hours": 0})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_simulate_before_an_evaluation_conflicts(env):
    http, _, fakes = env
    fakes.fail.add("evaluate")
    lab_id = start(http)
    assert poll(http, lab_id)["status"] == "failed"
    r = http.post(f"{BASE}/{lab_id}/simulate", json={"followers": 9000})
    assert r.status_code == 409 and r.json()["error"]["code"] == "lab_conflict"


def test_the_passcode_covers_lab_files(tmp_path, fakes):
    store = ShootStore(tmp_path / "shoots")
    store.create("Test shoot", shoot_id="s1")
    app = create_app(settings=make_settings(tmp_path, demo_passcode="sesame"), store=store, client=object(),
                     manager=RunManager())
    http = TestClient(app)
    assert http.post(BASE).status_code == 401
    lab_id = http.post(BASE, headers={"x-demo-passcode": "sesame"}).json()["lab_id"]
    assert lab.wait_lab(lab_id, 10)
    url = f"{BASE}/{lab_id}/files/reels/A.mp4"
    assert http.get(url).status_code == 401
    assert http.get(url, params={"passcode": "sesame"}).status_code == 200  # what <video src> can send
