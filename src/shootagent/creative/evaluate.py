"""The evaluation network: an independent panel that watches the rendered reels and measures, honestly, how
creative they are and how far they would travel.

Nothing in the panel knows which reel is the template: viewers and judges see only frames, on-screen text,
spoken words, the sound described in words and the post caption, under neutral names. The phases, reported
with ctx.progress:
- watch: 11 viewer agents (audience.py) react to every reel the way people scroll.
- judge: 5 expert judges (judges.py) rate every reel and compare every pair in both orders.
- novelty: deterministic measures (novelty.py): distance from a cliche corpus, structure, the room-versus-
  single-agent diversity, and the creativity map.
- verdict: a head judge that never sees the reels reads only the numbers and quotes and writes one paragraph.
  It is handed the facts it must state when they hold (a creative reel did not win, the judges disagreed, the
  single agent matched or beat the room); the same facts go into `notes`, so they survive any wording.

Rates in each ReelSummary are means over the viewers who reacted, weighted by their audience share. Summary
novelty is the mean of the reel's text novelty and structure novelty.
"""
from __future__ import annotations

import threading
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from ..config import get_settings
from ..store import Listing
from . import novelty
from .audience import Frame, Prewarmer, default_viewers, reel_frames, watch
from .context import LabContext
from .judges import LOW_AGREEMENT, agreement, judge_cards, judge_reels, win_rates
from .llm import ask_once, dumps
from .schema import (AgentCard, Evaluation, JudgeRating, NoveltyScore, PairwiseVote, Reaction, Reel, ReelConcept,
                     ReelSummary, ViewerPersona)

HEAD_JUDGE = AgentCard(id="head_judge", name="Head judge", role="judge", color="#3A3A3C", initials="HJ",
                       lens="Never watches the reels: reads only the panel's numbers and quotes, writes the verdict, "
                            "and says plainly when the creative reels lost or the judges disagreed")

HEAD_SYSTEM = """You are the head judge of an evaluation panel for short listing reels. You did not watch the reels. You read only what the panel measured: eleven simulated viewers (each one slice of a real Instagram and TikTok audience, weighted by its share), five independent expert judges who rated the reels blind and compared every pair in both orders, and deterministic novelty scores (distance from a corpus of real-estate cliches, and from the standard walkthrough structure).

Write the verdict as one paragraph of at most 130 words, in plain sentences: which reel is most creative (the judges' creativity scores and pairwise wins first, novelty as supporting evidence), which reel the audience panel suggests would travel furthest (stop rate, watch time and completion, then shares and saves), and why, quoting a viewer or a judge. Refer to reels by id and say what each is (template, creative or single agent). Use only the numbers given; do not invent any.

Honesty comes first. Every statement under "must_state" is true and belongs in the paragraph, said plainly, without softening or burying it: if no creative reel beat the template, say so; if the judges disagreed, say the ranking is unsettled; if the single agent matched or beat the multi-agent room, say so. Then list up to two short caveats a careful reader should know that the numbers raise and that the paragraph does not already cover."""


class HeadAnswer(BaseModel):
    verdict: str = Field(description="One paragraph, at most 130 words.")
    caveats: list[str] = Field(description="Up to two short caveats, or an empty list.")


def evaluation_agents() -> list[AgentCard]:
    """Every agent the evaluation posts as: the judges, the viewers (same ids as the ViewerPersonas) and the head
    judge, so feed messages resolve before results exist."""
    s = get_settings()
    judges = [c.model_copy(update={"model": s.creative_judge_model}) for c in judge_cards()]
    viewers = [AgentCard(id=v.id, name=v.name, role="viewer", lens=v.segment, color=v.color, initials=v.initials,
                         model=s.audience_model) for v in default_viewers()]
    return [*judges, *viewers, HEAD_JUDGE.model_copy(update={"model": s.creative_judge_model})]


# ---- inputs -------------------------------------------------------------------------------------------------


