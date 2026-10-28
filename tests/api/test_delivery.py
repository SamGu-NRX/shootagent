"""Delivery routes (docs/api.md, Delivery): disclosures and publication, the public page, the client review link and
the listing kit. Token routes work without the photographer's passcode; the token is the credential."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from fakes import ScriptedClient, make_shoot, reply, settings, tool

from shootagent.agent.loop import RunManager, wait
from shootagent.api.app import create_app

PASS = {"x-demo-passcode": "sesame"}


@pytest.fixture
def env(tmp_path):
    store, sid = make_shoot(tmp_path / "shoots", size=(300, 400))
    client = ScriptedClient([
        reply(tool("submit_plan", steps=[{"view": "v01", "intent": "brighten"}], text="")),
        reply(tool("report_view_result", view="v01",
                   results=[{"request": "brighter", "status": "not_needed", "reason": "already bright"}])),
        reply(text="It is already bright."),
    ])
    mgr = RunManager()
    app = create_app(settings=settings(tmp_path, demo_passcode="sesame"), store=store, client=client, manager=mgr)
    return TestClient(app), store, sid, mgr


def test_compliance_and_publish_then_public_page_needs_no_passcode(env):
    http, store, sid, _ = env
    assert http.get(f"/api/shoots/{sid}/compliance").status_code == 401
    rep = http.get(f"/api/shoots/{sid}/compliance", headers=PASS).json()
    assert rep["stale"] and rep["publication"] is None and len(rep["photos"]) == 2
    pub = http.post(f"/api/shoots/{sid}/publish", headers=PASS).json()
    token = pub["token"]
    page = http.get(f"/api/public/{token}")  # no passcode
    assert page.status_code == 200 and len(page.json()["photos"]) == 2
    photo = page.json()["photos"][0]
    assert photo["chain"] and photo["final"]["sha256"]
    assert http.get(f"/api/public/{token}/blobs/{photo['final']['blob']}").status_code == 200
    # only published files are served on the public link
    raw = store.load(sid).view("v01").versions[0].blob
    assert http.get(f"/api/public/{token}/blobs/{raw}").status_code == 404
    assert http.get("/api/public/not-a-token").status_code == 404
    assert not http.get(f"/api/shoots/{sid}/compliance", headers=PASS).json()["stale"]


def test_review_link_comments_declines_and_client_runs(env):
    http, store, sid, mgr = env
    share = http.post(f"/api/shoots/{sid}/shares", json={"label": "Jane"}, headers=PASS).json()
    t = share["token"]
    review = http.get(f"/api/review/{t}").json()  # no passcode
    assert [v["id"] for v in review["views"]] == ["v01", "v02"] and review["requests_left"] == 20
    # A request the policy declines costs nothing: no run, an immediate answer.
    c = http.post(f"/api/review/{t}/comments", json={"view": "v01", "text": "remove the power lines"}).json()
    assert c["state"] == "declined" and c["run"] is None and "surroundings" in c["policy"]["items"][0]["category"]
    # An allowed request starts a client-origin chat run scoped to that photo.
    c2 = http.post(f"/api/review/{t}/comments", json={"view": "v01", "text": "make it brighter"}).json()
    assert c2["run"] and c2["state"] in ("working", "queued", "not_needed")
    assert wait(mgr.get(c2["run"]).trace, 10, manager=mgr)
    done = next(x for x in http.get(f"/api/review/{t}").json()["comments"] if x["id"] == c2["id"])
    assert done["state"] == "not_needed" and "already bright" in done["reason"]
    start = http.get(f"/api/review/{t}/runs/{c2['run']}/events")
    assert start.status_code == 200 and '"origin": "client"' in start.text
    # the photographer sees the same comments
    assert len(http.get(f"/api/shoots/{sid}/comments", headers=PASS).json()) == 2
    # verdicts
    r = http.post(f"/api/review/{t}/views/v02/verdict", json={"status": "approved"}).json()
    assert next(v for v in r["views"] if v["id"] == "v02")["client"]["status"] == "approved"
    # revoking the link turns it off
    assert http.delete(f"/api/shoots/{sid}/shares/{t}", headers=PASS).json()["ok"]
    assert http.get(f"/api/review/{t}").status_code == 404


def test_undo_moves_the_photo_back(env):
    http, store, sid, mgr = env
    t = http.post(f"/api/shoots/{sid}/shares", headers=PASS).json()["token"]
    c = http.post(f"/api/review/{t}/comments", json={"view": "v02", "text": "remove the car"}).json()
    assert wait(mgr.get(c["run"]).trace, 10, manager=mgr)
    # the edit the run would have made
    img = store.get_image(sid, store.load(sid).view("v02").head_version.blob)
    store.add_version(sid, "v02", img[::-1].copy(), op="remove_object", params={"what": "car"})
    r = http.post(f"/api/review/{t}/comments/{c['id']}/undo")
    assert r.status_code == 200 and r.json()["state"] == "undone"
    assert store.load(sid).view("v02").head == "v02.0"


def test_kit_builds_and_downloads(env):
    http, store, sid, _ = env
    info = http.post(f"/api/shoots/{sid}/kit", headers=PASS).json()
    assert info["photos"] == 2 and info["url"].endswith("latest.zip")
    z = http.get(info["url"], headers=PASS)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert any(n.endswith("provenance/manifest.json") for n in names)
    assert http.get(f"/api/shoots/{sid}/kit", headers=PASS).json()["name"] == info["name"]


def test_lint_routes(env):
    http, *_ = env
    r = http.post("/api/lint/listing", json={"text": "Perfect for families, with a master suite."}, headers=PASS).json()
    assert not r["ok"] and {f["severity"] for f in r["flags"]} == {"avoid", "review"}
    p = http.post("/api/lint/request", json={"text": "fix the crack in the driveway"}, headers=PASS).json()
    assert p["verdict"] == "decline"


def test_photographer_rules_on_a_finding(env):
    http, store, sid, _ = env
    from shootagent.store import Finding

    store.mutate(sid, lambda s: s.view("v01").findings.append(
        Finding(id="f_x", type="paint_color", explanation="wall colour differs", status="open")))
    rep = http.get(f"/api/shoots/{sid}/compliance", headers=PASS).json()
    assert next(p for p in rep["photos"] if p["view"] == "v01")["status"] == "review" and not rep["ready"]
    r = http.post(f"/api/shoots/{sid}/views/v01/findings/f_x", json={"verdict": "not_alteration"}, headers=PASS)
    assert r.status_code == 200 and r.json()["findings"][0]["evidence"]["photographer"] == "not_alteration"
    rep = http.get(f"/api/shoots/{sid}/compliance", headers=PASS).json()
    assert rep["ready"]
