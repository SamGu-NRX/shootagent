"""The launch simulator (creative/simulate.py): hourly cumulative counts, reproducible draws, reactions as the
only input, and decay for a reel nobody engages with."""

from __future__ import annotations

from shootagent.creative.schema import Reaction, Reel, ReelSummary, ViewerPersona
from shootagent.creative.simulate import simulate

VIEWERS = [ViewerPersona(id=f"p{i}", name=f"Persona {i}", segment="s", weight=w, bio="")
           for i, w in enumerate([0.3, 0.25, 0.2, 0.15, 0.1])]
FIELDS = ("views", "likes", "comments", "shares", "saves", "follows", "inquiries")


def reel(rid: str = "B", kind: str = "creative", seconds: float = 20.0) -> Reel:
    return Reel(id=rid, label="Showrunner's pick", kind=kind, concept="c1", title="T", video="reels/B.mp4",
                poster="reels/B.jpg", seconds=seconds)


def summary(rid: str = "B", **kw) -> ReelSummary:
    base = dict(creativity=0, creativity_sd=0, pairwise_win_rate=0, novelty=0, stop_rate=0, avg_watch_fraction=0,
                completion_rate=0, like_rate=0, comment_rate=0, share_rate=0, save_rate=0, inquire_rate=0)
    return ReelSummary(reel=rid, **{**base, **kw})


def reactions(rid: str, stopped, watch, completed, intent, comments: bool = True) -> list[Reaction]:
    return [Reaction(viewer=v.id, reel=rid, stopped=bool(stopped[i]), first_impression="", watch_seconds=watch[i],
                     completed=bool(completed[i]), rewatch=intent[i], like=intent[i], comment=intent[i],
                     share=intent[i], save=intent[i], follow=intent[i] // 2, inquire=intent[i] // 3,
                     comment_text=f"comment from {v.name}" if comments else "")
            for i, v in enumerate(VIEWERS)]


STRONG = dict(stopped=[1, 1, 1, 1, 0], watch=[20, 20, 18, 15, 1], completed=[1, 1, 0, 0, 0], intent=[70, 60, 50, 40, 10])
MID = dict(stopped=[1, 1, 0, 1, 0], watch=[20, 10, 1, 8, 1], completed=[1, 0, 0, 0, 0], intent=[50, 40, 20, 40, 10])
WEAK = dict(stopped=[0, 1, 0, 0, 0], watch=[1, 6, 1, 1, 1], completed=[0, 0, 0, 0, 0], intent=[20, 20, 10, 10, 5])
ZERO = dict(stopped=[0] * 5, watch=[0.5] * 5, completed=[0] * 5, intent=[0] * 5)


def run(case: dict, rid: str = "B", **kw):
    return simulate(reel(rid), summary(rid), reactions(rid, **case), VIEWERS, **kw)


def test_series_is_hourly_cumulative_and_monotone():
    sim = run(STRONG, hours=48)
    assert [p.hour for p in sim.series] == [float(h) for h in range(49)]
    assert all(getattr(sim.series[0], f) == 0 for f in FIELDS)
    for f in FIELDS:
        values = [getattr(p, f) for p in sim.series]
        assert values == sorted(values), f
    assert sim.series[-1].views > 0 and sim.followers == 8000 and sim.hours == 48 and sim.seed == 7


def test_same_inputs_give_the_same_launch_and_the_seed_changes_the_draws():
    a, b = run(MID, seed=11), run(MID, seed=11)
    assert a.model_dump() == b.model_dump()
    assert run(MID, seed=12).series != a.series


def test_better_reactions_travel_further():
    for seed in range(1, 6):
        strong, mid, weak = (run(c, seed=seed).totals for c in (STRONG, MID, WEAK))
        assert strong.views > mid.views > weak.views
        assert strong.shares > weak.shares and strong.inquiries >= weak.inquiries


def test_a_reel_nobody_engages_with_decays():
    sim = run(ZERO)
    first_hour = sim.series[1].views
    assert first_hour > 0  # the followers' wave still lands
    assert sim.totals.views < 1.25 * first_hour
    assert sim.totals.views - sim.series[24].views <= max(2, first_hour // 100)  # the second day adds ~nothing
    assert sim.comments == []


def test_totals_are_the_last_point_and_segments_add_up():
    for case in (STRONG, WEAK):
        sim = run(case, hours=30)
        assert sim.totals == sim.series[-1] and sim.totals.hour == 30
        assert set(sim.by_segment) == {v.id for v in VIEWERS}
        assert sum(sim.by_segment.values()) == sim.totals.views


def test_reel_kind_and_label_do_not_enter():
    rs = reactions("B", **MID)
    creative = simulate(reel("B", "creative"), summary(), rs, VIEWERS)
    baseline = simulate(reel("B", "baseline").model_copy(update={"label": "Template", "title": "Other"}),
                        summary(creativity=9.5, novelty=1.0, pairwise_win_rate=1.0), rs, VIEWERS)
    assert creative.model_dump() == baseline.model_dump()


def test_more_followers_reach_more_people():
    assert run(MID, followers=40_000).totals.views > run(MID, followers=8000).totals.views


def test_retention_starts_full_and_only_falls():
    sim = run(MID)
    fractions = [p.fraction for p in sim.retention]
    assert sim.retention[0].second == 0 and fractions[0] == 1.0
    assert fractions == sorted(fractions, reverse=True)
    assert sim.retention[-1].second == 20.0 and 0 < fractions[-1] < 1


def test_comments_are_the_panels_own_words_in_time_order():
    sim = run(STRONG)
    texts = {f"comment from {v.name}" for v in VIEWERS}
    assert sim.comments and all(c.text in texts for c in sim.comments)
    assert len({c.text for c in sim.comments}) == len(sim.comments)
    hours = [c.hour for c in sim.comments]
    assert hours == sorted(hours) and all(0 < h <= 48 for h in hours)
    assert all(c.name.startswith("Persona") and c.likes >= 0 for c in sim.comments)
    silent = run(STRONG | {"intent": [0] * 5})
    assert silent.totals.comments > 0 and silent.comments == []  # floor-rate comments, but nobody said a line


def test_only_this_reels_reactions_count():
    mixed = reactions("A", **WEAK) + reactions("B", **STRONG)
    assert simulate(reel("B"), summary(), mixed, VIEWERS).totals == run(STRONG).totals


def test_a_reel_without_reactions_uses_its_summary_or_stays_flat():
    flat = simulate(reel(), summary(), [], VIEWERS)
    assert flat.totals.views == 0 and len(flat.series) == 49 and flat.notes
    from_summary = simulate(reel(), summary(stop_rate=0.8, avg_watch_fraction=0.7, completion_rate=0.5,
                                            like_rate=0.5, share_rate=0.4), [], VIEWERS)
    assert from_summary.totals.views > 0 and list(from_summary.by_segment) == ["audience"]


def test_notes_document_the_assumptions():
    notes = " ".join(run(MID).notes)
    for needle in ("25%", "like 2%-10%", "comment 0.1%-1%", "share 0.2%-2%", "save 0.2%-2%", "follow 0.05%-0.5%",
                   "inquire 0.01%-0.3%", "0.35 completion", "0.25 share", "2-6 viewers per share", "Waves"):
        assert needle in notes, needle
