// A TypeScript port of src/shootagent/creative/simulate.py: the same staged-distribution model and constants,
// so demo mode can re-run a launch for other followers and seeds without a server. The random stream differs
// from numpy's, so a seed gives different (equally likely) draws than the Python model.
// Only `import type` here: the fixture builder runs this file directly under Node.

import type { Reaction, Reel, ReelSummary, RetentionPoint, SimComment, SimPoint, Simulation, ViewerPersona } from "./types";

const REACH = 0.25;
const HOOK_SECONDS = 1.5;
const STOP: [number, number] = [0.2, 0.7];
const COMPLETION: [number, number] = [0.05, 0.6];
const RATES = {
  like: [0.02, 0.1],
  comment: [0.001, 0.01],
  share: [0.002, 0.02],
  save: [0.002, 0.02],
  follow: [0.0005, 0.005],
  inquire: [0.0001, 0.003],
  rewatch: [0.01, 0.25],
} as const satisfies Record<string, readonly [number, number]>;
type Action = keyof typeof RATES;
const ACTIONS = Object.keys(RATES) as Action[];
const WEIGHTS = { completion: 0.35, rewatch: 0.15, share: 0.25, save: 0.15, comment: 0.1 } as const;
type Signal = keyof typeof WEIGHTS;
const MAX_MULTIPLIER = 3.0;
const SHARE_VIEWERS: [number, number] = [2, 6];
const POOL_PER_FOLLOWER = 40;
const EXPLORE = 0.5;
const COMMENT_LIKE = 0.004;
const RETENTION_STEP = 0.5;
const MAX_WAVES = 16;
const POINT_FIELDS = { likes: "like", comments: "comment", shares: "share", saves: "save", follows: "follow", inquiries: "inquire" } as const;

// ---- seeded sampling ------------------------------------------------------------------------------

export interface Rng {
  next(): number;
  normal(): number;
  binomial(n: number, p: number): number;
  multinomial(n: number, probs: number[]): number[];
  /** sum of `count` uniform integers in [lo, hi] */
  intSum(count: number, lo: number, hi: number): number;
}

export function makeRng(seed: number): Rng {
  let a = (Math.floor(seed) ^ 0x9e3779b9) >>> 0 || 1;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const normal = () => {
    const u = Math.max(next(), 1e-12);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * next());
  };
  const binomial = (n: number, p: number): number => {
    n = Math.max(0, Math.floor(n));
    if (n === 0 || p <= 0) return 0;
    if (p >= 1) return n;
    if (p > 0.5) return n - binomial(n, 1 - p);
    if (n < 60) {
      let k = 0;
      for (let i = 0; i < n; i++) if (next() < p) k++;
      return k;
    }
    const mean = n * p;
    if (mean < 30) {
      // Poisson approximation for rare events
      const limit = Math.exp(-mean);
      let k = 0;
      let prod = next();
      while (prod > limit) {
        k++;
        prod *= next();
      }
      return Math.min(n, k);
    }
    return Math.min(n, Math.max(0, Math.round(mean + Math.sqrt(mean * (1 - p)) * normal())));
  };
  const multinomial = (n: number, probs: number[]): number[] => {
    const out = probs.map(() => 0);
    let rest = Math.max(0, Math.floor(n));
    let mass = probs.reduce((s, x) => s + Math.max(0, x), 0);
    for (let i = 0; i < probs.length - 1 && rest > 0; i++) {
      const p = Math.max(0, probs[i]!);
      const k = mass > 0 ? binomial(rest, Math.min(1, p / mass)) : 0;
      out[i] = k;
      rest -= k;
      mass -= p;
    }
    if (probs.length) out[probs.length - 1]! += rest;
    return out;
  };
  const intSum = (count: number, lo: number, hi: number): number => {
    count = Math.max(0, Math.floor(count));
    if (count < 60) {
      let s = 0;
      for (let i = 0; i < count; i++) s += lo + Math.floor(next() * (hi - lo + 1));
      return s;
    }
    const m = (lo + hi) / 2;
    const variance = ((hi - lo + 1) ** 2 - 1) / 12;
    return Math.max(count * lo, Math.min(count * hi, Math.round(count * m + Math.sqrt(count * variance) * normal())));
  };
  return { next, normal, binomial, multinomial, intSum };
}

// ---- the model ------------------------------------------------------------------------------------

interface Segment {
  id: string;
  name: string;
  weight: number;
  stop: number;
  completion: number;
  rates: Record<Action, number>;
  watch: number[];
  comments: string[];
}

interface Wave {
  start: number;
  length: number;
  impressions: number[];
  views: number[];
  completions: number[];
  actions: Record<Action, number[]>;
  score: number;
  multiplier: number;
}

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const lerp = ([lo, hi]: readonly [number, number], x: number) => lo + (hi - lo) * clamp01(x);
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

