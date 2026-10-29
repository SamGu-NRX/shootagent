"""Film mode in the agent loop: what the agent owes before it may finish."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from fakes import ScriptedClient, make_shoot, reply, settings, tool  # noqa: E402

from shootagent.agent.loop import AgentRun  # noqa: E402
from shootagent.trace import TraceRun, read_events  # noqa: E402


def test_film_run_is_held_until_every_language_is_assembled(tmp_path):
    """Fails if a film run can end with a language never rendered."""
    store, sid = make_shoot(tmp_path / "shoots")
    client = ScriptedClient([reply(tool("submit_plan", steps=[{"intent": "walk the home"}], text="Plan.")),
                             reply(text="Done.")])
    trace = TraceRun(store.shoot_dir(sid), "film")
    AgentRun(store, sid, "film", trace, settings(tmp_path), client, languages=["en", "es"]).run()

    nudges = [e["data"]["text"] for e in read_events(trace.path) if e["kind"] == "thought" and "Not finished" in e["data"]["text"]]
    assert nudges and "en, es" in nudges[0]


def test_a_language_code_cannot_name_a_folder_outside_the_film(tmp_path):
    """Fails if a language such as '../x' reaches assembly, where it becomes a directory name."""
    import pytest

    store, sid = make_shoot(tmp_path / "shoots")
    trace = TraceRun(store.shoot_dir(sid), "film")
    with pytest.raises(ValueError):
        AgentRun(store, sid, "film", trace, settings(tmp_path), ScriptedClient([]), languages=["en", "../x"])
