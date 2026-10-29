"""Launch simulator: how far would a reel travel in a short-video feed, given how the audience panel reacted?

A transparent staged-distribution model, not a forecast. The platform shows a new reel to a slice of the
creator's followers, measures how that wave engaged, and sizes the next wave from it; shares pull in extra
viewers, and later waves lean toward the audience segments that engaged most (the interest graph). Each viewer
persona is one segment of the audience, weighted by its share. Every input comes from the viewer agents'
reactions to this reel: the reel's kind, the judges' creativity scores and its novelty are not inputs, so two
reels with the same reactions get the same launch. Seeded numpy sampling makes a run reproducible, and
`Simulation.notes` lists every assumption so the dashboard can show them next to the curves.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .schema import Reaction, Reel, ReelSummary, RetentionPoint, SimComment, SimPoint, Simulation, ViewerPersona

REACH = 0.25  # share of followers the first wave reaches
HOOK_SECONDS = 1.5  # an impression that has not stopped by here scrolled past
STOP = (0.20, 0.70)  # impressions that watch past the hook: segment scrolled past .. segment stopped
COMPLETION = (0.05, 0.60)  # views that reach the end
# Per-view action rates at intent 0 and at intent 100, linear in between: typical short-video ranges.
RATES: dict[str, tuple[float, float]] = {
    "like": (0.02, 0.10),
    "comment": (0.001, 0.01),
    "share": (0.002, 0.02),
    "save": (0.002, 0.02),
    "follow": (0.0005, 0.005),
    "inquire": (0.0001, 0.003),
    "rewatch": (0.01, 0.25),
}
# What the platform ranks on: each signal per impression, relative to a reel whose signals all sit mid-range.
WEIGHTS = {"completion": 0.35, "rewatch": 0.15, "share": 0.25, "save": 0.15, "comment": 0.10}
MAX_MULTIPLIER = 3.0
SHARE_VIEWERS = (2, 6)  # people each share brings in, uniform
POOL_PER_FOLLOWER = 40  # the interest-graph audience a reel can reach, in followers
EXPLORE = 0.5  # share of each later wave split by audience weight; the rest follows last wave's engagement
COMMENT_LIKE = 0.004  # chance that a later view likes a given comment
RETENTION_STEP = 0.5  # seconds
MAX_WAVES = 16

_ACTIONS = tuple(RATES)
_POINT_FIELDS = {"likes": "like", "comments": "comment", "shares": "share", "saves": "save", "follows": "follow",
                 "inquiries": "inquire"}


@dataclass
class _Segment:
    id: str
    name: str
    weight: float
    stop: float  # P(view | impression)
    completion: float  # P(complete | view)
    rates: dict[str, float]  # P(action | view)
    watch: list[float]  # seconds watched, one per reaction, capped at the reel length
    comments: list[str]  # real comment text from this segment's reactions


@dataclass
class _Wave:
    start: float  # hour the wave starts landing
    length: float  # hours it takes to land
    impressions: np.ndarray  # per segment
    views: np.ndarray
    completions: np.ndarray
    actions: dict[str, np.ndarray]
    score: float
    multiplier: float


def _lerp(lo_hi: tuple[float, float], x: float) -> float:
    lo, hi = lo_hi
    return lo + (hi - lo) * min(1.0, max(0.0, float(x)))


def _segment(sid: str, name: str, weight: float, stopped: float, watch_fraction: float, completed: float,
             intents: dict[str, float], watch: list[float], comments: list[str]) -> _Segment:
    """`stopped`, `watch_fraction`, `completed` and each intent are 0-1 shares of the segment's reactions."""
    return _Segment(
        id=sid, name=name, weight=weight, stop=_lerp(STOP, stopped),
        completion=_lerp(COMPLETION, (completed + watch_fraction) / 2),
        rates={a: _lerp(RATES[a], intents.get(a, 0.0)) for a in _ACTIONS}, watch=watch, comments=comments,
    )