function segment(
  id: string,
  name: string,
  weight: number,
  stopped: number,
  watchFraction: number,
  completed: number,
  intents: Partial<Record<Action, number>>,
  watch: number[],
  comments: string[],
): Segment {
  const rates = {} as Record<Action, number>;
  for (const a of ACTIONS) rates[a] = lerp(RATES[a], intents[a] ?? 0);
  return { id, name, weight, stop: lerp(STOP, stopped), completion: lerp(COMPLETION, (completed + watchFraction) / 2), rates, watch, comments };
}

function fromReactions(id: string, name: string, weight: number, rs: Reaction[], seconds: number): Segment {
  const n = rs.length;
  const watch = rs.map((r) => Math.min(seconds, Math.max(0, r.watch_seconds)));
  const intents = {} as Record<Action, number>;
  for (const a of ACTIONS) intents[a] = sum(rs.map((r) => Math.min(100, Math.max(0, r[a])))) / (100 * n);
  const said = rs.filter((r) => r.comment > 0 && r.comment_text.trim()).map((r) => r.comment_text.trim());
  return segment(id, name, weight, rs.filter((r) => r.stopped).length / n, sum(watch) / (n * seconds), rs.filter((r) => r.completed).length / n, intents, watch, said);
}

function fromSummary(s: ReelSummary, seconds: number): Segment {
  const intents = { like: s.like_rate, comment: s.comment_rate, share: s.share_rate, save: s.save_rate, inquire: s.inquire_rate };
  return segment("audience", "Audience panel", 1, s.stop_rate, s.avg_watch_fraction, s.completion_rate, intents, [
    Math.min(seconds, Math.max(0, s.avg_watch_fraction * seconds)),
  ], []);
}

function audience(reel: Reel, summary: ReelSummary | undefined, reactions: Reaction[], viewers: ViewerPersona[], seconds: number) {
  let mine = reactions.filter((r) => r.reel === reel.id);
  if (!mine.length && new Set(reactions.map((r) => r.reel)).size === 1) mine = [...reactions];
  const byViewer = new Map<string, Reaction[]>();
  for (const r of mine) byViewer.set(r.viewer, [...(byViewer.get(r.viewer) ?? []), r]);
  const known = viewers.map((v) => v.weight).filter((w) => w > 0);
  const fallback = known.length ? sum(known) / known.length : 1;
  let segs: Segment[] = [];
  const missing: string[] = [];
  const notes: string[] = [];
  for (const v of viewers) {
    const rs = byViewer.get(v.id);
    if (rs) {
      segs.push(fromReactions(v.id, v.name, Math.max(0, v.weight), rs, seconds));
      byViewer.delete(v.id);
    } else missing.push(v.name);
  }
  for (const [vid, rs] of byViewer) segs.push(fromReactions(vid, vid, fallback, rs, seconds));
  if (segs.length && missing.length) notes.push(`No reaction from ${missing.join(", ")}: left out of the audience mix.`);
  const signal = summary && [summary.stop_rate, summary.avg_watch_fraction, summary.completion_rate, summary.like_rate, summary.comment_rate, summary.share_rate, summary.save_rate, summary.inquire_rate].some((x) => x);
  if (!segs.length && summary && signal) {
    segs = [fromSummary(summary, seconds)];
    notes.push("No individual reactions for this reel: one segment built from its panel summary.");
  }
  const total = sum(segs.map((s) => s.weight));
  for (const s of segs) s.weight = total > 0 ? s.weight / total : 1 / segs.length;
  return { segs, notes };
}

const REF: Record<Signal, number> = (() => {
  const stop = lerp(STOP, 0.5);
  return {
    completion: stop * lerp(COMPLETION, 0.5),
    rewatch: stop * lerp(RATES.rewatch, 0.5),
    share: stop * lerp(RATES.share, 0.5),
    save: stop * lerp(RATES.save, 0.5),
    comment: stop * lerp(RATES.comment, 0.5),
  };
})();

/** Engagement score per segment (per impression); 0 where a segment had no impressions. */
function score(impressions: number[], completions: number[], actions: Record<Action, number[]>): number[] {
  return impressions.map((imp, i) => {
    if (imp <= 0) return 0;
    const counts: Record<Signal, number> = {
      completion: completions[i]!,
      rewatch: actions.rewatch[i]!,
      share: actions.share[i]!,
      save: actions.save[i]!,
      comment: actions.comment[i]!,
    };
    let s = 0;
    for (const k of Object.keys(WEIGHTS) as Signal[]) s += (WEIGHTS[k] * (counts[k] / Math.max(imp, 1))) / REF[k];
    return s;
  });
}

