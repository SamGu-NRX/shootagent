"""Agent loop rules the model cannot bypass: plan first, enforced self-check with revert and retry, attempt
cap, run scope, one active run per shoot, and the run_end totals the UI shows."""

from __future__ import annotations

import threading

import numpy as np
import pytest
from fakes import ScriptedClient, make_shoot, reply, settings, tool

from shootagent.agent.loop import AgentRun, RunManager, start_run, wait
from shootagent.tools import _ops
from shootagent.trace import TraceRun, read_events

PLAN = dict(steps=[{"intent": "brighten every view"}], text="Brighten.")


def run(tmp_path, replies, mode="chat", request="make it brighter", views=None, **skw):
    store, sid = make_shoot(tmp_path / "shoots")
    client = ScriptedClient(replies)
    trace = TraceRun(store.shoot_dir(sid), mode)
    AgentRun(store, sid, mode, trace, settings(tmp_path, **skw), client, request=request, views=views).run()
    return store, sid, client, read_events(trace.path)


def kinds(events, kind):
    return [e for e in events if e["kind"] == kind]


def test_edit_before_plan_is_refused_and_does_not_touch_pixels(tmp_path, fake_ops):
    store, sid, client, events = run(
        tmp_path,
        [
            reply(tool("adjust_exposure", view="v01", ev=0.5, intent="brighter")),
            reply(tool("submit_plan", **PLAN)),
            reply(text="Done."),
        ],
    )
    first = kinds(events, "tool_result")[0]
    assert first["data"]["tool"] == "adjust_exposure" and not first["data"]["ok"]
    assert "submit_plan first" in first["data"]["summary"]
    assert store.load(sid).view("v01").head == "v01.0"
    assert len(kinds(events, "plan")) == 1
    # the refusal reached the model as an error tool_result
    tr = client.requests[1]["messages"][-1]["content"][0]
    assert tr["is_error"] and "submit_plan" in tr["content"][0]["text"]


def test_failed_check_reverts_head_emits_retry_and_next_attempt_lands(tmp_path, fake_ops):
    fake_ops.extend(["fail", "pass"])
    store, sid, client, events = run(
        tmp_path,
        [
            reply(tool("submit_plan", **PLAN)),
            reply(tool("adjust_exposure", view="v01", ev=0.5, intent="half a stop brighter")),
            reply(tool("adjust_exposure", view="v01", ev=0.4, intent="half a stop brighter")),
            reply(text="Done."),
        ],
        views=["v01"],
    )
    checks = kinds(events, "check")
    assert [c["data"]["passed"] for c in checks] == [False, True]
    retry = kinds(events, "retry")
    assert (
        len(retry) == 1
        and retry[0]["data"]["attempt"] == 1
        and "judge said fail" in retry[0]["data"]["reason"]
    )
    # retry comes after the failing check, before the next model call's tool_call
    seqs = [e["seq"] for e in events]
    assert checks[0]["seq"] < retry[0]["seq"] < kinds(events, "tool_call")[2]["seq"] <= seqs[-1]
    view = store.load(sid).view("v01")
    assert view.head == "v01.2"
    assert (
        view.version("v01.2").parent == "v01.0"
    )  # the second attempt started from the original, not the failure
    assert view.version("v01.1").check["passed"] is False
    tr = client.requests[2]["messages"][-1]["content"][0]
    assert "reverted" in tr["content"][0]["text"]
    # images are allowed only in non-error results; the failed check still shows the model its before/after
    assert not tr["is_error"] and any(b["type"] == "image" for b in tr["content"])


def test_attempt_cap_refuses_further_edits_on_that_view(tmp_path, fake_ops):
    fake_ops.extend(["fail"] * 5)
    edit = lambda: tool("adjust_exposure", view="v01", ev=0.5, intent="brighter")
    store, sid, client, events = run(
        tmp_path,
        [
            reply(tool("submit_plan", **PLAN)),
            reply(edit()),
            reply(edit()),
            reply(edit()),
            reply(text="Gave up on v01."),
        ],
        views=["v01"],
        max_fix_attempts=2,
    )
    assert len(kinds(events, "check")) == 2
    assert len(kinds(events, "retry")) == 1  # the second failure exhausts the budget: no retry offered
    last = kinds(events, "tool_result")[-1]
    assert not last["data"]["ok"] and "fix attempts" in last["data"]["summary"]
    assert store.load(sid).view("v01").head == "v01.0"


def test_pixels_changed_outside_the_mask_fail_the_check_even_if_the_judge_passes(
    tmp_path, fake_ops, monkeypatch
):
    store, sid = make_shoot(tmp_path / "shoots", n_views=1)
    mask = np.zeros((48, 64), bool)
    mask[10:20, 10:20] = True
    m = store.add_mask(sid, "v01", mask, source="test")
    monkeypatch.setattr(_ops, "recolor", lambda img, mk, hx: (np.full_like(img, 200), mk))
    monkeypatch.setattr(
        _ops, "composite", lambda before, after, mk: after
    )  # buggy composite leaks everywhere
    client = ScriptedClient(
        [
            reply(tool("submit_plan", **PLAN)),
            reply(tool("recolor_region", view="v01", mask=m.id, hex="#C8C2B7", intent="floor to greige")),
            reply(text="Done."),
        ]
    )
    trace = TraceRun(store.shoot_dir(sid), "chat")
    AgentRun(store, sid, "chat", trace, settings(tmp_path), client, request="recolor floor").run()
    check = kinds(read_events(trace.path), "check")[0]["data"]
    assert not check["passed"]
    assert check["metrics"]["outside_max_delta"] > 1
    assert any("outside the mask" in f for f in check["failures"])
    assert store.load(sid).view("v01").head == "v01.0"