def _from_reactions(sid: str, name: str, weight: float, rs: list[Reaction], seconds: float) -> _Segment:
    n = len(rs)
    watch = [min(seconds, max(0.0, float(r.watch_seconds))) for r in rs]
    intents = {a: sum(min(100, max(0, getattr(r, a))) for r in rs) / (100 * n) for a in _ACTIONS}
    # A viewer with no intent to comment never posts their line, even when the segment's floor rate fires.
    said = [r.comment_text.strip() for r in rs if r.comment > 0 and r.comment_text and r.comment_text.strip()]
    return _segment(sid, name, weight, sum(r.stopped for r in rs) / n, sum(watch) / (n * seconds),
                    sum(r.completed for r in rs) / n, intents, watch, said)


def _from_summary(s: ReelSummary, seconds: float) -> _Segment:
    intents = {"like": s.like_rate, "comment": s.comment_rate, "share": s.share_rate, "save": s.save_rate,
               "inquire": s.inquire_rate}
    return _segment("audience", "Audience panel", 1.0, s.stop_rate, s.avg_watch_fraction, s.completion_rate, intents,
                    [min(seconds, max(0.0, s.avg_watch_fraction * seconds))], [])


def _has_signal(s: ReelSummary | None) -> bool:
    return s is not None and any((s.stop_rate, s.avg_watch_fraction, s.completion_rate, s.like_rate, s.comment_rate,
                                  s.share_rate, s.save_rate, s.inquire_rate))


def _audience(reel: Reel, summary: ReelSummary | None, reactions: list[Reaction], viewers: list[ViewerPersona],
              seconds: float) -> tuple[list[_Segment], list[str]]:
    """One segment per viewer persona with a reaction to this reel; the summary stands in when there are none."""
    mine = [r for r in reactions if r.reel == reel.id]
    if not mine and len({r.reel for r in reactions}) == 1:
        mine = list(reactions)  # one reel's reactions, filed under another id
    by_viewer: dict[str, list[Reaction]] = {}
    for r in mine:
        by_viewer.setdefault(r.viewer, []).append(r)
    known = [v.weight for v in viewers if v.weight > 0]
    fallback_weight = sum(known) / len(known) if known else 1.0
    segs, missing, notes = [], [], []
    for v in viewers:
        if v.id in by_viewer:
            segs.append(_from_reactions(v.id, v.name, max(0.0, v.weight), by_viewer.pop(v.id), seconds))
        else:
            missing.append(v.name)
    for vid, rs in by_viewer.items():  # reactions from viewers missing from the persona list
        segs.append(_from_reactions(vid, vid, fallback_weight, rs, seconds))
    if segs and missing:
        notes.append(f"No reaction from {', '.join(missing)}: left out of the audience mix.")
    if not segs and _has_signal(summary):
        segs = [_from_summary(summary, seconds)]
        notes.append("No individual reactions for this reel: one segment built from its panel summary.")
    total = sum(s.weight for s in segs)
    for s in segs:
        s.weight = s.weight / total if total > 0 else 1 / len(segs)
    return segs, notes


def _reference() -> dict[str, float]:
    """Per-impression signals of a reel whose every signal sits mid-range: engagement score 1.0."""
    stop = _lerp(STOP, 0.5)
    ref = {"completion": stop * _lerp(COMPLETION, 0.5)}
    ref.update({a: stop * _lerp(RATES[a], 0.5) for a in ("rewatch", "share", "save", "comment")})
    return ref


_REF = _reference()


def _score(impressions: np.ndarray, completions: np.ndarray, actions: dict[str, np.ndarray]) -> np.ndarray:
    """Engagement score per segment (per impression, so the hook counts); 0 where a segment had no impressions."""
    counts = {"completion": completions, **{a: actions[a] for a in ("rewatch", "share", "save", "comment")}}
    safe = np.maximum(impressions, 1)
    score = sum(WEIGHTS[k] * (counts[k] / safe) / _REF[k] for k in WEIGHTS)
    return np.where(impressions > 0, score, 0.0)


