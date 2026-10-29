"""The creativity judges: Amabile's Consensual Assessment Technique with five independent expert agents.

CAT measures creativity as what domain experts agree on when each judges on their own. Five judges with
different expertise (a viral short-form strategist, a real-estate marketing director, a film editor, an
ad-agency creative director and a skeptical cultural critic) each rate every reel blind: the reels are shown
under shuffled neutral names ("Reel 1"), nobody is told how a reel was made, and each rating is a fresh call
that sees one reel (frames, on-screen text and the spoken words). Ratings are 1-10 on originality, surprise,
hook, specificity, craft, coherence, truthfulness and overall creativity, plus the best and weakest moment.

Absolute ratings drift, so each judge also compares every pair of reels, in both orders: a judge who picks
whichever reel came first (position bias) shows up as a split. Agreement is Kendall's W over the judges'
creativity rankings (tie-corrected), and the share of judge-pairs that picked the same winner in both orders.

As with the audience, the reel blocks come first and the judge's persona last, so the five judges share one
cached prefix per reel (and per ordered pair).
"""
from __future__ import annotations

import re
import threading
from collections import defaultdict
from collections.abc import Callable
from itertools import permutations
from random import Random
from typing import Any

import numpy as np
from pydantic import BaseModel, Field
from scipy.stats import chi2, rankdata

from .audience import Frame, Prewarmer, brief, reel_blocks
from .context import LabContext
from .llm import ask_once, pmap, text_block
from .schema import AgentCard, JudgeRating, PairwiseVote, Reel

PAIR_LONG_EDGE = 512  # two reels per request: smaller frames
LOW_AGREEMENT = 0.5  # Kendall's W below this: the judges did not agree on a ranking

_JUDGES: list[tuple[AgentCard, str]] = [
    (AgentCard(id="judge_viral", name="Nadia Cole", role="judge", color="#FF375F", initials="NC",
               lens="Viral short-form strategist: judges the first 1.5 seconds, hold, loops and whether people would "
                    "send it on"),
     "You have grown accounts to millions of followers on TikTok and Reels and can tell in a second whether a hook "
     "works. You reward a reason to keep watching and a reason to send it to someone; you punish slow openers, "
     "filler and anything that looks like an ad. Novelty matters to you only when it holds attention."),
    (AgentCard(id="judge_listing", name="Robert Hale", role="judge", color="#0A84FF", initials="RH",
               lens="Real-estate marketing director: knows every listing-video template and what sells a home "
                    "truthfully"),
     "You approve the marketing for hundreds of listings a year at a large brokerage. You know the 'just listed' "
     "template by heart and are tired of it, but a reel must still make a buyer understand and want this specific "
     "home, and must never misrepresent it. You reward specificity and truth; you punish gimmicks that hide the "
     "home."),
    (AgentCard(id="judge_editor", name="Mina Sato", role="judge", color="#30D158", initials="MS",
               lens="Film editor for commercials and music videos: judges pacing, cut rhythm, motion, type timing and "
                    "whether the edit serves the idea"),
     "You notice where every cut lands, whether text stays up long enough to read, and whether moves and "
     "transitions mean something or are decoration. You respect rhythm and restraint; you dislike templates, "
     "random effects and edits that fight the idea."),
    (AgentCard(id="judge_cd", name="Luis Ferreira", role="judge", color="#FF9F0A", initials="LF",
               lens="Ad-agency creative director and awards juror: asks what the idea is, and whether it is "
                    "single-minded, fresh and well made"),
     "You can state any good piece of work's idea in one sentence; if you cannot, there is no idea. You reward a "
     "fresh, simple idea carried out with conviction and true to the product; you dismiss borrowed interest, "
     "trends pasted on, and cleverness that has nothing to do with the home."),
    (AgentCard(id="judge_critic", name="Ada Lindqvist", role="judge", color="#BF5AF2", initials="AL",
               lens="Skeptical critic of internet culture: allergic to trend-chasing, fake quirkiness and AI slop; "
                    "credits only genuine wit"),
     "You write about why most online content feels the same. You have seen every 'POV:' and every talking-object "
     "gimmick, and you can tell machine-made filler from a real idea. You are hard to impress and say so plainly, "
     "but you give full credit when something is genuinely new, funny or true."),
]

