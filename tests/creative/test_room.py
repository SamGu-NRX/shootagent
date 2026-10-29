"""The writers' room and its baselines on a scripted client: no network. The fake answers every `respond` call from
the request itself (which agent is asking, which schema it asks for, what is on the wall), so the room runs end to
end: brief, pitch, critique, remix and steal, re-check and vote, greenlight; plus the single-agent control and
the template."""
from __future__ import annotations

import itertools
import json
import re
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from shootagent.config import Settings
from shootagent.creative import baseline, room
from shootagent.creative.context import LabContext
from shootagent.creative.llm import strict_schema
from shootagent.creative.schema import LabOptions, PropertyBrief
from shootagent.store import Listing, ShootStore, View

ROOMS = {"v01": ("living room", (60, 90)), "v02": ("kitchen", (90, 60)), "v03": ("entry", (90, 60)),
         "v04": ("bedroom", (90, 60)), "v05": ("dining", (60, 90)), "v06": ("bedroom", (60, 90))}
LISTING = Listing(address="Unit 9, test listing", price="$300,000", beds=1, baths=1, sqft=500)
GOOD = ["v01", "v02", "v03", "v04", "v05"]


NO_CARD = dict(kind="none", value="", text="", items=[], highlight="")


def card(kind: str, **kw) -> dict:
    return {**NO_CARD, "kind": kind, **kw}


def draft(title: str, views: list[str], *, drop: int = 2, voice: str = "none", vo: str = "") -> dict:
    """A concept in the compact draft shape the creators answer with ('' / [] / -1 for none)."""
    beats = [dict(view=v, seconds=2.5, motion="push_in", focus=[0.5, 1.4], transition="cut", text="Look at this",
                  text_style="pop_words", text_pos="bottom", voiceover=vo, card=NO_CARD, sfx="none") for v in views]
    return dict(title=title, format="spot the detail", logline="A detail hunt.", rationale="The bowls (v02).",
                audience="people who cook", hook=dict(text="Nobody notices the thing on this counter", style="pop_words",
                                                      voiceover="", sfx="pop"),
                beats=beats, ending=dict(style="loop", text="Again?", voiceover=""),
                music=dict(mood="suspense", bpm=200, drop_beat=drop), voice=dict(voice=voice, direction=""),
                caption="Look closer.", hashtags=["#tag one", "detail"], claims=["500 sq ft"])


def _text(messages: list) -> str:
    last = messages[-1]["content"]
    return "\n".join(b["text"] for b in last if isinstance(b, dict) and b.get("type") == "text")


def _who(messages: list) -> str:
    """The persona an agent was given in its first message ('You are the Storyteller.')."""
    first = messages[0]["content"]
    for b in first if isinstance(first, list) else []:
        m = re.search(r"^You are (?:the |The )?(.+?)\.$", b.get("text", ""), re.M) if isinstance(b, dict) else None
        if m:
            return m.group(1)
    return "?"


def _ids(text: str, pattern: str) -> list[str]:
    m = re.search(pattern, text)
    return re.findall(r"c\d\d", m.group(1)) if m else []