def _waves(segs: list[_Segment], followers: int, hours: int, rng: np.random.Generator) -> list[_Wave]:
    base = np.array([s.weight for s in segs])
    stop = np.array([s.stop for s in segs])
    completion = np.array([s.completion for s in segs])
    rates = {a: np.array([s.rates[a] for s in segs]) for a in _ACTIONS}
    pool = max(1, followers * POOL_PER_FOLLOWER)
    impressions = rng.multinomial(int(round(followers * REACH)), base)
    waves: list[_Wave] = []
    reached = 0
    for k in range(MAX_WAVES):
        start, length = 2.0**k - 1, 2.0**k  # 0-1 h, 1-3 h, 3-7 h, 7-15 h, ...
        total = int(impressions.sum())
        if start >= hours or total == 0:
            break
        views = rng.binomial(impressions, stop)
        completions = rng.binomial(views, completion)
        actions = {a: rng.binomial(views, rates[a]) for a in _ACTIONS}
        reached += total
        whole = _score(np.array([total]), np.array([completions.sum()]),
                       {a: np.array([v.sum()]) for a, v in actions.items()})
        score = float(whole[0])
        multiplier = min(MAX_MULTIPLIER, score)
        organic = int(round(total * multiplier * max(0.0, 1 - reached / pool)))
        tilt = base * _score(impressions, completions, actions)
        weights = base if tilt.sum() <= 0 else EXPLORE * base + (1 - EXPLORE) * tilt / tilt.sum()
        nxt = rng.multinomial(organic, weights / weights.sum())
        shared = np.array([int(rng.integers(SHARE_VIEWERS[0], SHARE_VIEWERS[1] + 1, size=int(n)).sum())
                           for n in actions["share"]])
        waves.append(_Wave(start, length, impressions, views, completions, actions, score, multiplier))
        impressions = nxt + shared  # a share's viewers resemble the sharer
    return waves


def _landed(w: _Wave, hour: float) -> float:
    """Share of a wave that has landed by `hour`: waves land evenly over their window."""
    return min(1.0, max(0.0, (hour - w.start) / w.length))


def _point(waves: list[_Wave], hour: float) -> SimPoint:
    fr = [_landed(w, hour) for w in waves]
    views = sum(int(np.floor(w.views * f).sum()) for w, f in zip(waves, fr))
    counts = {name: sum(int(np.floor(w.actions[a].sum() * f)) for w, f in zip(waves, fr))
              for name, a in _POINT_FIELDS.items()}
    return SimPoint(hour=float(hour), views=views, **counts)


def _retention(segs: list[_Segment], share: np.ndarray, seconds: float) -> list[RetentionPoint]:
    """Share of impressions still watching at each second: a segment's viewers who stop watch as long as its
    reactions did; the rest leave by the end of the hook."""
    ts = list(np.arange(0.0, seconds + 1e-9, RETENTION_STEP))
    if seconds - ts[-1] > 1e-6:
        ts.append(seconds)
    out = []
    for t in ts:
        frac = 0.0
        for s, w in zip(segs, share):
            stayed = sum(x >= t - 1e-9 for x in s.watch) / len(s.watch)
            hooked = sum(min(x, HOOK_SECONDS) >= t - 1e-9 for x in s.watch) / len(s.watch)
            frac += w * (s.stop * stayed + (1 - s.stop) * hooked)
        out.append(RetentionPoint(second=round(float(t), 2), fraction=round(float(frac), 4)))
    return out


def _comments(segs: list[_Segment], waves: list[_Wave], hours: int, total_views: int,
              rng: np.random.Generator) -> list[SimComment]:
    """Real comment text from the reactions, posted when that segment's simulated comments land."""
    out = []
    for i, s in enumerate(segs):
        texts = list(s.comments)
        for w in waves:
            n = int(w.actions["comment"][i])
            for j in range(min(n, len(texts))):
                hour = w.start + w.length * (j + 0.5) / n
                if hour > hours:
                    break
                later = max(0, total_views - _point(waves, hour).views)
                out.append(SimComment(hour=round(hour, 2), viewer=s.id, name=s.name, text=texts.pop(0),
                                      likes=int(rng.binomial(later, COMMENT_LIKE))))
            if not texts:
                break
    return sorted(out, key=lambda c: (c.hour, c.viewer))