def listing_facts(listing: Listing) -> str:
    parts = [listing.address, listing.price,
             f"{listing.beds:g} bed" if listing.beds else None, f"{listing.baths:g} bath" if listing.baths else None,
             f"{listing.sqft:,} sq ft" if listing.sqft else None]
    return "; ".join(p for p in parts if p) or "no listing facts given"


def caption_for(concept: ReelConcept | None) -> str:
    if concept is None:
        return ""
    tags = " ".join("#" + t.lstrip("#") for t in concept.hashtags if t.strip())
    return " ".join(x for x in (concept.caption.strip(), tags) if x)


def _address_parts(listing: Listing) -> list[str]:
    a = (listing.address or "").strip()
    return [a, *(p.strip() for p in a.split(","))] if a else []


# ---- summaries ----------------------------------------------------------------------------------------------


def summarize(reel: Reel, reactions: list[Reaction], ratings: list[JudgeRating], wins: dict[str, float],
              score: NoveltyScore | None, weights: dict[str, float]) -> ReelSummary:
    mine = [r for r in reactions if r.reel == reel.id]
    w = np.array([weights.get(r.viewer, 0.0) for r in mine], dtype=float)
    if mine and w.sum() <= 0:
        w = np.ones(len(mine))

    def rate(values: list[float]) -> float:
        return round(float((np.array(values, dtype=float) * w).sum() / w.sum()), 4) if mine else 0.0

    length = max(reel.seconds, 0.1)
    creativity = [r.creativity for r in ratings if r.reel == reel.id]
    return ReelSummary(
        reel=reel.id,
        creativity=round(float(np.mean(creativity)), 3) if creativity else 0.0,
        creativity_sd=round(float(np.std(creativity, ddof=1)), 3) if len(creativity) > 1 else 0.0,
        pairwise_win_rate=round(wins.get(reel.id, 0.0), 4),
        novelty=round((score.novelty + score.structure_novelty) / 2, 3) if score else 0.0,
        stop_rate=rate([r.stopped for r in mine]),
        avg_watch_fraction=rate([min(1.0, r.watch_seconds / length) for r in mine]),
        completion_rate=rate([r.completed for r in mine]),
        like_rate=rate([r.like / 100 for r in mine]),
        comment_rate=rate([r.comment / 100 for r in mine]),
        share_rate=rate([r.share / 100 for r in mine]),
        save_rate=rate([r.save / 100 for r in mine]),
        inquire_rate=rate([r.inquire / 100 for r in mine]),
    )


# ---- the verdict --------------------------------------------------------------------------------------------


def _kind(reel: Reel) -> str:
    return {"baseline": "template", "creative": "creative", "single_agent": "single agent"}[reel.kind]


