"""The listing photo policy: what is declined, disclosed or exempt, and the loop refusing a declined edit."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from fakes import ScriptedClient, make_shoot, reply, settings, tool

from shootagent.agent.loop import AgentRun
from shootagent.compliance.policy import check_edit, classify_request
from shootagent.trace import TraceRun, read_events

CASES = {
    # the editor-request phrasings that passed from chat (README Results) stay allowed and disclosed
    "remove the grey car": "disclose",
    "remove wires and extension cord": "disclose",
    "Please use #C8C2B7 color for floors": "disclose",
    "Add the reference image on the TV screen": "disclose",
    "change the grey walls to the colour in the reference image": "disclose",
    "Add a 'Listed by Acme Realty' watermark bottom-right on every photo": "overlay",
    "half a stop brighter on every photo": "exempt",
    "straighten the verticals": "exempt",
    "remove the tripod reflection in the mirror": "exempt",
    "add fire to the fireplace": "disclose",
    "virtually stage the living room": "disclose",
    "replace the sky with a blue sky": "disclose",
    "make the lawn greener": "disclose",
    "remove the door mat": "disclose",
    "remove the wall art": "disclose",
    "remove the neighbor's car": "disclose",
    "remove the damaged trash can": "disclose",
    "Declutter this garage to create a completely empty, clean garage with nothing but the floor and walls": "disclose",
    "remove the trash bins but keep the windows, doors and fence intact": "disclose",
    # misrepresentation is declined
    "remove the power lines and the trash bins": "decline",
    "fix the crack in the driveway": "decline",
    "remove the water stain on the ceiling": "decline",
    "add a pool to the backyard": "decline",
    "replace the view out the window with an ocean view": "decline",
    "remove the wall between the kitchen and living room": "decline",
    "make the room look bigger": "decline",
    "remove the neighbor's house": "decline",
    "remove the electric pole": "decline",
}


@pytest.mark.parametrize("text,want", CASES.items())
def test_request_verdicts(text, want):
    assert classify_request(text).verdict == want


def test_a_mixed_request_keeps_its_allowed_parts():
    r = classify_request("Remove the power lines and the cracks in the driveway, then brighten everything")
    verdicts = [(i.verdict, i.category) for i in r.items]
    assert verdicts == [("decline", "surroundings"), ("decline", "condition"), ("exempt", "adjustment")]
    assert r.verdict == "decline" and len(r.declined) == 2


@pytest.mark.parametrize("text", ["Stage the bedroom", "Furnish the room", "Virtually stage the bedroom",
                                  "Stage the patio with outdoor furniture"])
def test_staging_a_space_is_disclosed_not_declined_as_a_new_feature(text):
    r = classify_request(text)
    assert r.verdict == "disclose" and [(i.verdict, i.category) for i in r.items] == [("disclose", "staging")]


def test_staging_does_not_excuse_adding_a_feature():
    r = classify_request("Stage the bedroom and add a fireplace")
    assert [(i.verdict, i.category) for i in r.items] == [("disclose", "staging"), ("decline", "feature")]


def test_check_edit_judges_the_object_not_the_context():
    assert check_edit("remove_object", {"what": "power lines", "intent": "x"}).category == "surroundings"
    assert check_edit("remove_object", {"what": "grey car", "intent": "the car in front of the neighbour's house is gone"}) is None
    assert check_edit("remove_object", {"what": "car", "intent": "the car is gone and the cracked driveway looks natural"}) is None
    assert check_edit("generative_fill", {"prompt": "lit fire inside the fireplace", "intent": "fire"}) is None
    assert check_edit("generative_fill", {"prompt": "a swimming pool in the backyard", "intent": "x"}).category == "feature"
    assert check_edit("recolor_region", {"intent": "cover the water stain on the ceiling", "hex": "#fff"}).category == "condition"


DATASET = os.environ.get("AUTOHDR_EDITOR_REQUESTS", "")


@pytest.mark.skipif(not DATASET or not Path(DATASET).is_file(), reason="AUTOHDR_EDITOR_REQUESTS not set")
def test_real_customer_requests_are_not_refused_by_mistake():
    records = json.loads(Path(DATASET).read_text(encoding="utf-8"))["records"]
    declined = [r["request"] for r in records if classify_request(r["request"]).verdict == "decline"]
    assert declined == ["remove the electric pole"]


def test_loop_refuses_a_misrepresenting_edit_and_the_policy_reaches_the_trace(tmp_path, fake_ops):
    import numpy as np

    store, sid = make_shoot(tmp_path / "shoots")
    m = np.zeros((48, 64), bool)
    m[10:30, 10:40] = True
    mask = store.add_mask(sid, "v01", m, source="user")
    # The model ignores the policy and tries the removal anyway: the loop refuses it before any pixel changes.
    client = ScriptedClient([
        reply(tool("submit_plan", steps=[{"intent": "remove the lines"}], text="")),
        reply(tool("remove_object", view="v01", mask=mask.id, what="power lines", intent="the lines are gone")),
        reply(tool("report_view_result", view="v01", results=[
            {"request": "remove the power lines", "status": "declined", "reason": "permanent surroundings"}])),
        reply(text="I can't remove power lines from a listing photo."),
    ])
    trace = TraceRun(store.shoot_dir(sid), "chat")
    AgentRun(store, sid, "chat", trace, settings(tmp_path), client, request="remove the power lines",
             views=["v01"]).run()
    events = read_events(trace.path)
    start = next(e for e in events if e["kind"] == "run_start")
    assert start["data"]["policy"]["verdict"] == "decline"
    refused = [e for e in events if e["kind"] == "tool_result" and e["data"]["tool"] == "remove_object"]
    assert refused and refused[0]["data"]["data"]["refused"] and "listing photo policy" in refused[0]["data"]["summary"]
    assert store.load(sid).view("v01").head == "v01.0"
    end = next(e for e in events if e["kind"] == "run_end")
    assert end["data"]["counts"]["edits_declined"] == 1
    opening = client.requests[0]["messages"][0]["content"][0]["text"]
    assert "policy check" in opening and "decline" in opening