JUDGE_SYSTEM = """You are one of five independent expert judges on a Consensual Assessment Technique panel: experts each rate creative work on their own, without conferring, and their agreement is measured afterwards. The work is short vertical listing reels for one home. The facts about the home: {facts}. The photos in the reel are of this home.

You judge one reel, blind: you are not told who made it, how or why, and you should not guess. You see it as a sequence of still frames, each introduced by its time, the text on screen and the words spoken from that moment; the sound is described in words. The line gives the words on screen; their size and legibility are in the frame. Judge the reel that is there, not the one it could have been.

Rate each dimension from 1 to 10 and use the whole scale:
- originality: how unlike the usual listing reel the idea is. A standard walkthrough ("just listed", room names, slow moves, price and "schedule a tour") is 1-3 however polished it is; 8 or more means you have not seen this approach to a listing before.
- surprise: something unexpected that still makes sense in hindsight.
- hook: how strongly the first 1.5 seconds would stop a stranger's scroll.
- specificity: how much it is about this home (its actual features, details and facts) rather than any home.
- craft: execution: timing, legibility, rhythm, framing, and sound and text working together.
- coherence: one clear idea from start to finish, with an ending that pays off the opening.
- truthfulness: whether every claim and implication matches the photos and the facts. 10 means nothing misleading; deduct for invented features, false scarcity and claims the photos do not support.
- creativity: your overall judgement of how creative the reel is, meaning novel and appropriate together: a novel reel that misrepresents the home or loses the viewer is less creative, not more.
Anchors: 5 is a competent, forgettable reel; 8 is one you would show colleagues as an example; 10 is the best listing reel you have seen. Most reels are 3-6.
Then give the best and the weakest moment, each starting with its time in seconds, and a verdict of at most two sentences in your own voice. Refer to the reel by its name."""

PAIR_SYSTEM = """You are one of five independent expert judges comparing short vertical listing reels for one home, two at a time and blind: you are not told who made them, how or why. The facts about the home: {facts}. The photos in the reels are of this home.

You see each reel as a sequence of still frames, each introduced by its time, the text on screen and the words spoken from that moment; the sound is described in words. Decide which of the two is more creative: more original and surprising while still true to the home, specific to it, coherent, and well carried out. The order the reels are shown in, their length and the amount of text are not reasons. You must pick one. Refer to the reels by their names."""


class RatingAnswer(BaseModel):
    best_moment: str = Field(description="The strongest moment, starting with its time, e.g. '3.4 s: ...'. One sentence.")
    weakest_moment: str = Field(description="The weakest moment, starting with its time. One sentence.")
    originality: int = Field(description="1-10")
    surprise: int = Field(description="1-10")
    hook: int = Field(description="1-10")
    specificity: int = Field(description="1-10")
    craft: int = Field(description="1-10")
    coherence: int = Field(description="1-10")
    truthfulness: int = Field(description="1-10")
    creativity: int = Field(description="1-10: your overall judgement of how creative the reel is.")
    notes: str = Field(description="Your verdict in your own voice, at most two sentences.")


def pair_schema(first: str, second: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "One or two sentences in your own voice: what decides it."},
            "winner": {"type": "string", "enum": [first, second], "description": "The more creative of the two."},
        },
        "required": ["reason", "winner"],
        "additionalProperties": False,
    }


def judge_cards() -> list[AgentCard]:
    return [card.model_copy() for card, _ in _JUDGES]


def judge_persona(card: AgentCard) -> str:
    return next(p for c, p in _JUDGES if c.id == card.id)


def blind_names(reels: list[Reel], seed: int) -> dict[str, str]:
    """Reel id -> "Reel N" in a seeded shuffle, so names say nothing about kind or order of creation."""
    order = [r.id for r in reels]
    Random(seed).shuffle(order)
    return {rid: f"Reel {i + 1}" for i, rid in enumerate(order)}


def unblind(text: str, names: dict[str, str]) -> str:
    """Replace blind names in a judge's words with the reels' real ids ("Reel 2" -> "Reel B")."""
    ids = {name.split()[-1]: rid for rid, name in names.items()}
    return re.sub(r"\bReel (\d+)\b", lambda m: f"Reel {ids.get(m.group(1), m.group(1))}", text, flags=re.IGNORECASE)


def _score(x: int) -> int:
    return int(min(10, max(1, int(x))))


# ---- agreement ----------------------------------------------------------------------------------------------


