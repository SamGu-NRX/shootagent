"""The evaluation network with a fake Claude client: agreement statistics, viewer consistency, blinding, the
shared-prefix cache warm-up, and evaluate() end to end on two tiny synthetic mp4s."""
from __future__ import annotations

import itertools
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from shootagent.config import Settings
from shootagent.creative import audience, evaluate as ev_mod, judges, novelty
from shootagent.creative.context import LabContext
from shootagent.creative.schema import (Beat, Ending, Hook, LabOptions, Music, PairwiseVote, Reel, ReelConcept,
                                        TimelineEntry)
from shootagent.store import Listing, ShootStore, View

_ids = itertools.count(1)


# ---- a fake Anthropic client --------------------------------------------------------------------------------


@dataclass
class _Block:
    type: str
    name: str = ""
    id: str = ""
    input: dict = field(default_factory=dict)
    text: str = ""


@dataclass
class _Usage:
    input_tokens: int = 1000
    output_tokens: int = 100
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Resp:
    content: list
    usage: _Usage
    stop_reason: str


def _texts(kw: dict) -> list[str]:
    out = [b["text"] for b in kw.get("system", []) if isinstance(b, dict)]
    for m in kw["messages"]:
        for b in m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b["text"])
    return out


def _template_names(kw: dict) -> set[str]:
    """Blind names whose frames show the template's text ("JUST LISTED")."""
    names, current = set(), None
    for t in _texts(kw):
        m = re.match(r"(Reel \d+): a vertical", t)
        if m:
            current = m.group(1)
        elif current and "JUST LISTED" in t:
            names.add(current)
    return names


class FakeClient:
    """Answers each `respond` call according to its schema; the "template" (frames saying JUST LISTED) loses
    everywhere. `fail` (a predicate on the request) raises instead."""

    def __init__(self, fail=None):
        self.messages = self
        self.requests: list[dict] = []
        self.fail = fail
        self._lock = threading.Lock()

    def create(self, **kw: Any) -> _Resp:
        snap = {**kw, "messages": [{**m, "content": list(m["content"]) if isinstance(m["content"], list) else m["content"]}
                                   for m in kw["messages"]]}
        with self._lock:
            self.requests.append(snap)
        if kw["max_tokens"] == 0:
            return _Resp(content=[], usage=_Usage(output_tokens=0, cache_creation_input_tokens=900), stop_reason="max_tokens")
        if self.fail and self.fail(kw):
            raise RuntimeError("simulated API failure")
        props = kw["tools"][0]["input_schema"]["properties"]
        text = "\n".join(_texts(kw))
        template = "JUST LISTED" in text
        if "first_impression" in props:
            data = ({"first_impression": "another just listed", "stopped": False, "watch_seconds": 0.8, "completed": False,
                     "rewatch": 40, "like": 30, "comment": 0, "share": 20, "save": 0, "follow": 0, "inquire": 0,
                     "comment_text": "", "share_note": "", "why": "Looks like every listing."} if template else
                    {"first_impression": "wait, the mirror?", "stopped": True, "watch_seconds": 99, "completed": True,
                     "rewatch": 50, "like": 70, "comment": 55, "share": 60, "save": 40, "follow": 10, "inquire": 5,
                     "comment_text": "the mirror got me", "share_note": "my sister", "why": "The mirror line made me stay."})
        elif "originality" in props:
            c = 3 if template else 8
            data = {"best_moment": "0.5 s: Reel 1 opens", "weakest_moment": "2.0 s: the end", "originality": c,
                    "surprise": c, "hook": c, "specificity": c, "craft": 6, "coherence": 6, "truthfulness": 9,
                    "creativity": c, "notes": "Reel 1 is what it is."}
        elif "winner" in props:
            options = props["winner"]["enum"]
            losers = _template_names(kw)
            winner = next((o for o in options if o not in losers), options[0])
            data = {"reason": f"{winner} has an idea.", "winner": winner}
        elif "verdict" in props:
            data = {"verdict": "Reel B is the most creative and would travel furthest.", "caveats": ["A tiny panel."]}
        else:
            raise AssertionError(f"unexpected schema {sorted(props)}")
        block = _Block(type="tool_use", name="respond", id=f"toolu_{next(_ids)}", input=data)
        return _Resp(content=[block], usage=_Usage(), stop_reason="tool_use")


# ---- fixtures -----------------------------------------------------------------------------------------------


def _video(path: Path, seconds: float = 2.5, fps: int = 10, shade: int = 90) -> None:
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (72, 128))
    assert w.isOpened()
    for i in range(round(seconds * fps)):
        w.write(np.full((128, 72, 3), (shade + 7 * i) % 255, np.uint8))
    w.release()