function waves(segs: Segment[], followers: number, hours: number, rng: Rng): Wave[] {
  const base = segs.map((s) => s.weight);
  const pool = Math.max(1, followers * POOL_PER_FOLLOWER);
  let impressions = rng.multinomial(Math.round(followers * REACH), base);
  const out: Wave[] = [];
  let reached = 0;
  for (let k = 0; k < MAX_WAVES; k++) {
    const start = 2 ** k - 1;
    const length = 2 ** k;
    const total = sum(impressions);
    if (start >= hours || total === 0) break;
    const views = impressions.map((n, i) => rng.binomial(n, segs[i]!.stop));
    const completions = views.map((n, i) => rng.binomial(n, segs[i]!.completion));
    const actions = {} as Record<Action, number[]>;
    for (const a of ACTIONS) actions[a] = views.map((n, i) => rng.binomial(n, segs[i]!.rates[a]));
    reached += total;
    const wholeActions = {} as Record<Action, number[]>;
    for (const a of ACTIONS) wholeActions[a] = [sum(actions[a])];
    const whole = score([total], [sum(completions)], wholeActions)[0]!;
    const multiplier = Math.min(MAX_MULTIPLIER, whole);
    const organic = Math.round(total * multiplier * Math.max(0, 1 - reached / pool));
    const segScore = score(impressions, completions, actions);
    const tilt = base.map((b, i) => b * segScore[i]!);
    const tiltSum = sum(tilt);
    const weights = tiltSum <= 0 ? base : base.map((b, i) => EXPLORE * b + ((1 - EXPLORE) * tilt[i]!) / tiltSum);
    const wSum = sum(weights);
    const nxt = rng.multinomial(organic, weights.map((w) => w / wSum));
    const shared = actions.share.map((n) => rng.intSum(n, SHARE_VIEWERS[0], SHARE_VIEWERS[1]));
    out.push({ start, length, impressions, views, completions, actions, score: whole, multiplier });
    impressions = nxt.map((n, i) => n + shared[i]!);
  }
  return out;
}

const landed = (w: Wave, hour: number) => clamp01((hour - w.start) / w.length);

function point(ws: Wave[], hour: number): SimPoint {
  const fr = ws.map((w) => landed(w, hour));
  const views = sum(ws.map((w, k) => sum(w.views.map((v) => Math.floor(v * fr[k]!)))));
  const p: SimPoint = { hour, views, likes: 0, comments: 0, shares: 0, saves: 0, follows: 0, inquiries: 0 };
  for (const [field, a] of Object.entries(POINT_FIELDS) as [keyof typeof POINT_FIELDS, Action][]) {
    p[field] = sum(ws.map((w, k) => Math.floor(sum(w.actions[a]) * fr[k]!)));
  }
  return p;
}

function retention(segs: Segment[], share: number[], seconds: number): RetentionPoint[] {
  const ts: number[] = [];
  for (let t = 0; t <= seconds + 1e-9; t += RETENTION_STEP) ts.push(Math.round(t * 100) / 100);
  if (seconds - ts[ts.length - 1]! > 1e-6) ts.push(seconds);
  return ts.map((t) => {
    let frac = 0;
    segs.forEach((s, i) => {
      const stayed = s.watch.filter((x) => x >= t - 1e-9).length / s.watch.length;
      const hooked = s.watch.filter((x) => Math.min(x, HOOK_SECONDS) >= t - 1e-9).length / s.watch.length;
      frac += share[i]! * (s.stop * stayed + (1 - s.stop) * hooked);
    });
    return { second: Math.round(t * 100) / 100, fraction: Math.round(frac * 10000) / 10000 };
  });
}

function comments(segs: Segment[], ws: Wave[], hours: number, totalViews: number, rng: Rng): SimComment[] {
  const out: SimComment[] = [];
  segs.forEach((s, i) => {
    const texts = [...s.comments];
    for (const w of ws) {
      const n = w.actions.comment[i]!;
      const m = Math.min(n, texts.length); // fixed before the loop, like Python's range()
      for (let j = 0; j < m; j++) {
        const hour = w.start + (w.length * (j + 0.5)) / n;
        if (hour > hours) break;
        const later = Math.max(0, totalViews - point(ws, hour).views);
        out.push({ hour: Math.round(hour * 100) / 100, viewer: s.id, name: s.name, text: texts.shift()!, likes: rng.binomial(later, COMMENT_LIKE) });
      }
      if (!texts.length) break;
    }
  });
  return out.sort((a, b) => a.hour - b.hour || a.viewer.localeCompare(b.viewer));
}

const pct = (x: number) => `${(x * 100).toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}%`;