def _pct(x: float) -> str:
    return f"{x * 100:.2f}".rstrip("0").rstrip(".") + "%"


def _notes(followers: int, waves: list[_Wave]) -> list[str]:
    rates = ", ".join(f"{a} {_pct(lo)}-{_pct(hi)}" for a, (lo, hi) in RATES.items())
    weights = " + ".join(f"{w:.2f} {k}" for k, w in WEIGHTS.items())
    out = [
        f"Wave 0 reaches {REACH:.0%} of {followers:,} followers. A view is an impression that watched past the "
        f"hook (the first {HOOK_SECONDS:g} s).",
        f"Per segment (one viewer persona, weighted by its audience share): {STOP[0]:.0%} of impressions stop if its "
        f"viewer scrolled past, {STOP[1]:.0%} if they stopped; {_pct(COMPLETION[0])}-{_pct(COMPLETION[1])} of views "
        "complete, from `completed` and watch_seconds / reel length.",
        f"Per-view action rates are linear in the 0-100 intents: {rates}. Every count is a binomial draw.",
        f"Engagement score = {weights}, each per impression and relative to a reel whose signals all sit mid-range "
        "(score 1.0 keeps the next wave the same size).",
        f"Next wave = this wave x score (capped at {MAX_MULTIPLIER:g}x) x the unreached share of an interest-graph "
        f"pool of {POOL_PER_FOLLOWER}x followers, plus {SHARE_VIEWERS[0]}-{SHARE_VIEWERS[1]} viewers per share "
        "from the sharer's segment.",
        f"Interest graph: {EXPLORE:.0%} of each later wave follows audience weight, the rest goes to segments in "
        "proportion to weight x their engagement in the previous wave.",
        "Wave k lands evenly over hours 2^k - 1 to 2^(k+1) - 1 (1 h, 2 h, 4 h, ...): fast early growth that "
        "flattens unless the score stays near 2.",
        "Only the panel's reactions to this reel go in: its kind, creativity scores and novelty do not. The same "
        "seed gives the same draws.",
    ]
    if waves:
        out.append("Waves (impressions, score): "
                   + ", ".join(f"{int(w.impressions.sum()):,} at {w.score:.2f}" for w in waves) + ".")
    return out


def simulate(reel: Reel, summary: ReelSummary, reactions: list[Reaction], viewers: list[ViewerPersona],
             followers: int = 8000, hours: int = 48, seed: int = 7) -> Simulation:
    """Simulate `hours` of a reel's launch to `followers` from its audience panel's reactions. Pure and seeded."""
    followers, hours = max(0, int(followers)), max(1, int(hours))
    seconds = max(float(reel.seconds or 0), 1.0)
    segs, notes = _audience(reel, summary, reactions, viewers, seconds)
    if not segs:
        zero = SimPoint(hour=float(hours), views=0, likes=0, comments=0, shares=0, saves=0, follows=0, inquiries=0)
        series = [zero.model_copy(update={"hour": float(h)}) for h in range(hours + 1)]
        return Simulation(reel=reel.id, followers=followers, hours=hours, seed=seed, series=series, totals=zero,
                          notes=["No audience reactions for this reel, so there is nothing to simulate."])
    rng = np.random.default_rng(seed)
    waves = _waves(segs, followers, hours, rng)
    series = [_point(waves, h) for h in range(hours + 1)]
    totals = series[-1]
    landed = [_landed(w, hours) for w in waves]
    by_segment = {s.id: int(sum(np.floor(w.views[i] * f) for w, f in zip(waves, landed))) for i, s in enumerate(segs)}
    share = np.array([sum(w.impressions[i] * f for w, f in zip(waves, landed)) for i in range(len(segs))], float)
    share = share / share.sum() if share.sum() > 0 else np.array([s.weight for s in segs])
    return Simulation(
        reel=reel.id, followers=followers, hours=hours, seed=seed, series=series,
        retention=_retention(segs, share, seconds), totals=totals,
        comments=_comments(segs, waves, hours, totals.views, rng), by_segment=by_segment,
        notes=_notes(followers, waves) + notes,
    )