class FakeClient:
    """Plays every agent: answers by the requested schema and the asking agent's system prompt."""

    def __init__(self, n_single: int = 5):
        self.messages = self
        self.calls: list[dict] = []
        self.n_single = n_single
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def create(self, **kw):
        with self._lock:
            self.calls.append({**kw, "messages": list(kw["messages"])})  # snapshot: a conversation keeps growing
        usage = SimpleNamespace(input_tokens=1000, output_tokens=0, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if kw["max_tokens"] == 0:  # a cache pre-warm
            return SimpleNamespace(content=[], usage=usage, stop_reason="max_tokens")
        props = set(kw["tools"][0]["input_schema"]["properties"])
        answer = self.answer(props, kw["system"][0]["text"], _text(kw["messages"]), _who(kw["messages"]))
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", name="respond", id=f"toolu_{next(self._ids)}", input=answer)],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
            stop_reason="tool_use")

    def answer(self, props: set[str], system: str, text: str, who: str) -> dict:
        if "note_to_room" in props:
            return dict(summary="A small warm flat.", tone="warm", note_to_room="Build on the bowls; nobody claims a view.",
                        hero_features=[dict(view="V2", feature="blue bowls", why="colour"),
                                       dict(view="kitchen", feature="stone", why="texture"),
                                       dict(view="v99", feature="a ghost", why="none"),
                                       dict(view="bedroom", feature="stripes", why="ambiguous room")],
                        facts=["500 sq ft", " "], avoid_claims=["no views"], audiences=["people who cook"])
        if "angles" in props:
            one, two = draft(f"{who} one", GOOD), draft(f"{who} two", GOOD[::-1])
            if who == "Storyteller":
                two = draft("Storyteller two", ["v01", "v77", "v88"])  # one real photo: rejected
            if who == "Contrarian":
                one = draft("Contrarian one", ["v01", "V2", "kitchen", "v99", "v03"], drop=4)
            return dict(obvious=["a walkthrough"], angles=["x"] * 5,
                        concepts=[{**one, "pitch": f"{who}: pitch one"}, {**two, "pitch": f"{who}: pitch two"}])
        if "moves" in props:
            own = _ids(text, r"yours are ([c\d ,and]+)\.")
            dealt = _ids(text, r"dealt you: ([c\d ,and]+)\.")
            source = "c99" if who == "Detail Detective" else dealt[0]  # a bad id; the note names the real one
            steal_title = "flagme steal" if who == "Comedian" else f"{who} steal"
            return dict(moves=[
                dict(move="revise", source=own[0].upper(), own="", concept=draft(f"{who} revised", GOOD),
                     note=f"Revising {own[0]}: cut the cliche."),
                dict(move="steal", source=source, own=own[-1], concept=draft(steal_title, GOOD[1:]),
                     note=f"Taking the twist from {dealt[0]} and making it mine.")])
        if "critiques" in props:
            ids = list(dict.fromkeys(re.findall(r"^(c\d\d) \"", text, re.M))) or _ids(text, r"review (c[\d, c]+)")
            flagged = set(re.findall(r"^(c\d\d) \"flagme", text, re.M))
            critiques = [dict(concept=i, originality=4 + int(i[1:]) % 6, hook=6, specificity=7, truth=9, virality=6,
                              verdict="revise", notes=f"{who} on {i}.", cliche_flags=[],
                              truth_flags=["claims a view"] if who == "Fact Checker" and i in flagged else [])
                         for i in ids if not (who == "Algorithm" and i == ids[-1] and "summary" in props)]
            if "summary" not in props:
                return dict(critiques=critiques)
            return dict(critiques=critiques, summary=f"{who}: {ids[0]} works, {ids[-1]} does not.",
                        strongest=ids[0], strongest_why="It lands.", weakest=ids[-1], weakest_why="It drags.")
        if "picks" in props:
            flagged = re.findall(r"^(c\d\d) \"flagme", text, re.M)
            ids = list(dict.fromkeys(re.findall(r"^(c\d\d) \"", text, re.M)))
            return dict(picks=[dict(concept=c, reason=f"Because {c}.") for c in (flagged + ids)[:2]],
                        passed_over=f"{ids[-1]} was close.")
        if "working alone" in system and "obvious" in props:
            concepts = [draft(f"solo {i}", GOOD) for i in range(1, self.n_single + 1)]
            concepts[1] = draft("solo broken", ["v42", "v43", "v01"])
            return dict(obvious=["a walkthrough"], concepts=concepts)
        if "working alone" in system:
            return dict(concept="S2", why="The boldest.")
        if {"concept", "why"} <= props:
            if "is not a concept you can vote for" in text:
                return dict(concept=re.search(r"one of (c\d\d)", text).group(1), why="Fine, that one.")
            return dict(concept=_ids(text, r"Yours are ([c\d ,and]+);")[0], why="Mine is best.")  # not allowed
        raise AssertionError(f"unexpected request: {sorted(props)}")