function notes(followers: number, ws: Wave[]): string[] {
  const rates = ACTIONS.map((a) => `${a} ${pct(RATES[a][0])}-${pct(RATES[a][1])}`).join(", ");
  const weights = (Object.keys(WEIGHTS) as Signal[]).map((k) => `${WEIGHTS[k].toFixed(2)} ${k}`).join(" + ");
  const out = [
    `Wave 0 reaches ${Math.round(REACH * 100)}% of ${followers.toLocaleString("en-US")} followers. A view is an impression that watched past the hook (the first ${HOOK_SECONDS} s).`,
    `Per segment (one viewer persona, weighted by its audience share): ${Math.round(STOP[0] * 100)}% of impressions stop if its viewer scrolled past, ${Math.round(STOP[1] * 100)}% if they stopped; ${pct(COMPLETION[0])}-${pct(COMPLETION[1])} of views complete, from \`completed\` and watch_seconds / reel length.`,
    `Per-view action rates are linear in the 0-100 intents: ${rates}. Every count is a binomial draw.`,
    `Engagement score = ${weights}, each per impression and relative to a reel whose signals all sit mid-range (score 1.0 keeps the next wave the same size).`,
    `Next wave = this wave x score (capped at ${MAX_MULTIPLIER}x) x the unreached share of an interest-graph pool of ${POOL_PER_FOLLOWER}x followers, plus ${SHARE_VIEWERS[0]}-${SHARE_VIEWERS[1]} viewers per share from the sharer's segment.`,
    `Interest graph: ${Math.round(EXPLORE * 100)}% of each later wave follows audience weight, the rest goes to segments in proportion to weight x their engagement in the previous wave.`,
    "Wave k lands evenly over hours 2^k - 1 to 2^(k+1) - 1 (1 h, 2 h, 4 h, ...): fast early growth that flattens unless the score stays near 2.",
    "Only the panel's reactions to this reel go in: its kind, creativity scores and novelty do not. The same seed gives the same draws.",
  ];
  if (ws.length) out.push(`Waves (impressions, score): ${ws.map((w) => `${sum(w.impressions).toLocaleString("en-US")} at ${w.score.toFixed(2)}`).join(", ")}.`);
  return out;
}

/** Simulate `hours` of one reel's launch to `followers` from its audience panel's reactions. Pure and seeded. */
export function simulateReel(
  reel: Reel,
  summary: ReelSummary | undefined,
  reactions: Reaction[],
  viewers: ViewerPersona[],
  followers = 8000,
  hours = 48,
  seed = 7,
): Simulation {
  followers = Math.max(0, Math.floor(followers));
  hours = Math.max(1, Math.floor(hours));
  const seconds = Math.max(reel.seconds || 0, 1);
  const { segs, notes: audienceNotes } = audience(reel, summary, reactions, viewers, seconds);
  if (!segs.length) {
    const zero = (h: number): SimPoint => ({ hour: h, views: 0, likes: 0, comments: 0, shares: 0, saves: 0, follows: 0, inquiries: 0 });
    return {
      reel: reel.id,
      followers,
      hours,
      seed,
      series: Array.from({ length: hours + 1 }, (_, h) => zero(h)),
      retention: [],
      totals: zero(hours),
      comments: [],
      by_segment: {},
      notes: ["No audience reactions for this reel, so there is nothing to simulate."],
    };
  }
  // one stream per reel, so adding a reel doesn't change the others' draws
  const rng = makeRng(seed * 1009 + [...reel.id].reduce((a, c) => a * 31 + c.charCodeAt(0), 7));
  const ws = waves(segs, followers, hours, rng);
  const series = Array.from({ length: hours + 1 }, (_, h) => point(ws, h));
  const totals = series[series.length - 1]!;
  const fr = ws.map((w) => landed(w, hours));
  const by_segment: Record<string, number> = {};
  segs.forEach((s, i) => (by_segment[s.id] = sum(ws.map((w, k) => Math.floor(w.views[i]! * fr[k]!)))));
  let share = segs.map((_, i) => sum(ws.map((w, k) => w.impressions[i]! * fr[k]!)));
  const shareSum = sum(share);
  share = shareSum > 0 ? share.map((x) => x / shareSum) : segs.map((s) => s.weight);
  return {
    reel: reel.id,
    followers,
    hours,
    seed,
    series,
    retention: retention(segs, share, seconds),
    totals,
    comments: comments(segs, ws, hours, totals.views, rng),
    by_segment,
    notes: [...notes(followers, ws), ...audienceNotes],
  };
}

/** Every rendered reel's launch, from a lab's evaluation. */
export function simulateLab(
  lab: { reels: Reel[]; evaluation: { reactions: Reaction[]; viewers: ViewerPersona[]; summaries: ReelSummary[] } | null },
  body: { followers: number; hours: number; seed: number },
): Simulation[] {
  const ev = lab.evaluation;
  if (!ev) return [];
  return lab.reels.map((r) =>
    simulateReel(r, ev.summaries.find((s) => s.reel === r.id), ev.reactions, ev.viewers, body.followers, body.hours, body.seed),
  );
}