def kendall_w(scores: np.ndarray) -> float:
    """Kendall's coefficient of concordance for m judges (rows) scoring n items (columns), tie-corrected:
    W = 12 S / (m^2 (n^3 - n) - m sum(T)), with S the squared deviations of the items' rank sums from their mean
    and T = sum(t^3 - t) over each judge's groups of tied scores. 1 = identical rankings, 0 = no agreement.
    0 when undefined (fewer than two items, or every judge tied every item)."""
    scores = np.asarray(scores, dtype=float)
    if scores.ndim != 2 or scores.shape[0] == 0 or scores.shape[1] < 2:
        return 0.0
    m, n = scores.shape
    ranks = np.vstack([rankdata(row) for row in scores])
    sums = ranks.sum(0)
    s = float(((sums - sums.mean()) ** 2).sum())
    ties = sum(float((c ** 3 - c).sum()) for c in (np.unique(row, return_counts=True)[1] for row in scores))
    denom = m * m * (n ** 3 - n) - m * ties
    return float(12 * s / denom) if denom > 0 else 0.0


def order_consistency(votes: list[PairwiseVote]) -> tuple[float, int]:
    """(share of judge-pairs judged in both orders whose winner did not change with the order, that count)."""
    seen: dict[tuple[str, frozenset], dict[str, str]] = defaultdict(dict)
    for v in votes:
        seen[(v.judge, frozenset((v.first, v.second)))][v.first] = v.winner
    both = [d for d in seen.values() if len(d) == 2]
    return (sum(len(set(d.values())) == 1 for d in both) / len(both) if both else 0.0), len(both)


def agreement(ratings: list[JudgeRating], votes: list[PairwiseVote], reel_ids: list[str]) -> dict[str, float]:
    """Kendall's W over the judges who rated every reel (with its chi-square p-value), order consistency, and
    how often the first-shown reel won."""
    by_judge: dict[str, dict[str, int]] = defaultdict(dict)
    for r in ratings:
        by_judge[r.judge][r.reel] = r.creativity
    rows = [[d[rid] for rid in reel_ids] for d in by_judge.values() if all(rid in d for rid in reel_ids)]
    w = kendall_w(np.array(rows)) if rows else 0.0
    m, n = len(rows), len(reel_ids)
    consistent, pairs = order_consistency(votes)
    out = {"kendall_w": round(w, 4), "judges": float(m), "reels": float(n),
           "order_consistency": round(consistent, 4), "pairs_judged": float(pairs)}
    if m >= 2 and n >= 2:
        out["kendall_w_p"] = round(float(chi2.sf(m * (n - 1) * w, n - 1)), 4)
    if votes:
        out["first_shown_wins"] = round(sum(v.winner == v.first for v in votes) / len(votes), 4)
    return out


def win_rates(votes: list[PairwiseVote]) -> dict[str, float]:
    wins: dict[str, int] = defaultdict(int)
    seen: dict[str, int] = defaultdict(int)
    for v in votes:
        seen[v.first] += 1
        seen[v.second] += 1
        wins[v.winner] += 1
    return {rid: wins[rid] / n for rid, n in seen.items()}


# ---- judging ------------------------------------------------------------------------------------------------


def rate_one(ctx: LabContext, judge: AgentCard, name: str, system: str, shared: list[dict]) -> RatingAnswer:
    prompt = f"You are {judge.name}. {judge_persona(judge)}\nRate {name} on your own, using the whole scale."
    return ask_once(ctx, judge.id, "judge", system, [*shared, text_block(prompt)], RatingAnswer,
                    model=ctx.settings.creative_judge_model, effort=ctx.settings.creative_judge_effort)


def compare_one(ctx: LabContext, judge: AgentCard, first: str, second: str, system: str, shared: list[dict]) -> dict:
    prompt = (f"You are {judge.name}. {judge_persona(judge)}\nWhich is more creative, {first} (shown first) or "
              f"{second} (shown second)?")
    return ask_once(ctx, judge.id, "judge", system, [*shared, text_block(prompt)], pair_schema(first, second),
                    model=ctx.settings.creative_judge_model, effort=ctx.settings.creative_judge_effort)