def make_ctx(tmp_path, client, **options) -> tuple[LabContext, list, list]:
    settings = Settings(_env_file=None, data_dir=tmp_path, anthropic_api_key="test", creative_workers=4)
    store = ShootStore(settings.shoots_dir)
    shoot = store.create("Test flat", shoot_id="s1")
    rng = np.random.default_rng(0)

    def setup(s):
        s.listing = LISTING
        s.views.extend(View(id=v, room=room_name, label=room_name.title(), approved=True)
                       for v, (room_name, _) in ROOMS.items())

    store.mutate(shoot.id, setup)
    for v, (_, (h, w)) in ROOMS.items():
        store.add_version(shoot.id, v, rng.integers(40, 200, (h, w, 3), dtype=np.uint8), op="ingest")
    feed, progress = [], []
    ctx = LabContext(store=store, shoot_id=shoot.id, settings=settings, lab_dir=tmp_path / "lab",
                     options=LabOptions(**options), client=client, emit_fn=feed.append,
                     progress_fn=lambda key, detail: progress.append((key, detail)),
                     publish_fn=lambda kind, items: published.append((kind, items)))
    ctx.published = published = []
    return ctx, feed, progress


def _breakpoints(kw: dict) -> int:
    blocks = list(kw["system"]) + [b for m in kw["messages"] if isinstance(m["content"], list)
                                   for b in m["content"] if isinstance(b, dict)]
    return sum(1 for b in blocks if "cache_control" in b)