def _concept(cid: str, agent: str, hook: str, texts: list[str], ending: str, round_: int = 1, caption: str = "") -> ReelConcept:
    return ReelConcept(id=cid, agent=agent, round=round_, title=f"{cid} title", logline=f"{hook} and more", format=f"{cid} format",
                       hook=Hook(text=hook, style="bold_center"),
                       beats=[Beat(view=f"v0{i + 1}", seconds=1.0, motion="push_in", text=t, transition="crossfade" if agent == "template" else "cut")
                              for i, t in enumerate(texts)],
                       ending=Ending(style="end_card" if agent == "template" else "question", text=ending),
                       music=Music(mood="warm", bpm=90), caption=caption)


def _reel(rid: str, kind: str, concept: ReelConcept, label: str) -> Reel:
    tl = [TimelineEntry(t0=0, t1=1.5, kind="hook", beat=-1, text=concept.hook.text)]
    tl += [TimelineEntry(t0=i * 1.0, t1=(i + 1) * 1.0, kind="beat", beat=i, view=b.view, text=b.text)
           for i, b in enumerate(concept.beats)]
    tl.append(TimelineEntry(t0=2.0, t1=2.5, kind="ending", beat=-1, text=concept.ending.text))
    return Reel(id=rid, label=label, kind=kind, concept=concept.id, title=label, video=f"{rid.lower()}.mp4",
                poster=f"{rid.lower()}.jpg", seconds=2.5, timeline=tl, music="warm piano, 90 bpm")


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(novelty, "DEFAULT_METHOD", "tfidf")
    store = ShootStore(tmp_path / "shoots")
    store.create("Test shoot", shoot_id="s1")

    def setup(s):
        s.views.extend([View(id="v01", room="entry"), View(id="v02", room="kitchen"), View(id="v03", room="bedroom")])
        s.listing = Listing(address="Unit 4B, demo listing", price="$489,000", beds=1, baths=1, sqft=640)

    store.mutate("s1", setup)
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    _video(lab_dir / "a.mp4", shade=40)
    _video(lab_dir / "b.mp4", shade=160)
    template = _concept("template", "template", "JUST LISTED", ["Entry", "Kitchen", "Bedroom"], "SCHEDULE A TOUR",
                        round_=0, caption="Just listed! Book your tour today.")
    room = [_concept("c1", "contrarian", "The mirror is lying to you", ["Twice as long", "Loud on purpose", "Arched glow"],
                     "Which one would you keep?", caption="Look closer at the mirror."),
            _concept("c2", "comedian", "Rate this lantern", ["It glows", "It judges", "It stays"], "Your rating?")]
    single = [_concept("s01", "single_agent", "Small but mighty", ["Cozy", "Bright", "Calm"], "Tour it"),
              _concept("s02", "single_agent", "Welcome home", ["Kitchen goals", "Dream bedroom", "Light"], "DM me")]
    reels = [_reel("A", "baseline", template, "Template walkthrough"), _reel("B", "creative", room[0], "Showrunner's pick")]
    feed, progress = [], []
    settings = Settings(_env_file=None, data_dir=tmp_path, anthropic_api_key="test")

    def make(client: FakeClient) -> LabContext:
        return LabContext(store=store, shoot_id="s1", settings=settings, lab_dir=lab_dir, options=LabOptions(seed=3),
                          client=client, emit_fn=feed.append, progress_fn=lambda k, d: progress.append((k, d)))

    return {"make": make, "reels": reels, "concepts": [*room, template], "single": single, "feed": feed, "progress": progress}


# ---- agreement ----------------------------------------------------------------------------------------------


def test_kendall_w_known_cases():
    assert judges.kendall_w(np.array([[1, 2, 3, 4], [1, 2, 3, 4], [2, 3, 4, 5]])) == pytest.approx(1.0)
    assert judges.kendall_w(np.array([[1, 2, 3], [3, 2, 1]])) == pytest.approx(0.0)
    # Ties: ranks (1, 2, 3), (1, 2, 3), (1.5, 1.5, 3); S = 15.5; tie term 2^3 - 2 = 6; W = 12 * 15.5 / (9 * 24 - 3 * 6).
    assert judges.kendall_w(np.array([[1, 2, 3], [1, 2, 3], [2, 2, 3]])) == pytest.approx(186 / 198)
    assert judges.kendall_w(np.array([[5, 5], [5, 5]])) == 0.0  # every judge tied everything: undefined
    assert judges.kendall_w(np.array([[5], [6]])) == 0.0  # one item