def verdict_facts(reels: list[Reel], summaries: list[ReelSummary], agree: dict[str, float], div: dict[str, float],
                  scores: list[NoveltyScore], room_ids: set[str], single_ids: set[str]) -> tuple[list[str], list[str]]:
    """(statements the verdict must make when true, context statements), computed from the numbers alone."""
    must, context = [], []
    kind = {r.id: r.kind for r in reels}
    judged = [s for s in summaries if s.creativity > 0]
    if judged:
        top = max(judged, key=lambda s: (s.creativity, s.pairwise_win_rate))
        top_pw = max(judged, key=lambda s: (s.pairwise_win_rate, s.creativity))
        context.append(f"Highest mean creativity: Reel {top.reel} ({_kind(next(r for r in reels if r.id == top.reel))}), "
                       f"{top.creativity:.1f}/10 (sd {top.creativity_sd:.1f}).")
        context.append(f"Most pairwise wins: Reel {top_pw.reel}, {top_pw.pairwise_win_rate:.0%} of its comparisons.")
        base = [s for s in judged if kind[s.reel] == "baseline"]
        creative = [s for s in judged if kind[s.reel] == "creative"]
        if base and creative:
            b, best = base[0], max(creative, key=lambda s: (s.creativity, s.pairwise_win_rate))
            if best.creativity <= b.creativity:
                must.append(f"No creative reel beat the template (Reel {b.reel}) on mean creativity: the best creative "
                            f"reel, {best.reel}, scored {best.creativity:.1f} against {b.creativity:.1f}.")
            if max(s.pairwise_win_rate for s in creative) <= b.pairwise_win_rate:
                must.append(f"No creative reel won more pairwise comparisons than the template (Reel {b.reel}, "
                            f"{b.pairwise_win_rate:.0%}).")
        single = [s for s in judged if kind[s.reel] == "single_agent"]
        if single and creative:
            s1, best = single[0], max(creative, key=lambda s: (s.creativity, s.pairwise_win_rate))
            if s1.creativity >= best.creativity or s1.pairwise_win_rate >= best.pairwise_win_rate:
                must.append(f"The single-agent reel ({s1.reel}) matched or beat the room's best reel ({best.reel}): "
                            f"creativity {s1.creativity:.1f} vs {best.creativity:.1f}, pairwise wins "
                            f"{s1.pairwise_win_rate:.0%} vs {best.pairwise_win_rate:.0%}.")
    if agree.get("judges", 0) >= 2 and len(judged) >= 2 and agree.get("kendall_w", 0) < LOW_AGREEMENT:
        must.append(f"The judges disagreed about the ranking: Kendall's W = {agree['kendall_w']:.2f} "
                    f"(below {LOW_AGREEMENT}), so the creativity ranking is unsettled.")
    elif "kendall_w" in agree and len(judged) >= 2:
        context.append(f"Judge agreement: Kendall's W = {agree['kendall_w']:.2f}.")
    if "room" in div and "single_agent" in div:
        verb = "were at least as diverse as" if div["single_agent"] >= div["room"] else "were less diverse than"
        line = (f"The single agent's concepts {verb} the room's round-1 concepts (mean pairwise distance "
                f"{div['single_agent']:.2f} vs {div['room']:.2f}).")
        (must if div["single_agent"] >= div["room"] else context).append(line)
    room_nov = [s.novelty for s in scores if s.kind == "concept" and s.id in room_ids]
    single_nov = [s.novelty for s in scores if s.kind == "concept" and s.id in single_ids]
    if room_nov and single_nov:
        r, s = float(np.mean(room_nov)), float(np.mean(single_nov))
        line = f"Mean text novelty of the concepts: room {r:.2f}, single agent {s:.2f}."
        (must if s >= r else context).append(line + (" The single agent matched or beat the room." if s >= r else ""))
    if summaries:
        hold = max(summaries, key=lambda s: (s.stop_rate, s.avg_watch_fraction))
        spread = max(summaries, key=lambda s: s.share_rate + s.save_rate)
        context.append(f"Highest stop rate: Reel {hold.reel} ({hold.stop_rate:.0%}, average watch "
                       f"{hold.avg_watch_fraction:.0%} of the reel). Highest share plus save intent: Reel {spread.reel}.")
    return must, context


def _quotes(reels: list[Reel], reactions: list[Reaction], ratings: list[JudgeRating], votes: list[PairwiseVote],
            viewers: list[ViewerPersona]) -> dict[str, Any]:
    weight = {v.id: v.weight for v in viewers}
    segment = {v.id: v.segment.split(",")[0] for v in viewers}
    out: dict[str, Any] = {}
    for reel in reels:
        rs = sorted((r for r in reactions if r.reel == reel.id), key=lambda r: -weight.get(r.viewer, 0))
        left = [r for r in rs if not r.stopped][:2]
        stayed = [r for r in rs if r.stopped][:3]
        out[reel.id] = {
            "viewers": [{"who": segment.get(r.viewer, r.viewer), "watched": f"{r.watch_seconds:.1f} of {reel.seconds:.0f} s",
                         "said": r.comment_text or (r.why if r.stopped else r.first_impression)} for r in [*left, *stayed]],
            "judges": [{"judge": r.judge, "creativity": r.creativity, "said": r.notes} for r in ratings if r.reel == reel.id],
            "pairwise": [v.reason for v in votes if v.winner == reel.id][:2],
        }
    return out


