"""novelty.py: the deterministic measures, on the TF-IDF fallback so no model is downloaded."""
from __future__ import annotations

import json

import numpy as np
import pytest

from shootagent.creative import novelty as nv
from shootagent.creative.schema import Beat, CardSpec, Ending, Hook, Music, Reel, ReelConcept, TimelineEntry, Voice

ROOMS = {"v01": "entry hall", "v02": "kitchen", "v03": "living room", "v04": "entry", "v05": "dining nook",
         "v06": "dining and living", "v07": "bedroom", "v08": "bedroom"}
WALK = ["v01", "v04", "v03", "v06", "v02", "v05", "v07", "v08"]  # reel/spec.py showing order
LABEL = {"v01": "Entry Hall", "v02": "Kitchen", "v03": "Living Room", "v04": "Entry", "v05": "Dining Nook",
         "v06": "Dining And Living", "v07": "Bedroom", "v08": "Bedroom"}
MASK = ["Unit 4B, demo listing", "Unit 4B", "demo listing"]


@pytest.fixture(autouse=True)
def _tfidf(monkeypatch):
    monkeypatch.setattr(nv, "DEFAULT_METHOD", "tfidf")


def concept(cid: str, hook: str, beats: list[Beat], ending: Ending, fmt: str, logline: str, agent: str = "storyteller",
            round_: int = 1, voice: str = "none", hook_vo: str | None = None) -> ReelConcept:
    return ReelConcept(id=cid, agent=agent, round=round_, title=cid, logline=logline, format=fmt,
                       hook=Hook(text=hook, style="bold_center", voiceover=hook_vo), beats=beats, ending=ending,
                       music=Music(mood="warm", bpm=90), voice=Voice(voice=voice))


def template() -> ReelConcept:
    beats = [Beat(view=v, seconds=3, motion="push_in", transition="crossfade", text=LABEL[v], text_style="label",
                  card=CardSpec(kind="stat_count_up", value="640", text="sq ft") if i == 1 else None)
             for i, v in enumerate(WALK)]
    return concept("template", "Just listed: Unit 4B, $489,000", beats,
                   Ending(style="end_card", text="$489,000. Unit 4B. Schedule a tour."), "classic listing walkthrough",
                   "A room-by-room tour of Unit 4B with the price, the stats and a call to book a showing.",
                   agent="template", round_=0)


def original(cid: str = "c1", agent: str = "contrarian") -> ReelConcept:
    beats = [Beat(view="v07", seconds=2, motion="push_in", transition="cut", text="Arched. Backlit. Better than a headboard."),
             Beat(view="v01", seconds=1.5, motion="punch_in", transition="whip", text="The mirror makes this hall look twice as long"),
             Beat(view="v02", seconds=1.5, motion="push_in", transition="flash", text="We're the only loud thing here. On purpose.",
                  card=CardSpec(kind="price_tag", value="$489,000")),
             Beat(view="v05", seconds=2, motion="tilt_down", transition="cut", text="I glow at dinner. You're welcome.")]
    return concept(cid, "The mirror has been lying to you", beats,
                   Ending(style="question", text="Which one would you keep talking to?"),
                   "the furniture narrates the listing",
                   "The wavy mirror, the cobalt bowls and the paper lantern each confess one thing about life in 640 square feet.",
                   agent=agent, voice="sage", hook_vo="Don't trust the mirror.")


def hook_only(cid: str, text: str) -> ReelConcept:
    return concept(cid, text, [Beat(view="v03", seconds=2, motion="static")], Ending(style="end_card", text=""), "", "")


# ---- embeddings ---------------------------------------------------------------------------------------------


def test_tfidf_fallback_is_normalised_and_ranks_near_duplicates_first():
    texts = ["Just listed! Schedule a tour.", "Just listed!! Schedule your tour", "The mirror has been lying to you."]
    x = nv.tfidf_embed(texts)
    assert x.shape[0] == 3 and x.dtype == np.float32
    assert np.allclose(np.linalg.norm(x, axis=1), 1.0, atol=1e-5)
    sims = x @ x.T
    assert sims[0, 1] > 0.5 > sims[0, 2]


