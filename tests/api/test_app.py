"""HTTP contract the web UI relies on (docs/api.md): error shape, blob safety and caching, passcode, runs
over SSE, head moves."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from fakes import ScriptedClient, make_shoot, reply, settings, tool

from shootagent.agent.loop import RunManager, wait
from shootagent.api.app import create_app


@pytest.fixture
def env(tmp_path):
    store, sid = make_shoot(tmp_path / "shoots", size=(300, 400))
    client = ScriptedClient(
        [
            reply(tool("submit_plan", steps=[{"intent": "nothing to do"}], text="")),
            reply(
                tool(
                    "report_view_result",
                    view="v01",
                    results=[{"request": "x", "status": "not_needed", "reason": "n/a"}],
                ),
                tool(
                    "report_view_result",
                    view="v02",
                    results=[{"request": "x", "status": "not_needed", "reason": "n/a"}],
                ),
            ),
            reply(text="Nothing needed."),
        ]
    )
    mgr = RunManager()
    app = create_app(settings=settings(tmp_path), store=store, client=client, manager=mgr)
    return TestClient(app), store, sid, mgr


def sse_events(text: str) -> list[dict]:
    out, name = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name == "trace":
            out.append(json.loads(line.split(":", 1)[1]))
    return out


def test_unknown_shoot_uses_the_error_shape(env):
    http, *_ = env
    r = http.get("/api/shoots/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "shoot_not_found" and r.json()["error"]["message"]


def test_blob_names_cannot_escape_the_blob_folder(env):
    http, store, sid, _ = env
    for bad in ("..%2Fshoot.json", "..%5Cshoot.json", "shoot.json"):
        r = http.get(f"/api/shoots/{sid}/blobs/{bad}")
        assert r.status_code in (400, 404)
        assert b'"views"' not in r.content


def test_resized_blob_is_smaller_and_immutable(env):
    http, store, sid, _ = env
    blob = store.load(sid).view("v01").head_version.blob
    r = http.get(f"/api/shoots/{sid}/blobs/{blob}", params={"w": 100})
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert max(Image.open(io.BytesIO(r.content)).size) == 100
    full = http.get(f"/api/shoots/{sid}/blobs/{blob}")
    assert Image.open(io.BytesIO(full.content)).size == (400, 300)


def test_passcode_guards_api_but_accepts_header_or_query(tmp_path):
    store, sid = make_shoot(tmp_path / "shoots")
    http = TestClient(
        create_app(settings=settings(tmp_path, demo_passcode="sesame"), store=store, manager=RunManager())
    )
    assert http.get("/api/shoots").status_code == 401
    assert http.get("/api/shoots", headers={"x-demo-passcode": "sesame"}).status_code == 200
    assert http.get("/api/shoots", params={"passcode": "sesame"}).status_code == 200  # EventSource / <img>


def test_chat_run_streams_its_trace_and_lists_as_done(env):
    http, store, sid, mgr = env
    r = http.post(f"/api/shoots/{sid}/chat", json={"message": "remove the car"})
    run_id = r.json()["run_id"]
    with http.stream("GET", f"/api/shoots/{sid}/runs/{run_id}/events") as s:
        events = sse_events("".join(s.iter_text()))
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end" and "plan" in kinds
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert wait(mgr.get(run_id).trace, 10, manager=mgr)
    runs = http.get(f"/api/shoots/{sid}/runs").json()
    assert runs[0]["id"] == run_id and runs[0]["done"] and runs[0]["mode"] == "chat"
    # a finished run replays from disk, and Last-Event-ID resumes after a reconnect
    with http.stream("GET", f"/api/shoots/{sid}/runs/{run_id}/events", headers={"last-event-id": "3"}) as s:
        resumed = sse_events("".join(s.iter_text()))
    assert resumed[0]["seq"] == 4 and resumed[-1]["kind"] == "run_end"


def test_replay_of_a_run_that_never_finished_still_ends_the_stream(env):
    http, store, sid, _ = env
    path = store.shoot_dir(sid) / "runs" / "dead-run.jsonl"
    path.write_text(
        json.dumps(
            {
                "run": "dead-run",
                "seq": 1,
                "t": "2026-10-28T00:00:00+00:00",
                "kind": "run_start",
                "view": None,
                "data": {"mode": "qc"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with http.stream("GET", f"/api/shoots/{sid}/runs/dead-run/events") as s:
        events = sse_events("".join(s.iter_text()))
    assert events[-1]["kind"] == "run_end" and events[-1]["data"]["ok"] is False


def test_moving_head_is_validated_and_persisted(env):
    http, store, sid, _ = env
    assert http.post(f"/api/shoots/{sid}/views/v01/head", json={"version": "v01.9"}).status_code == 404
    store.add_version(
        sid,
        "v01",
        store.get_image(sid, store.load(sid).view("v01").head_version.blob)[::-1].copy(),
        op="test",
    )
    r = http.post(f"/api/shoots/{sid}/views/v01/head", json={"version": "v01.0"})
    assert r.status_code == 200 and r.json()["head"] == "v01.0"
    assert store.load(sid).view("v01").head == "v01.0"


def test_chat_with_an_unknown_view_is_a_400_not_a_crashed_run(env):
    http, _, sid, _ = env
    r = http.post(f"/api/shoots/{sid}/chat", json={"message": "x", "views": ["v99"]})
    assert r.status_code == 400 and "v99" in r.json()["error"]["message"]
