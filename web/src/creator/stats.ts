// Scorecard helpers: means, judge agreement in plain English, head-to-head tables.

import type { JudgeRating, PairwiseVote } from "./types";

export const DIMENSIONS = ["originality", "surprise", "hook", "specificity", "craft", "coherence", "truthfulness"] as const;
export type Dimension = (typeof DIMENSIONS)[number];

export const DIMENSION_LABEL: Record<Dimension, string> = {
  originality: "Originality",
  surprise: "Surprise",
  hook: "Hook",
  specificity: "Specificity",
  craft: "Craft",
  coherence: "Coherence",
  truthfulness: "Truthfulness",
};

export const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

/** Sample standard deviation (n - 1); 0 for fewer than two values. */
export function sd(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  return Math.sqrt(xs.reduce((a, x) => a + (x - m) ** 2, 0) / (xs.length - 1));
}

/** Mean judge score per dimension for one reel. */
export function dimensionMeans(ratings: JudgeRating[], reel: string): Record<Dimension, number> {
  const mine = ratings.filter((r) => r.reel === reel);
  const out = {} as Record<Dimension, number>;
  for (const d of DIMENSIONS) out[d] = mean(mine.map((r) => r[d]));
  return out;
}

/** Ranks (1 = highest score), ties sharing their average rank. */
export function ranks(scores: number[]): number[] {
  const order = scores.map((s, i) => ({ s, i })).sort((a, b) => b.s - a.s);
  const out = new Array<number>(scores.length);
  for (let i = 0; i < order.length; ) {
    let j = i;
    while (j + 1 < order.length && order[j + 1]!.s === order[i]!.s) j++;
    const r = (i + j) / 2 + 1;
    for (let k = i; k <= j; k++) out[order[k]!.i] = r;
    i = j + 1;
  }
  return out;
}

/**
 * Kendall's W for m judges ranking n items (rows: one judge's scores per item; higher is better).
 * 1 = every judge gives the same order, 0 = no shared order. Tie-corrected, as creative/judges.py, so the
 * dashboard and the backend report the same W when judges give equal scores.
 */
export function kendallW(scoresByJudge: number[][]): number {
  const m = scoresByJudge.length;
  const n = scoresByJudge[0]?.length ?? 0;
  if (m < 2 || n < 2) return 0;
  const rk = scoresByJudge.map(ranks);
  const totals = Array.from({ length: n }, (_, j) => rk.reduce((a, r) => a + r[j]!, 0));
  const avg = mean(totals);
  const s = totals.reduce((a, t) => a + (t - avg) ** 2, 0);
  let ties = 0;
  for (const row of scoresByJudge) {
    const counts = new Map<number, number>();
    for (const v of row) counts.set(v, (counts.get(v) ?? 0) + 1);
    for (const c of counts.values()) ties += c ** 3 - c;
  }
  const denom = m * m * (n ** 3 - n) - m * ties;
  return denom > 0 ? (12 * s) / denom : 0;
}

/** Kendall's W from judge ratings on one field (creativity by default). Judges missing a reel are left out. */
export function kendallFromRatings(ratings: JudgeRating[], reels: string[], field: Dimension | "creativity" = "creativity"): number {
  const judges = [...new Set(ratings.map((r) => r.judge))];
  const rows = judges
    .map((j) => reels.map((reel) => ratings.find((r) => r.judge === j && r.reel === reel)?.[field]))
    .filter((row): row is number[] => row.every((v) => typeof v === "number"));
  return kendallW(rows);
}

/** Plain English for Kendall's W. */
export function agreementLabel(w: number): { short: string; long: string } {
  if (w >= 0.7) return { short: "Very strong agreement", long: "The judges rank the reels in nearly the same order." };
  if (w >= 0.5) return { short: "Strong agreement", long: "Most judges rank the reels in the same order." };
  if (w >= 0.3) return { short: "Moderate agreement", long: "The judges share a rough order but disagree on some places." };
  if (w >= 0.1) return { short: "Weak agreement", long: "The judges disagree more than they agree; read the ranking loosely." };
  return { short: "No agreement", long: "The judges' rankings look unrelated; treat the creativity ranking as noise." };
}

export interface HeadToHead {
  wins: number;
  total: number;
}

/** wins[a][b]: how often a beat b when the two were shown together. */
export function headToHead(votes: PairwiseVote[], reels: string[]): Record<string, Record<string, HeadToHead>> {
  const t: Record<string, Record<string, HeadToHead>> = {};
  for (const a of reels) {
    t[a] = {};
    for (const b of reels) t[a]![b] = { wins: 0, total: 0 };
  }
  for (const v of votes) {
    const loser = v.winner === v.first ? v.second : v.first;
    const row = t[v.winner]?.[loser];
    const col = t[loser]?.[v.winner];
    if (!row || !col) continue;
    row.wins += 1;
    row.total += 1;
    col.total += 1;
  }
  return t;
}

/** Share of a reel's pairwise comparisons it won. */
export function winRate(votes: PairwiseVote[], reel: string): number {
  const mine = votes.filter((v) => v.first === reel || v.second === reel);
  return mine.length ? mine.filter((v) => v.winner === reel).length / mine.length : 0;
}