def test_embed_records_the_method():
    vec, method = nv.embed(["a b c", "d e f"], "tfidf")
    assert method == "tfidf" and vec.shape[0] == 2


# ---- the corpus ---------------------------------------------------------------------------------------------


def test_load_cliches_falls_back_and_reads_both_file_forms(tmp_path):
    items, source = nv.load_cliches(tmp_path / "missing.json")
    assert len(items) >= 55 and source.startswith("built-in") and all(i["text"] and i["kind"] for i in items)
    listed = tmp_path / "list.json"
    listed.write_text(json.dumps([{"text": "Just listed!", "kind": "hook", "source": "x"}, {"text": " "}]))
    assert nv.load_cliches(listed)[0] == [{"text": "Just listed!", "kind": "hook", "source": "x"}]
    wrapped = tmp_path / "dict.json"
    wrapped.write_text(json.dumps({"cliches": [{"text": "Welcome home"}]}))
    assert nv.load_cliches(wrapped)[0][0]["text"] == "Welcome home"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert nv.load_cliches(broken)[1].startswith("built-in")


# ---- lines and structure ------------------------------------------------------------------------------------


def test_facts_are_masked_and_labels_left_out():
    assert nv.mask_facts("Just listed: Unit 4B, $489,000", MASK) == "Just listed"
    assert not nv.scorable(nv.mask_facts("640 sq ft", MASK))
    assert nv.is_label("Dining And Living") and nv.is_label("Primary Bedroom 2") and nv.is_label(None)
    assert not nv.is_label("I glow at dinner. You're welcome.")
    lines = [t for t, _ in nv.concept_lines(template())]
    assert "Kitchen" not in lines and lines[0] == "Just listed: Unit 4B, $489,000"


def test_order_distance_known_cases():
    assert nv.order_distance([0, 1, 2, 3]) == 0.0
    assert nv.order_distance([3, 2, 1, 0]) == 1.0
    assert nv.order_distance([1, 0, 2]) == pytest.approx(1 / 3)
    assert nv.order_distance([2, 2, 2]) == 0.0  # same room throughout: no pair to order


def test_structure_novelty_separates_the_walkthrough_from_an_original_cut():
    t, o = template(), original()
    assert nv.structure_parts(t, ROOMS) == {"order": 0.0, "opener": 0.0, "text": 0.0, "devices": 0.25}
    parts = nv.structure_parts(o, ROOMS)
    assert parts["opener"] == 1.0 and parts["text"] == 1.0 and parts["devices"] == 1.0
    assert nv.structure_novelty(t, ROOMS) < 0.1 < 0.6 < nv.structure_novelty(o, ROOMS) <= 1.0


def test_reel_structure_from_timeline_alone():
    reel = Reel(id="A", label="x", kind="baseline", concept="unknown", title="x", video="a.mp4", poster="a.jpg", seconds=6,
                timeline=[TimelineEntry(t0=0, t1=3, kind="beat", beat=0, view="v01", text="Entry Hall"),
                          TimelineEntry(t0=3, t1=6, kind="beat", beat=1, view="v02", text="Kitchen"),
                          TimelineEntry(t0=5, t1=6, kind="ending", beat=-1, text="Schedule a tour")])
    assert nv.reel_structure_novelty(reel, ROOMS) == 0.0


# ---- scores, calibration, diversity, map --------------------------------------------------------------------