def test_room_end_to_end(tmp_path):
    client = FakeClient()
    ctx, feed, progress = make_ctx(tmp_path, client)
    brief = room.write_brief(ctx)
    assert [(f.view, f.feature) for f in brief.hero_features] == [("v02", "blue bowls"), ("v02", "stone")]
    assert brief.facts == ["500 sq ft"]
    assert [m.kind for m in feed] == ["brief"] and "bowls" in feed[0].text

    result = room.run_room(ctx, brief)
    wall = {c.id: c for c in result.concepts}
    assert list(wall) == [f"c{i:02d}" for i in range(1, len(wall) + 1)]
    r1 = [c for c in result.concepts if c.round == 1]
    r2 = [c for c in result.concepts if c.round == 2]
    assert len(r1) == 11 and len(r2) == 12  # the Storyteller's second pitch had one real photo
    assert any(m.kind == "system" and "Storyteller two" in m.text for m in feed)

    contrarian = next(c for c in r1 if c.title == "Contrarian one")
    assert [b.view for b in contrarian.beats] == ["v01", "v02", "v02", "v03"]  # V2 and 'kitchen' mapped, v99 dropped
    assert contrarian.music.drop_beat == 3 and contrarian.music.bpm == 150
    for c in result.concepts:
        assert 12 <= c.seconds <= 35 and len(c.hook.text.split()) <= 10
        assert all(b.view in ROOMS and b.focus[1] <= 1.0 and b.text_style == "pop_words" for b in c.beats)
        assert c.hashtags == ["tagone", "detail"]

    remixes = [kw for kw in client.calls if "moves" in kw["tools"][0]["input_schema"]["properties"]]
    hands = {_who(kw["messages"]): _ids(_text(kw["messages"]), r"dealt you: ([c\d ,and]+)\.") for kw in remixes}
    dealt = [i for hand in hands.values() for i in hand]
    assert all(len(h) == 2 for h in hands.values()) and set(dealt) == {c.id for c in r1}  # the steals cover the wall
    assert max(dealt.count(i) for i in dealt) <= 2
    for c in r2:
        mine = {x.id for x in r1 if x.agent == c.agent}
        if c.title.endswith("revised"):
            assert len(c.parents) == 1 and c.parents[0] in mine and wall[c.parents[0]].status == "revised"
        else:
            assert c.parents[0] not in mine and wall[c.parents[0]].round == 1 and c.parents[1:] == [max(mine)]
            assert c.parents[0] in next(h for who, h in hands.items() if c.title.startswith(who) or
                                        (who == "Comedian" and c.title == "flagme steal"))
    detective = next(c for c in r2 if c.title == "Detail Detective steal")
    assert detective.parents[0] != "c99"  # the bad id was replaced by the one the note names

    assert len(result.critiques) == 3 * (len(r1) + len(r2))  # the Algorithm's missed concept was asked for again
    flagged = next(c for c in r2 if c.title == "flagme steal")
    assert len(result.finalists) == 2 and flagged.id not in result.finalists
    assert all(wall[f].status == "finalist" and wall[f].round == 2 for f in result.finalists)
    assert len({wall[f].agent for f in result.finalists}) == 2
    assert {c.status for c in result.concepts if c.id not in result.finalists} <= {"killed", "revised"}

    kinds = [m.kind for m in feed if m.kind != "system"]
    first, last = (lambda k: kinds.index(k)), (lambda k: len(kinds) - 1 - kinds[::-1].index(k))
    assert kinds[0] == "brief" and last("pitch") < first("critique") < first("remix")
    assert last("critique") > first("remix")  # round 2 is re-checked after the remix
    assert max(last("remix"), last("steal")) < first("vote") and last("vote") < first("greenlight")
    assert kinds[-3:] == ["greenlight"] * 3  # pick, wildcard, the one passed over
    assert sum(1 for k in kinds if k == "critique") == 3 * 3 * 2  # summary, strongest, weakest; per critic, per round
    for m in feed:
        if m.kind == "vote":
            assert wall[m.concept].agent != m.agent and wall[m.concept].round == 2
    assert [k for k, _ in progress][:2] == ["brief", "brief"]
    assert {k for k, _ in progress} == {"brief", "pitch", "critique", "remix", "greenlight"}
    assert {a.id for a in result.agents} >= {"showrunner", "storyteller", "fact_checker", "algorithm"}
    # Work reaches the lab as it lands: round-1 concepts, critiques, round-2 concepts, round-2 critiques, then every
    # concept with its final status and the finalists.
    kinds = [k for i, (k, _) in enumerate(ctx.published) if i == 0 or ctx.published[i - 1][0] != k]
    assert kinds == ["concepts", "critiques", "concepts", "critiques", "concepts", "finalists"]
    runs, start = [], 0
    for i, (k, _) in enumerate(ctx.published + [("end", [])]):
        if i and k != ctx.published[i - 1][0]:
            runs.append([x for _, items in ctx.published[start:i] for x in items])
            start = i
    assert {c.round for c in runs[0]} == {1} and {c.status for c in runs[0]} == {"pitched"}
    assert len(runs[1]) == 3 * len(r1) and len(runs[3]) == 3 * len(r2)
    assert {c.round for c in runs[2] if c.status != "revised"} == {2}
    assert {c.id: c.status for c in runs[4][-len(wall):]} == {c.id: c.status for c in result.concepts}
    assert runs[5] == result.finalists
    assert all(x is not wall.get(getattr(x, "id", None)) for x in runs[0] + runs[4])  # snapshots, not live objects
    assert ctx.usage.usage.calls == len(client.calls)
    assert all(_breakpoints(kw) <= 4 for kw in client.calls)
    warm = [kw for kw in client.calls if kw["max_tokens"] == 0]
    assert len(warm) == 2  # the creators' and the critics' shared openings
    for kw in warm:  # each real request starts with exactly the pre-warmed prefix
        twins = [c for c in client.calls if c["max_tokens"] and c["system"] == kw["system"] and c["tools"] == kw["tools"]
                 and c["messages"][0]["content"][:len(kw["messages"][0]["content"])] == kw["messages"][0]["content"]]
        assert len(twins) >= 3