def _fallback_verdict(must: list[str], context: list[str]) -> str:
    return " ".join([*context[:2], *must]) or "The panel produced no ratings, so there is no verdict."


def head_judge(ctx: LabContext, reels: list[Reel], summaries: list[ReelSummary], agree: dict[str, float],
               div: dict[str, float], must: list[str], context: list[str], quotes: dict[str, Any]) -> tuple[str, list[str]]:
    """(verdict paragraph, caveats). Falls back to a plain statement of the facts if the call fails."""
    payload = {
        "reels": [{"id": r.id, "is": _kind(r), "seconds": round(r.seconds, 1)} for r in reels],
        "summaries": [s.model_dump() for s in summaries],
        "judge_agreement": agree,
        "concept_diversity": div,
        "must_state": must,
        "context": context,
        "quotes": quotes,
    }
    try:
        a = ask_once(ctx, HEAD_JUDGE.id, "judge", HEAD_SYSTEM, f"The panel's results:\n{dumps(payload)}", HeadAnswer,
                     model=ctx.settings.creative_judge_model, effort=ctx.settings.creative_judge_effort)
        return a.verdict.strip(), [c.strip() for c in a.caveats if c.strip()][:2]
    except Exception as e:  # noqa: BLE001 - the numbers stand without the paragraph
        if type(e).__name__ == "Cancelled":
            raise
        return _fallback_verdict(must, context), [f"The head judge failed ({type(e).__name__}); this verdict lists the facts."]


# ---- the whole network --------------------------------------------------------------------------------------