def test_template_scores_low_and_original_high_and_cliche_hook_below_original_hook():
    concepts = [template(), original(), hook_only("cliche", "Just listed! Your dream home awaits."),
                hook_only("fresh", "This hallway mirror has been lying to you.")]
    rep = nv.measure(concepts, [], [], ROOMS, mask=MASK)
    s = {x.id: x for x in rep.scores}
    assert rep.method == "tfidf" and rep.anchors["stock"] > rep.anchors["fresh"]
    assert s["template"].novelty < 0.3 < 0.6 < s["c1"].novelty
    assert s["cliche"].novelty < s["fresh"].novelty
    assert s["cliche"].cliche_similarity > s["fresh"].cliche_similarity
    assert s["template"].nearest_cliche  # names the cliche it sits closest to
    assert all(0.0 <= x.novelty <= 1.0 and 0.0 <= x.structure_novelty <= 1.0 for x in rep.scores)


def test_reels_are_scored_from_their_timeline():
    o = original()
    reel = Reel(id="B", label="x", kind="creative", concept=o.id, title="x", video="b.mp4", poster="b.jpg", seconds=7,
                timeline=[TimelineEntry(t0=0, t1=1.5, kind="hook", beat=-1, text=o.hook.text, voiceover=o.hook.voiceover),
                          TimelineEntry(t0=0, t1=2, kind="beat", beat=0, view="v07", text=o.beats[0].text),
                          TimelineEntry(t0=6, t1=7, kind="ending", beat=-1, text=o.ending.text)])
    rep = nv.measure([template(), o], [reel], [], ROOMS, mask=MASK)
    r = next(x for x in rep.scores if x.kind == "reel")
    assert r.id == "B" and r.structure_novelty == nv.structure_novelty(o, ROOMS) and r.novelty > 0.5
    assert o.hook.text in r.text


def test_diversity_at_equal_n():
    a = np.eye(4, dtype=np.float32)  # four orthogonal concepts: distance 1
    b = np.repeat(np.eye(4, dtype=np.float32)[:1], 6, axis=0)  # six identical concepts: distance 0
    d = nv.diversity(a, b, seed=1)
    assert d == {"room": 1.0, "single_agent": 0.0}
    assert nv.diversity(a, b[:1]) == {"room": 1.0}  # too few single-agent concepts: no entry
    assert nv.diversity(a, b, seed=3) == nv.diversity(a, b, seed=3)


def test_pca_shapes():
    rng = np.random.default_rng(0)
    assert nv.pca_2d(rng.normal(size=(10, 5))).shape == (10, 2)
    assert nv.pca_2d(rng.normal(size=(1, 5))).shape == (1, 2)
    assert nv.pca_2d(rng.normal(size=(2, 5))).shape == (2, 2)  # one component: padded
    line = np.outer(np.arange(6.0), [1.0, 2.0, 0.5])
    xy = nv.pca_2d(line)
    assert np.allclose(xy[:, 1], 0, atol=1e-9) and np.ptp(xy[:, 0]) > 0


def test_map_has_every_cliche_and_concept_with_kinds():
    room = [original("c1"), original("c2", agent="comedian"), original("c3", agent="detective")]
    single = [original(f"s{i}", agent="single_agent") for i in range(3)]
    finalist = Reel(id="B", label="x", kind="creative", concept="c2", title="x", video="b.mp4", poster="b.jpg", seconds=7)
    rep = nv.measure([template(), *room], [finalist], single, ROOMS, mask=MASK)
    kinds = {p.id: p.kind for p in rep.map.points}
    n_cliches = len(nv.load_cliches()[0])
    assert len(rep.map.points) == n_cliches + 7 and rep.map.method == "char n-gram tf-idf + PCA"
    assert kinds["template"] == "baseline" and kinds["c2"] == "finalist" and kinds["c1"] == "concept"
    assert kinds["s0"] == "single_agent" and sum(k == "cliche" for k in kinds.values()) == n_cliches
    assert all(-1.0 <= p.x <= 1.0 and -1.0 <= p.y <= 1.0 for p in rep.map.points)
    assert set(rep.diversity) == {"room", "single_agent"}
    concept_points = [p for p in rep.map.points if p.kind != "cliche"]
    assert all(p.agent and p.round is not None for p in concept_points)