def _vote(j, a, b, w):
    return PairwiseVote(judge=j, first=a, second=b, winner=w)


def test_agreement_order_consistency_and_win_rates():
    from shootagent.creative.schema import JudgeRating

    def r(j, reel, c):
        return JudgeRating(judge=j, reel=reel, originality=c, surprise=c, hook=c, specificity=c, craft=c, coherence=c,
                           truthfulness=c, creativity=c)

    ratings = [r("j1", "A", 3), r("j1", "B", 8), r("j2", "A", 4), r("j2", "B", 7), r("j3", "A", 6), r("j3", "B", 5)]
    votes = [_vote("j1", "A", "B", "B"), _vote("j1", "B", "A", "B"), _vote("j2", "A", "B", "A"), _vote("j2", "B", "A", "B")]
    agree = judges.agreement(ratings, votes, ["A", "B"])
    assert agree["kendall_w"] == pytest.approx(6 / 54, abs=1e-4)
    assert agree["order_consistency"] == 0.5 and agree["pairs_judged"] == 2
    assert agree["first_shown_wins"] == 0.75 and agree["judges"] == 3 and 0 <= agree["kendall_w_p"] <= 1
    assert judges.win_rates(votes) == {"A": 0.25, "B": 0.75}


def test_blind_names_are_neutral_shuffled_and_reversible():
    reels = [Reel(id=x, label="Template" if x == "A" else "x", kind="baseline", concept=x, title=x, video="", poster="",
                  seconds=1) for x in "ABCS"]
    names = judges.blind_names(reels, seed=7)
    assert sorted(names.values()) == ["Reel 1", "Reel 2", "Reel 3", "Reel 4"] and names == judges.blind_names(reels, 7)
    back = {v: k for k, v in names.items()}
    assert judges.unblind("reel 2 beats Reel 1", names) == f"Reel {back['Reel 2']} beats Reel {back['Reel 1']}"


# ---- viewers ------------------------------------------------------------------------------------------------


def test_panel_weights_and_frame_times():
    viewers = audience.default_viewers()
    assert 10 <= len(viewers) <= 12 and sum(v.weight for v in viewers) == pytest.approx(1.0)
    assert max(viewers, key=lambda v: v.weight).id == "v_scroller" and len({v.id for v in viewers}) == len(viewers)
    assert all(v.initials and v.color.startswith("#") for v in viewers)
    for seconds, n in ((24.05, 16), (12.0, 12), (30.0, 16)):
        t = audience.frame_times(seconds)
        assert t[:4] == [0.0, 0.5, 1.0, 1.5] and len(t) == n and t[-1] <= seconds
        gaps = np.diff(t[3:])
        assert np.all(gaps > 0) and gaps.min() >= 1.2 and gaps.max() <= 2.4
    assert audience.frame_times(2.0) == [0.0, 0.5, 1.0, 1.5]


def test_reactions_are_made_consistent():
    reel = Reel(id="A", label="x", kind="baseline", concept="t", title="x", video="", poster="", seconds=20)
    v = audience.default_viewers()[0]
    base = dict(first_impression="meh", comment_text="nice", share_note="mom", why="boring", rewatch=50, like=60,
                comment=40, share=30, save=20, follow=10, inquire=5)
    gone = audience.to_reaction(audience.ViewerAnswer(stopped=False, watch_seconds=7.0, completed=True, **base), v, reel)
    assert not gone.stopped and gone.watch_seconds <= 1.5 and not gone.completed
    assert (gone.like, gone.share, gone.rewatch, gone.comment_text) == (0, 0, 0, "")
    left = audience.to_reaction(audience.ViewerAnswer(stopped=True, watch_seconds=8.0, completed=False, **base), v, reel)
    assert left.stopped and left.watch_seconds == 8.0 and left.like == 60 and left.rewatch == 0
    done = audience.to_reaction(audience.ViewerAnswer(stopped=True, watch_seconds=13, completed=True, **{**base, "like": 140}), v, reel)
    assert done.completed and done.watch_seconds == 20 and done.like == 100 and done.rewatch == 50
    assert audience.reaction_note(gone, 20) == "Scrolled at 1.5 s: meh"  # watch clamped to the hook
    quick = audience.to_reaction(audience.ViewerAnswer(stopped=False, watch_seconds=0.8, completed=False, **base), v, reel)
    assert audience.reaction_note(quick, 20) == "Scrolled at 0.8 s: meh"


