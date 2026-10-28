"""Copy mode: the agent writes the remarks and captions; the tools refuse fair-housing violations."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from fakes import ScriptedClient, make_shoot, reply, settings, tool

from shootagent.agent.loop import AgentRun
from shootagent.compliance.kit import build_kit
from shootagent.trace import TraceRun, read_events

BAD = ("Perfect for families: a bright three-bedroom home with an open kitchen, oak floors, a fenced yard and a "
       "two-car garage on a quiet street.")
GOOD = ("A bright three-bedroom home with an open kitchen, oak floors throughout, a primary suite with a walk-in "
        "closet, a fenced yard and a two-car garage on a quiet street.")


def test_copy_run_rewrites_refused_copy_and_captions_reach_the_kit(tmp_path):
    store, sid = make_shoot(tmp_path / "shoots", n_views=2)
    captions = [{"view": "v01", "caption": "Kitchen", "alt_text": "Open kitchen with oak floors and white cabinets."},
                {"view": "v02", "caption": "Primary suite", "alt_text": "Primary bedroom with a large window."}]
    client = ScriptedClient([
        reply(tool("submit_plan", steps=[{"intent": "remarks"}, {"intent": "captions"}], text="")),
        reply(tool("write_listing_copy", headline="Bright home on a quiet street", remarks=BAD)),
        reply(tool("write_listing_copy", headline="Bright home on a quiet street", remarks=GOOD),
              tool("set_photo_captions", captions=captions)),
        reply(text="Wrote the remarks and captions for both photos."),
    ])
    trace = TraceRun(store.shoot_dir(sid), "copy")
    AgentRun(store, sid, "copy", trace, settings(tmp_path), client).run()
    events = read_events(trace.path)
    results = [e["data"] for e in events if e["kind"] == "tool_result" and e["data"]["tool"] == "write_listing_copy"]
    assert [r["ok"] for r in results] == [False, True]
    assert "Perfect for families" in results[0]["summary"] and "nothing saved" in results[0]["summary"]
    shoot = store.load(sid)
    assert shoot.listing.description == GOOD and shoot.listing.headline == "Bright home on a quiet street"
    assert shoot.view("v02").meta["alt_text"].startswith("Primary bedroom")
    end = next(e for e in events if e["kind"] == "run_end")
    assert end["data"]["ok"]
    info = build_kit(store, sid, "http://example.test")
    with zipfile.ZipFile(info["path"]) as z:
        csv = z.read(next(n for n in z.namelist() if n.endswith("captions.csv"))).decode()
        listing = z.read(next(n for n in z.namelist() if n.endswith("listing.txt"))).decode()
    assert "Primary suite" in csv and "Open kitchen with oak floors" in csv
    assert GOOD in listing and "no flagged phrases" in listing


def test_copy_run_is_not_done_until_every_photo_has_a_caption(tmp_path):
    store, sid = make_shoot(tmp_path / "shoots", n_views=2)
    client = ScriptedClient([
        reply(tool("submit_plan", steps=[{"intent": "remarks"}], text="")),
        reply(tool("write_listing_copy", headline="Bright home on a quiet street", remarks=GOOD)),
        reply(text="Done."),  # nudged: captions missing
        reply(tool("set_photo_captions", captions=[{"view": "v01", "caption": "Kitchen", "alt_text": "Kitchen with oak floors."},
                                                   {"view": "v02", "caption": "Bedroom", "alt_text": "Bedroom with a window."}])),
        reply(text="Done."),
    ])
    trace = TraceRun(store.shoot_dir(sid), "copy")
    AgentRun(store, sid, "copy", trace, settings(tmp_path), client).run()
    nudges = [e for e in read_events(trace.path) if e["kind"] == "thought" and "set_photo_captions" in e["data"]["text"]]
    assert nudges and store.load(sid).view("v02").meta["caption"] == "Bedroom"