def test_one_round_votes_on_the_pitches(tmp_path):
    ctx, feed, progress = make_ctx(tmp_path, FakeClient(), rounds=1, finalists=1, creators=["comedian", "Contrarian"])
    brief = PropertyBrief(summary="A flat.")
    result = room.run_room(ctx, brief)
    assert {c.round for c in result.concepts} == {1} and {c.agent for c in result.concepts} == {"comedian", "contrarian"}
    assert not any(m.kind in ("remix", "steal") for m in feed)
    assert len(result.finalists) == 1 and sum(m.kind == "vote" for m in feed) == 2
    assert [a.id for a in result.agents if a.role == "creator"] == ["contrarian", "comedian"]
    assert "remix" not in {k for k, _ in progress}


def test_single_agent_control(tmp_path):
    ctx, feed, progress = make_ctx(tmp_path, FakeClient(n_single=5))
    concepts, pick = baseline.single_agent_concepts(ctx, PropertyBrief(summary="A flat."), n=4)
    assert [c.id for c in concepts] == ["s01", "s02", "s03"]  # 4 asked for, one unusable
    assert pick == "s02" and all(c.agent == "single_agent" and c.round == 1 for c in concepts)
    assert [m.agent for m in feed] == ["single_agent"] * len(feed) and feed[-1].concept == "s02"
    assert [(k, [c.id for c in items]) for k, items in ctx.published] == [("concepts", ["s01", "s02", "s03"])]
    assert progress[0][0] == progress[-1][0] == "single_agent"
    calls = ctx.client.calls
    assert len(calls) == 2
    assert "Shared by every creator" in calls[0]["system"][0]["text"]  # the room's craft notes, word for word


def test_template_is_conventional_and_deterministic(tmp_path):
    ctx, _, _ = make_ctx(tmp_path, client=None)
    a, b = baseline.template_concept(ctx), baseline.template_concept(ctx)
    assert a == b and a.id == a.agent == "template"
    assert a.hook.text == "Just listed: Unit 9, test listing · $300,000" and a.hook.style == "bold_center"
    assert [b.view for b in a.beats] == ["v03", "v01", "v02", "v05", "v04", "v06"]  # entry, living, kitchen, dining, bed
    assert all(b.text_style == "label" for b in a.beats) and a.beats[0].text == "Entry"
    assert [b.transition for b in a.beats][1:] == ["crossfade"] * 5
    assert [b.motion for b in a.beats][:4] == ["push_in", "pan_right", "push_in", "pan_left"]
    assert a.beats[1].card.kind == "stat_count_up" and a.beats[1].card.text == "1 bed · 1 bath · 500 sq ft"
    assert 20 <= a.seconds <= 26 and a.music.mood == "warm" and a.music.bpm == 84 and a.voice.voice == "none"
    assert a.ending.style == "end_card" and a.ending.text == "Schedule a tour"