def test_prewarm_writes_exactly_the_prefix_the_viewers_reuse(lab):
    client = FakeClient()
    ctx = lab["make"](client)
    reel = lab["reels"][1]
    out = audience.watch(ctx, reel, ctx.lab_dir / reel.video, audience.default_viewers()[:3], caption="cap")
    assert len(out) == 3 and all(r.stopped and r.completed for r in out)
    warm = [r for r in client.requests if r["max_tokens"] == 0]
    real = [r for r in client.requests if r["max_tokens"] > 0]
    assert len(warm) == 1 and len(real) == 3
    prefix = warm[0]["messages"][0]["content"][:-1]
    assert prefix[-1]["cache_control"] == {"type": "ephemeral"}
    for r in real:
        for key in ("model", "tools", "system", "tool_choice", "output_config"):
            assert r[key] == warm[0][key], key
        assert r["messages"][0]["content"][:len(prefix)] == prefix
    images = [b for b in prefix if b["type"] == "image"]
    assert len(images) == len(audience.frame_times(reel.seconds))
    assert sum(1 for m in lab["feed"] if m.kind == "note") == 3


# ---- end to end ---------------------------------------------------------------------------------------------


def test_evaluate_end_to_end_blind(lab):
    client = FakeClient()
    ctx = lab["make"](client)
    ev = ev_mod.evaluate(ctx, lab["reels"], lab["concepts"], lab["single"])

    assert len(ev.viewers) == 11 and len(ev.reactions) == 22 and len(ev.judges) == 5
    assert len(ev.ratings) == 10 and len(ev.pairwise) == 10  # 5 judges x 2 reels; 5 judges x 2 orders
    s = {x.reel: x for x in ev.summaries}
    assert s["A"].stop_rate == 0 and s["A"].like_rate == 0 and s["B"].stop_rate == 1 and s["B"].completion_rate == 1
    assert s["B"].share_rate == pytest.approx(0.6) and s["B"].avg_watch_fraction == 1
    assert (s["A"].creativity, s["B"].creativity, s["B"].creativity_sd) == (3, 8, 0)
    assert (s["A"].pairwise_win_rate, s["B"].pairwise_win_rate) == (0, 1)
    assert ev.agreement["kendall_w"] == 1 and ev.agreement["order_consistency"] == 1 and ev.agreement["first_shown_wins"] == 0.5
    assert {(n.id, n.kind) for n in ev.novelty} >= {("A", "reel"), ("B", "reel"), ("template", "concept"), ("s01", "concept")}
    assert s["A"].novelty < s["B"].novelty
    assert set(ev.diversity) == {"room", "single_agent"} and ev.map and ev.map.method.startswith("char n-gram")
    assert ev.verdict == "Reel B is the most creative and would travel furthest."
    assert any("Kendall's W = 1.00" in n for n in ev.notes) and "A tiny panel." in ev.notes
    assert any(r.why for r in ev.reactions) and all(r.reel in ("A", "B") for r in ev.reactions)
    assert ev.ratings[0].best_moment.startswith("0.5 s: Reel ") and "Reel 1" not in " ".join(r.notes for r in ev.ratings)

    # The panel is blind: no viewer or judge request names the reel's label, kind or id.
    panel = [r for r in client.requests if "head judge" not in r["system"][0]["text"]]
    assert len(panel) == len(client.requests) - 1
    for r in panel:
        text = "\n".join(_texts(r))
        for leak in ("Template walkthrough", "Showrunner's pick", "baseline", "template format", "Reel A", "Reel B"):
            assert leak not in text, leak
    head = next(r for r in client.requests if "head judge" in r["system"][0]["text"])
    assert '"is": "template"' in "\n".join(_texts(head))  # unblinded only for the verdict

    # One cache warm-up per shared prefix: 2 reels to watch, 2 to rate, 2 ordered pairs.
    assert sum(r["max_tokens"] == 0 for r in client.requests) == 6

    kinds = {(m.agent, m.kind) for m in lab["feed"]}
    assert ("v_scroller", "note") in kinds and ("judge_cd", "critique") in kinds and ("judge_cd", "vote") in kinds
    assert ("head_judge", "note") in kinds
    assert any(m.text.startswith("Scrolled at 0.8 s: another just listed") for m in lab["feed"])
    assert any(m.text.startswith("Reel B over Reel A, in both orders.") for m in lab["feed"])
    order = [k for k, _ in lab["progress"]]
    firsts = [order.index(k) for k in ("watch", "judge", "novelty", "verdict")]
    assert firsts == sorted(firsts)
    assert ctx.usage.usage.calls == len(client.requests) and ctx.usage.usage.by_role.keys() == {"viewer", "judge"}


