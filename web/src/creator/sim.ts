// Simulated-launch maths: the accelerated clock, counts at a clock time, the winner and its margin.

import type { Reel, SimComment, SimPoint, Simulation } from "./types";

export const METRICS = ["views", "likes", "comments", "shares", "saves", "follows", "inquiries"] as const;
export type Metric = (typeof METRICS)[number];

export const zeroPoint = (hour: number): SimPoint => ({
  hour,
  views: 0,
  likes: 0,
  comments: 0,
  shares: 0,
  saves: 0,
  follows: 0,
  inquiries: 0,
});

function sortedByHour(series: SimPoint[]): SimPoint[] {
  for (let i = 1; i < series.length; i++) if (series[i]!.hour < series[i - 1]!.hour) return [...series].sort((a, b) => a.hour - b.hour);
  return series;
}

/**
 * Cumulative counts at `hour`, linearly interpolated between the series' points and rounded to whole counts.
 * Before the first point the counts rise from zero at hour 0; after the last they hold.
 */
export function interpolateSeries(series: SimPoint[], hour: number): SimPoint {
  if (!series.length) return zeroPoint(hour);
  const pts = sortedByHour(series);
  const first = pts[0]!;
  const last = pts[pts.length - 1]!;
  if (hour >= last.hour) return { ...last, hour };
  let lo: SimPoint;
  let hi: SimPoint;
  if (hour <= first.hour) {
    if (first.hour <= 0 || hour <= 0) return first.hour <= 0 ? { ...first, hour } : zeroPoint(hour);
    lo = zeroPoint(0);
    hi = first;
  } else {
    let a = 0;
    let b = pts.length - 1;
    while (b - a > 1) {
      const mid = (a + b) >> 1;
      if (pts[mid]!.hour <= hour) a = mid;
      else b = mid;
    }
    lo = pts[a]!;
    hi = pts[b]!;
  }
  const span = hi.hour - lo.hour;
  const f = span > 0 ? (hour - lo.hour) / span : 1;
  const out = zeroPoint(hour);
  for (const k of METRICS) out[k] = Math.round(lo[k] + (hi[k] - lo[k]) * f);
  return out;
}

/** The accelerated clock: `elapsedMs` of a `playMs` playback mapped onto 0..hours. */
export function clockHour(elapsedMs: number, playMs: number, hours: number): number {
  if (playMs <= 0) return hours;
  return Math.min(hours, Math.max(0, (elapsedMs / playMs) * hours));
}

export interface LaunchOutcome {
  metric: Metric;
  winner: string;
  winnerValue: number;
  /** what the winner is measured against: the template baseline, or the runner-up when the template itself wins */
  against: string | null;
  againstValue: number;
  /** winnerValue / againstValue; null when `against` got nothing or there is no one to compare with */
  ratio: number | null;
  tie: boolean;
  ranking: { reel: string; value: number }[];
}

/** Who travelled furthest on `metric` (totals), and by how much. */
export function launchOutcome(
  sims: Simulation[],
  reels: Pick<Reel, "id" | "kind">[],
  metric: Metric = "views",
): LaunchOutcome | null {
  if (!sims.length) return null;
  const ranking = sims
    .map((s) => ({ reel: s.reel, value: s.totals[metric] }))
    .sort((a, b) => b.value - a.value || a.reel.localeCompare(b.reel));
  const top = ranking[0]!;
  const baselineId = reels.find((r) => r.kind === "baseline")?.id;
  const baseline = baselineId && baselineId !== top.reel ? ranking.find((r) => r.reel === baselineId) : undefined;
  const against = baseline ?? ranking[1] ?? null;
  const tie = !!ranking[1] && ranking[1].value === top.value;
  return {
    metric,
    winner: top.reel,
    winnerValue: top.value,
    against: against?.reel ?? null,
    againstValue: against?.value ?? 0,
    ratio: against && against.value > 0 ? top.value / against.value : null,
    tie,
    ranking,
  };
}

/** "6.2×", "14×": one decimal below 10, whole numbers above. */
export function formatRatio(r: number): string {
  return `${r >= 9.95 ? Math.round(r) : r.toFixed(1)}×`;
}

/** "B reached 6.2× the views of A" */
export function marginText(o: LaunchOutcome): string {
  if (!o.against) return `${o.winner} was the only reel launched`;
  if (o.tie) return `${o.winner} and ${o.ranking[1]!.reel} tied on ${o.metric}`;
  if (o.ratio == null) return `${o.winner} reached ${o.winnerValue.toLocaleString("en-US")} ${o.metric}; ${o.against} reached none`;
  return `${o.winner} reached ${formatRatio(o.ratio)} the ${o.metric} of ${o.against}`;
}

export interface FeedComment extends SimComment {
  reel: string;
  key: string;
}

/** Comments posted by `hour`, newest first. */
export function visibleComments(sims: Simulation[], hour: number): FeedComment[] {
  const all: FeedComment[] = [];
  sims.forEach((s) => s.comments.forEach((c, i) => c.hour <= hour && all.push({ ...c, reel: s.reel, key: `${s.reel}-${i}` })));
  return all.sort((a, b) => b.hour - a.hour || a.key.localeCompare(b.key));
}

/** Views per audience segment by `hour`: the final split, scaled by how far the reel's views have got. */
export function segmentsAt(sim: Simulation, hour: number): Record<string, number> {
  const total = sim.totals.views;
  const f = total > 0 ? interpolateSeries(sim.series, hour).views / total : 0;
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(sim.by_segment)) out[k] = Math.round(v * f);
  return out;
}