def test_views_outside_scope_are_never_edited(tmp_path, fake_ops):
    store, sid, client, events = run(
        tmp_path,
        [
            reply(tool("submit_plan", **PLAN)),
            reply(tool("adjust_exposure", view="v02", ev=0.5, intent="brighter")),
            reply(text="Done."),
        ],
        views=["v01"],
    )
    res = kinds(events, "tool_result")[-1]["data"]
    assert not res["ok"] and "outside this run's scope" in res["summary"]
    assert store.load(sid).view("v02").head == "v02.0"


def test_chat_run_is_nudged_until_every_view_is_reported(tmp_path, fake_ops):
    report = lambda v: tool(
        "report_view_result",
        view=v,
        results=[{"request": "brighter", "status": "not_needed", "reason": "already bright"}],
    )
    store, sid, client, events = run(
        tmp_path,
        [
            reply(tool("submit_plan", **PLAN)),
            reply(report("v01")),
            reply(text="All done."),  # v02 not reported yet: the loop must push back
            reply(report("v02")),
            reply(text="All done."),
        ],
    )
    nudge = client.requests[3]["messages"][-1]
    assert nudge["role"] == "user" and "report_view_result for v02" in nudge["content"][0]["text"]
    end = kinds(events, "run_end")[0]["data"]
    assert end["ok"] and end["counts"]["edits_skipped"] == 2
    assert kinds(events, "message")[-1]["data"]["text"] == "All done."


def test_run_end_totals_price_agent_and_judge_tokens(tmp_path, fake_ops):
    store, sid, client, events = run(
        tmp_path,
        [
            reply(tool("submit_plan", **PLAN)),
            reply(tool("adjust_exposure", view="v01", ev=0.5, intent="brighter")),
            reply(
                tool(
                    "report_view_result",
                    view="v01",
                    results=[{"request": "brighter", "status": "applied", "reason": "+0.5 EV"}],
                )
            ),
            reply(text="Done."),
        ],
        views=["v01"],
    )
    usage = kinds(events, "usage")
    assert [u["data"]["purpose"] for u in usage].count("agent") == 4
    assert [u["data"]["purpose"] for u in usage].count("judge_edit") == 1
    # sonnet-5-5 at $2 / $10 per MTok: agent 4 x (1000 in, 100 out) + judge (500 in, 50 out)
    expected = 4 * (1000 * 2 + 100 * 10) / 1e6 + (500 * 2 + 50 * 10) / 1e6
    end = kinds(events, "run_end")[0]["data"]
    assert end["usd"] == pytest.approx(expected, abs=1e-6)
    assert end["counts"]["edits_applied"] == 1 and end["counts"]["model_calls"] == 4


def test_second_run_on_a_shoot_waits_for_the_first_and_cancel_drops_a_queued_run(tmp_path, fake_ops):
    store, sid = make_shoot(tmp_path / "shoots")
    gate = threading.Event()
    mgr = RunManager()
    s = settings(tmp_path)
    first = start_run(
        store,
        sid,
        "chat",
        request="a",
        settings=s,
        client=ScriptedClient([reply(text="ok")], gate=gate),
        manager=mgr,
    )
    second = start_run(
        store, sid, "chat", request="b", settings=s, client=ScriptedClient([reply(text="ok")]), manager=mgr
    )
    third = start_run(
        store, sid, "chat", request="c", settings=s, client=ScriptedClient([reply(text="ok")]), manager=mgr
    )
    assert mgr.get(second.id).status == "queued"
    assert mgr.cancel(third.id)
    gate.set()
    assert wait(first, 10, manager=mgr) and wait(second, 10, manager=mgr)
    first_end = kinds(read_events(first.path), "run_end")[0]["t"]
    second_start = kinds(read_events(second.path), "run_start")[0]["t"]
    assert second_start >= first_end
    third_events = read_events(third.path)
    assert [e["kind"] for e in third_events] == ["run_end"] and not third_events[0]["data"]["ok"]
    assert store.load(sid).runs == [first.id, second.id, third.id]


@pytest.mark.parametrize("photoreal, passed", [(True, True), (False, False)])
def test_measured_exposure_overrides_a_judge_that_cannot_see_it_but_not_an_unrealistic_result(
    tmp_path, fake_ops, photoreal, passed
):
    fake_ops.append(
        {
            "verdict": "fail",
            "reason": "looks the same brightness",
            "issues": [],
            "checks": {"intent_achieved": False, "photoreal": photoreal, "outside_unchanged": True},
        }
    )
    store, sid, client, events = run(
        tmp_path,
        [
            reply(tool("submit_plan", **PLAN)),
            reply(tool("adjust_exposure", view="v01", ev=0.5, intent="half a stop brighter")),
            reply(text="Done."),
        ],
        views=["v01"],
    )
    check = kinds(events, "check")[0]["data"]
    assert check["metrics"]["ev_measured"] == pytest.approx(0.5, abs=0.05)
    assert check["passed"] is passed
    assert store.load(sid).view("v01").head == ("v01.1" if passed else "v01.0")