def evaluate(ctx: LabContext, reels: list[Reel], concepts: list[ReelConcept], single_agent: list[ReelConcept]) -> Evaluation:
    """Watch, judge and measure every reel; summarise; write the verdict. `concepts` is every concept in the lab,
    template included; `single_agent` the control's concepts."""
    viewers = default_viewers()
    judges = [c.model_copy(update={"model": ctx.settings.creative_judge_model}) for c in judge_cards()]
    weights = {v.id: v.weight for v in viewers}
    by_id: dict[str, ReelConcept] = {c.id: c for c in [*single_agent, *concepts]}
    shoot = ctx.store.load(ctx.shoot_id)
    rooms = {v.id: (v.room or v.label or "") for v in shoot.views}
    facts = listing_facts(shoot.listing)
    captions = {r.id: caption_for(by_id.get(r.concept)) for r in reels}
    seed = ctx.options.seed
    notes: list[str] = []
    failures: list[str] = []
    lock = threading.Lock()
    threading.Thread(target=novelty.warm, daemon=True).start()  # the embedding model loads while the panel works

    frames: dict[str, list[Frame]] = {}
    for r in reels:
        try:
            frames[r.id] = reel_frames(r, ctx.lab_dir / r.video)
        except Exception as e:  # noqa: BLE001 - one unreadable video should not sink the others
            notes.append(f"Reel {r.id}'s video could not be read ({e}); the panel did not see it.")
    shown = [r for r in reels if r.id in frames]
    warm = Prewarmer(ctx)

    # Watch: every viewer, every reel. Reels go one at a time, each reel's viewers in parallel.
    total, seen = len(viewers) * len(shown), [0]

    def reacted(_: Reaction) -> None:
        with lock:
            seen[0] += 1
            ctx.progress("watch", f"{seen[0]} of {total} reactions in")

    def lost(what: str, e: Exception) -> None:
        with lock:
            failures.append(f"{what}: {type(e).__name__}: {str(e)[:160]}")

    ctx.progress("watch", f"{len(viewers)} viewers are watching {len(shown)} reels")
    reactions: list[Reaction] = []
    for r in shown:
        reactions += watch(ctx, r, ctx.lab_dir / r.video, viewers, caption=captions[r.id], frames=frames[r.id],
                           prewarm=warm, on_reaction=reacted, on_error=lambda v, e, r=r: lost(f"{v.id} on {r.id}", e))

    # Judge: ratings and pairwise comparisons, blind, all in parallel.
    ctx.progress("judge", f"{len(judges)} judges are rating {len(shown)} reels and comparing every pair")
    ratings, votes, _ = judge_reels(
        ctx, shown, frames, captions, facts, seed, judges=judges, prewarm=warm,
        on_done=lambda n, t: ctx.progress("judge", f"{n} of {t} ratings and comparisons in"), on_error=lost)
    agree = agreement(ratings, votes, [r.id for r in shown])

    # Novelty: deterministic, and blind to which concept is which.
    ctx.progress("novelty", f"Scoring {len(by_id)} concepts and {len(reels)} reels against the cliche corpus")
    report = novelty.measure(concepts, reels, single_agent, rooms, mask=_address_parts(shoot.listing), seed=seed)
    reel_scores = {s.id: s for s in report.scores if s.kind == "reel"}
    ctx.progress("novelty", f"{report.map.method}; " + ", ".join(
        f"{rid} {s.novelty:.2f}" for rid, s in reel_scores.items()))

    wins = win_rates(votes)
    summaries = [summarize(r, reactions, ratings, wins, reel_scores.get(r.id), weights) for r in reels]

    # Verdict: the head judge reads only numbers and quotes.
    ctx.progress("verdict", "The head judge is reading the numbers")
    single_ids = {c.id for c in single_agent}
    room_ids = {c.id for c in concepts if c.id not in single_ids and c.agent not in ("template", "single_agent")
                and c.id != "template"}
    must, context = verdict_facts(reels, summaries, agree, report.diversity, report.scores, room_ids, single_ids)
    if reactions or ratings:
        verdict, caveats = head_judge(ctx, reels, summaries, agree, report.diversity, must, context,
                                      _quotes(shown, reactions, ratings, votes, viewers))
    else:  # nothing was watched or judged: there is nothing for the head judge to read
        verdict, caveats = "No reel could be shown to the panel, so there is no verdict.", []
    ctx.emit(HEAD_JUDGE.id, "note", verdict)
    ctx.progress("verdict", verdict[:140])

    s = ctx.settings
    n_frames = max((len(f) for f in frames.values()), default=0)
    notes = [
        *must,
        f"Every viewer and judge is a Claude agent ({s.audience_model} for viewers, {s.creative_judge_model} for "
        "judges), not a person: the panel is a structured simulation, and its members share one model's blind spots.",
        f"The panel saw up to {n_frames} still frames per reel with the on-screen text and spoken words as text: no "
        "motion, and the music and voice were described, not heard.",
        _agreement_note(agree, len(shown)),
        f"Novelty is distance from {report.corpus}, measured with {report.map.method} (calibration anchors: stock "
        f"{report.anchors['stock']:.2f}, fresh {report.anchors['fresh']:.2f}); it rewards being unlike known "
        "cliches, not being good.",
        *notes,
        *caveats,
    ]
    if failures:
        notes.append(f"{len(failures)} panel calls failed and are missing from the numbers: " + "; ".join(failures[:4]))
    return Evaluation(viewers=viewers, reactions=reactions, judges=judges, ratings=ratings, pairwise=votes,
                      agreement=agree, novelty=report.scores, map=report.map, diversity=report.diversity,
                      summaries=summaries, verdict=verdict, notes=[n for n in notes if n])


def _agreement_note(agree: dict[str, float], n_reels: int) -> str:
    if agree.get("judges", 0) < 2 or n_reels < 2:
        return "Too few judges or reels for an agreement statistic."
    line = (f"Kendall's W = {agree['kendall_w']:.2f} over {n_reels} reels and {int(agree['judges'])} judges"
            + (" (low: treat the creativity ranking as unsettled)" if agree["kendall_w"] < LOW_AGREEMENT else "") + ".")
    if "first_shown_wins" in agree:
        line += (f" In {agree['order_consistency']:.0%} of judge-pairs the same reel won in both orders; the reel "
                 f"shown first won {agree['first_shown_wins']:.0%} of votes.")
    if n_reels <= 3:
        line += f" With {n_reels} reels, W and the win rates rest on few comparisons."
    return line