def judge_reels(ctx: LabContext, reels: list[Reel], frames: dict[str, list[Frame]], captions: dict[str, str],
                facts: str, seed: int, judges: list[AgentCard] | None = None, prewarm: Prewarmer | None = None,
                on_done: Callable[[int, int], Any] | None = None,
                on_error: Callable[[str, Exception], Any] | None = None) -> tuple[list[JudgeRating], list[PairwiseVote], dict[str, str]]:
    """Every judge rates every reel and compares every ordered pair, all blind and in parallel. Ratings and
    pair verdicts go to the feed as they arrive. Returns (ratings, votes, blind names). A failed call is
    reported to `on_error` and left out."""
    judges = judges if judges is not None else judge_cards()
    names = blind_names(reels, seed)
    by_name = {name: rid for rid, name in names.items()}
    rate_system, pair_system = JUDGE_SYSTEM.format(facts=facts), PAIR_SYSTEM.format(facts=facts)
    single = {r.id: reel_blocks(r, frames[r.id], captions.get(r.id), name=names[r.id]) for r in reels}
    halves = {r.id: reel_blocks(r, frames[r.id], captions.get(r.id), name=names[r.id], long_edge=PAIR_LONG_EDGE, cache=False)
              for r in reels}
    reel_by_id = {r.id: r for r in reels}
    model, effort = ctx.settings.creative_judge_model, ctx.settings.creative_judge_effort
    warm = prewarm or Prewarmer(ctx)
    lock = threading.Lock()
    pending: dict[tuple[str, frozenset], PairwiseVote] = {}
    done = [0]
    tasks: list[tuple[str, AgentCard, tuple[str, ...]]] = [("rate", j, (r.id,)) for r in reels for j in judges]
    tasks += [("pair", j, (a.id, b.id)) for a, b in permutations(reels, 2) for j in judges]

    def pair_blocks(a: str, b: str) -> list[dict]:
        blocks = [text_block(f"Two reels for the same home: {names[a]} is shown first, then {names[b]}."),
                  *halves[a], *halves[b]]
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        return blocks

    pairs = {(a.id, b.id): pair_blocks(a.id, b.id) for a, b in permutations(reels, 2)}

    def run(task: tuple[str, AgentCard, tuple[str, ...]]) -> JudgeRating | PairwiseVote:
        kind, judge, ids = task
        if kind == "rate":
            (rid,) = ids
            warm.ensure(f"rate:{rid}", "judge", rate_system, RatingAnswer, single[rid], model, effort)
            a = rate_one(ctx, judge, names[rid], rate_system, single[rid])
            result: JudgeRating | PairwiseVote = JudgeRating(
                judge=judge.id, reel=rid, originality=_score(a.originality), surprise=_score(a.surprise),
                hook=_score(a.hook), specificity=_score(a.specificity), craft=_score(a.craft),
                coherence=_score(a.coherence), truthfulness=_score(a.truthfulness), creativity=_score(a.creativity),
                best_moment=unblind(a.best_moment.strip(), names), weakest_moment=unblind(a.weakest_moment.strip(), names),
                notes=unblind(a.notes.strip(), names))
            ctx.emit(judge.id, "critique", f"Reel {rid}: {result.creativity}/10. {brief(result.notes)}", concept=reel_by_id[rid].concept)
        else:
            a_id, b_id = ids
            schema = pair_schema(names[a_id], names[b_id])
            warm.ensure(f"pair:{a_id}>{b_id}", "judge", pair_system, schema, pairs[ids], model, effort)
            ans = compare_one(ctx, judge, names[a_id], names[b_id], pair_system, pairs[ids])
            winner = by_name.get(str(ans.get("winner")))
            if winner not in ids:  # never default to a side: that would read as position bias
                raise ValueError(f"{judge.id} named no valid winner ({ans.get('winner')!r})")
            result = PairwiseVote(judge=judge.id, first=a_id, second=b_id, winner=winner,
                                  reason=unblind(str(ans.get("reason", "")).strip(), names))
            with lock:
                other = pending.pop((judge.id, frozenset(ids)), None)
                if other is None:
                    pending[(judge.id, frozenset(ids))] = result
            if other is not None:
                ctx.emit(judge.id, "vote", _pair_note(other, result), concept=reel_by_id[result.winner].concept)
        with lock:
            done[0] += 1
            n = done[0]
        if on_done:
            on_done(n, len(tasks))
        return result

    def failed(task: tuple[str, AgentCard, tuple[str, ...]], e: Exception) -> None:
        if on_error:
            on_error(f"{task[1].id} {task[0]} {'/'.join(task[2])}", e)
        return None

    results = [x for x in pmap(ctx, run, tasks, on_error=failed) if x is not None]
    return ([x for x in results if isinstance(x, JudgeRating)], [x for x in results if isinstance(x, PairwiseVote)], names)


def _pair_note(a: PairwiseVote, b: PairwiseVote) -> str:
    """One judge's two votes on one pair, as a feed line."""
    x, y = sorted((a.first, a.second))
    if a.winner == b.winner:
        loser = y if a.winner == x else x
        return f"Reel {a.winner} over Reel {loser}, in both orders. {brief(b.reason)}".strip()
    picked_first = a.winner == a.first
    return (f"Split on Reel {x} vs Reel {y}: picked whichever came {'first' if picked_first else 'second'}. "
            f"{brief(b.reason)}").strip()