def test_evaluate_survives_failed_calls(lab):
    def fail(kw):
        texts = "\n".join(_texts(kw))
        return "You are Kai Brooks" in texts or "head judge" in kw["system"][0]["text"]

    ev = ev_mod.evaluate(lab["make"](FakeClient(fail=fail)), lab["reels"], lab["concepts"], lab["single"])
    assert len(ev.reactions) == 20 and not any(r.viewer == "v_genz" for r in ev.reactions)
    assert any("panel calls failed" in n for n in ev.notes)
    assert "Highest mean creativity: Reel B" in ev.verdict  # the deterministic fallback states the facts
    assert any("head judge failed" in n for n in ev.notes)


def test_a_vote_without_a_valid_winner_is_dropped_not_given_to_the_first_reel(lab):
    class NoWinner(FakeClient):
        def create(self, **kw):
            resp = super().create(**kw)
            if kw["max_tokens"] and "winner" in kw["tools"][0]["input_schema"]["properties"]:
                resp.content[0].input = {"reason": "Both.", "winner": "Reel 9"}
            return resp

    errors: list[str] = []
    ctx = lab["make"](NoWinner())
    frames = {r.id: audience.reel_frames(r, ctx.lab_dir / r.video) for r in lab["reels"]}
    ratings, votes, _ = judges.judge_reels(ctx, lab["reels"], frames, {}, "facts", seed=1,
                                           on_error=lambda what, e: errors.append(what))
    assert len(ratings) == 10 and votes == [] and len(errors) == 10 and "pair" in errors[0]


def test_unreadable_video_is_noted_and_nothing_is_invented(lab):
    reels = [r.model_copy(update={"video": "missing.mp4"}) for r in lab["reels"]]
    client = FakeClient()
    ev = ev_mod.evaluate(lab["make"](client), reels, lab["concepts"], lab["single"])
    assert ev.reactions == [] and ev.ratings == [] and all(s.stop_rate == 0 and s.creativity == 0 for s in ev.summaries)
    assert sum("could not be read" in n for n in ev.notes) == 2 and ev.verdict.startswith("No reel could be shown")
    assert client.requests == []  # no model call at all: not even the head judge


def test_brief_keeps_the_first_sentence():
    assert audience.brief("Short one. And a second sentence.") == "Short one."
    assert audience.brief('Its lines ("has a halo", "wavy. On purpose") work. Then it sags.') == \
        'Its lines ("has a halo", "wavy. On purpose") work.'
    assert audience.brief("It costs $489,000 vs. 2.5x more. Next.") == "It costs $489,000 vs. 2.5x more."
    assert audience.brief("x" * 300, 50).endswith("…") and len(audience.brief("x" * 300, 50)) == 50


def test_template_win_and_disagreement_are_stated(lab):
    reels = lab["reels"]
    summaries = [ev_mod.ReelSummary(reel="A", creativity=6.0, creativity_sd=1, pairwise_win_rate=0.7, novelty=0.1, stop_rate=0.3,
                                    avg_watch_fraction=0.3, completion_rate=0.1, like_rate=0.1, comment_rate=0, share_rate=0,
                                    save_rate=0, inquire_rate=0),
                 ev_mod.ReelSummary(reel="B", creativity=5.0, creativity_sd=3, pairwise_win_rate=0.3, novelty=0.8, stop_rate=0.4,
                                    avg_watch_fraction=0.5, completion_rate=0.2, like_rate=0.2, comment_rate=0.1, share_rate=0.1,
                                    save_rate=0.1, inquire_rate=0)]
    must, _ = ev_mod.verdict_facts(reels, summaries, {"kendall_w": 0.2, "judges": 5}, {"room": 0.3, "single_agent": 0.4}, [],
                                   {"c1"}, {"s01"})
    joined = " ".join(must)
    assert "No creative reel beat the template" in joined and "No creative reel won more pairwise" in joined
    assert "judges disagreed" in joined and "single agent's concepts were at least as diverse" in joined


def test_evaluation_agents_cover_every_poster():
    cards = ev_mod.evaluation_agents()
    ids = [c.id for c in cards]
    assert ids[:5] == [c.id for c in judges.judge_cards()] and ids[-1] == "head_judge"
    assert set(ids[5:-1]) == {v.id for v in audience.default_viewers()}
    assert {c.role for c in cards[:5]} == {"judge"} and {c.role for c in cards[5:-1]} == {"viewer"}
    assert all(c.model for c in cards)