def test_repair_fits_voice_cards_and_length():
    photos = {v: room.Photo(v, r, "portrait", "") for v, (r, _) in ROOMS.items()}
    d = room.ConceptDraft.model_validate(draft("A title", ["v01", "v02", "v03"], voice="onyx",
                                               vo="one two three four five six seven eight nine ten eleven twelve"))
    d.hook.text = "one two three four five six seven eight nine ten eleven and"
    d.beats[1].voiceover = " ".join(["word"] * 30)
    d.beats[0].card = room.CardDraft(**card("list_reveal", items=["only one"]))
    d.beats[1].card = room.CardDraft(**card("price_tag"))
    d.beats[2].card = room.CardDraft(**card("big_quote", text="The arch is the point", highlight="window"))
    c, notes = room.repair(d, photos, LISTING)
    assert c.hook.text == "one two three four five six seven eight nine ten"
    assert c.beats[0].card is None and c.beats[1].card.value == "$300,000" and c.beats[2].card.highlight is None
    assert len(c.beats[1].voiceover.split()) == 15 and c.beats[1].seconds > 5.9  # the longest line a beat can hold
    for b in c.beats:  # every line fits its beat at 2.6 words a second
        assert len(b.voiceover.split()) / room.WORDS_PER_SECOND <= b.seconds
    assert any("list card" in n for n in notes)

    long = room.ConceptDraft.model_validate(draft("Long", GOOD * 3))
    for b in long.beats:
        b.seconds = 6
    assert room.repair(long, photos, LISTING)[0].seconds == pytest.approx(35, abs=0.01)
    short = room.ConceptDraft.model_validate(draft("Short", GOOD[:3]))
    for b in short.beats:
        b.seconds, b.text = 0.7, ""
    assert room.repair(short, photos, LISTING)[0].seconds == pytest.approx(12, abs=0.01)
    silent = room.ConceptDraft.model_validate(draft("Silent", GOOD, vo="a line"))
    assert all(b.voiceover is None for b in room.repair(silent, photos, LISTING)[0].beats)  # voice none
    assert room.repair(room.ConceptDraft.model_validate(draft("Bad", ["v01", "x", "bedroom"])), photos, LISTING)[0] is None


def test_ids_and_views_are_normalised():
    photos = {v: room.Photo(v, r, "portrait", "") for v, (r, _) in ROOMS.items()}
    assert [room.match_view(x, photos) for x in ("v03", "V3", "3", "view 3", "Kitchen", "bedroom", "v9")] == \
        ["v03", "v03", "v03", "v03", "v02", None, None]
    assert [room.norm_id(x) for x in ("c07", "C7", "#c07", "7", "concept c12", None)] == \
        ["c07", "c07", "c07", "c07", "c12", None]
    assert room.norm_id("S2", prefix="s") == "s02"


def test_concept_schemas_have_no_nullable_fields():
    """The API compiles each strict tool schema into a grammar with a size cap. A list of concepts written with the
    contract's nullable fields is refused ("The compiled grammar is too large"); the union-free drafts fit."""
    for model in (room.PitchSet, room.RemixSet, baseline.SingleSet):
        assert "anyOf" not in json.dumps(strict_schema(model)), model.__name__


def test_basics_on_screen():
    photos = {v: room.Photo(v, r, "portrait", "") for v, (r, _) in ROOMS.items()}
    c = room.repair(room.ConceptDraft.model_validate(draft("Decor only", GOOD)), photos, LISTING)[0]
    assert room.basics_on_screen(c, LISTING) == (False, False)
    c.beats[1].text, c.beats[2].text = "All of this for $300K", "1 bed, 1 bath"
    assert room.basics_on_screen(c, LISTING) == (True, True)
    c.beats[1].text, c.beats[2].text = None, "500 square feet of it"
    c.ending.style = "price_reveal"
    assert room.basics_on_screen(c, LISTING) == (True, True)


def test_text_keeps_sublines_and_chat_replies():
    assert room.trim_words("640 sq ft\nevery  inch built in", 10) == "640 sq ft\nevery inch built in"
    assert room.trim_words("Verdict: *annoyingly* well done | 1 bed · 1 bath and more words here", 8) == \
        "Verdict: *annoyingly* well done | 1 bed · 1 bath"
    photos = {v: room.Photo(v, r, "portrait", "") for v, (r, _) in ROOMS.items()}
    d = room.ConceptDraft.model_validate(draft("Chat", GOOD))
    d.beats[0].card = room.CardDraft(**card("typing_chat", text="which hook is mine", value="the middle one"))
    d.beats[1].card = room.CardDraft(**card("typing_chat", text="and the bench?", items=["yours"]))
    d.beats[2].text = "Built in\nfrom the door to the bed"
    c = room.repair(d, photos, LISTING)[0]
    assert (c.beats[0].card.text, c.beats[0].card.value) == ("which hook is mine", "the middle one")
    assert c.beats[1].card.value == "yours" and c.beats[2].text == "Built in\nfrom the door to the bed"
