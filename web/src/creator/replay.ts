// Demo mode: replays a recorded LabState as if it were running. `buildTimeMap` squeezes the recording into a
// minute or so; `labAt` is the state the server would have served at a recorded time. Works on any LabState.

import type { ConceptStatus, Evaluation, LabPhase, LabState, ReelConcept } from "./types";

export interface TimeMap {
  /** replay seconds for the whole recording */
  duration: number;
  /** recorded seconds -> replay seconds */
  toReplay(t: number): number;
  /** replay seconds -> recorded seconds */
  toRecorded(r: number): number;
}

/** The recorded second the lab finished: its elapsed time, or the last thing that happened if that is later. */
export function recordingEnd(lab: LabState): number {
  let end = Number.isFinite(lab.elapsed) ? lab.elapsed : 0;
  for (const m of lab.feed) end = Math.max(end, m.t);
  for (const p of lab.phases) end = Math.max(end, p.ended ?? p.started ?? 0);
  return end;
}

/** Recorded times where something becomes visible: messages, phase starts and ends, and the end. Sorted, unique. */
export function keyframes(lab: LabState): number[] {
  const ts = new Set<number>([0, recordingEnd(lab)]);
  for (const m of lab.feed) ts.add(Math.max(0, m.t));
  for (const p of lab.phases) {
    if (p.started != null) ts.add(Math.max(0, p.started));
    if (p.ended != null) ts.add(Math.max(0, p.ended));
  }
  return [...ts].filter(Number.isFinite).sort((a, b) => a - b);
}

function interp(xs: number[], ys: number[], x: number): number {
  const n = xs.length;
  if (!n) return 0;
  if (x <= xs[0]!) return ys[0]!;
  if (x >= xs[n - 1]!) return ys[n - 1]!;
  let a = 0;
  let b = n - 1;
  while (b - a > 1) {
    const mid = (a + b) >> 1;
    if (xs[mid]! <= x) a = mid;
    else b = mid;
  }
  const span = xs[b]! - xs[a]!;
  return span > 0 ? ys[a]! + ((ys[b]! - ys[a]!) * (x - xs[a]!)) / span : ys[b]!;
}

/**
 * Maps the recording onto `target` replay seconds. Each gap between keyframes plays for gap^power (gaps past
 * `maxGap` count as `maxGap`) before everything is scaled to fit, so a four-minute render wait shrinks far
 * more than the second between two critiques: bursts stay readable and dead air goes.
 */
export function buildTimeMap(lab: LabState, target: number, power = 0.5, maxGap = 90): TimeMap {
  const ks = keyframes(lab);
  const gaps = ks.slice(1).map((k, i) => Math.pow(Math.min(maxGap, k - ks[i]!), power));
  const sum = gaps.reduce((a, b) => a + b, 0);
  const scale = sum > 0 ? target / sum : 0;
  const rs = [0];
  for (const g of gaps) rs.push(rs[rs.length - 1]! + g * scale);
  return {
    duration: rs[rs.length - 1]!,
    toReplay: (t) => interp(ks, rs, t),
    toRecorded: (r) => interp(rs, ks, r),
  };
}

/** The state of a recorded lab at recorded second `t`, as the server would have served it then. */
export function labAt(lab: LabState, t: number): LabState {
  const end = recordingEnd(lab);
  if (t >= end) return lab;

  const phase = (key: string): LabPhase | undefined => lab.phases.find((p) => p.key === key);
  const reached = (at: number | null | undefined) => at != null && at <= t;
  const startOf = (key: string) => phase(key)?.started ?? null;
  /** when a phase's results land: its end, or the end of the recording if it never ended */
  const doneAt = (key: string) => {
    const p = phase(key);
    return p ? (p.ended ?? end) : null;
  };
  const progress = (key: string) => {
    const p = phase(key);
    if (!p || p.started == null || p.started > t) return 0;
    const stop = p.ended ?? end;
    return stop <= p.started ? 1 : Math.min(1, (t - p.started) / (stop - p.started));
  };

  // ---- phases
  const phases: LabPhase[] = lab.phases.map((p) => {
    // lab.py marks a phase skipped when the lab starts; one that never ran otherwise stays pending
    if (p.started == null) return p.status === "skipped" ? p : { ...p, status: "pending" };
    if (p.started > t) return { ...p, status: "pending", started: null, ended: null, detail: "" };
    if (p.ended != null && p.ended <= t) return p;
    return { ...p, status: "running", ended: null, detail: "" };
  });
  const running = phases.filter((p) => p.status === "running");
  const current = running.reduce<LabPhase | null>((a, b) => (!a || (b.started ?? 0) >= (a.started ?? 0) ? b : a), null);

  // ---- room
  const feed = lab.feed.filter((m) => m.t <= t);
  const firstMention = new Map<string, number>();
  for (const m of lab.feed) {
    if (m.concept != null) firstMention.set(m.concept, Math.min(firstMention.get(m.concept) ?? Infinity, m.t));
  }
  const appears = (c: ReelConcept): number => {
    const m = firstMention.get(c.id);
    if (m != null) return m;
    if (c.agent === "template") return startOf("render") ?? end;
    const key = c.agent === "single_agent" || lab.single_agent.includes(c.id) ? "single_agent" : c.round <= 1 ? "pitch" : "remix";
    return doneAt(key) ?? end;
  };
  const appearsAt = new Map(lab.concepts.map((c) => [c.id, appears(c)]));
  const visible = new Set(lab.concepts.filter((c) => appearsAt.get(c.id)! <= t).map((c) => c.id));

  const picked = doneAt("greenlight") ?? doneAt("critique") ?? end;
  const statusAt = (c: ReelConcept): ConceptStatus => {
    switch (c.status) {
      case "rendered":
        if (reached(doneAt("render") ?? end)) return "rendered";
        return lab.finalists.includes(c.id) && reached(picked) ? "finalist" : "pitched";
      case "finalist":
      case "killed":
        return reached(picked) ? c.status : "pitched";
      case "revised":
        return lab.concepts.some((k) => k.parents.includes(c.id) && visible.has(k.id)) ? "revised" : "pitched";
      default:
        return c.status;
    }
  };
  const concepts = lab.concepts
    .filter((c) => visible.has(c.id))
    .map((c) => {
      const s = statusAt(c);
      return s === c.status ? c : { ...c, status: s };
    });

  const critiques = lab.critiques.filter((cr) => {
    if (!visible.has(cr.concept)) return false;
    const m = lab.feed.find((x) => x.kind === "critique" && x.agent === cr.critic && x.concept === cr.concept);
    return reached(m ? m.t : Math.max(appearsAt.get(cr.concept) ?? 0, doneAt("critique") ?? end));
  });

  const briefAt = lab.feed.find((m) => m.kind === "brief")?.t ?? doneAt("brief") ?? 0;

  // ---- reels: each lands with the last render-phase message about its concept, else when rendering ends
  const render = phase("render");
  const reels = lab.reels.filter((r) => {
    let at = doneAt("render") ?? end;
    if (render?.started != null) {
      const stop = render.ended ?? end;
      const msgs = lab.feed.filter((m) => m.concept === r.concept && m.t >= render.started! && m.t <= stop);
      if (msgs.length) at = msgs[msgs.length - 1]!.t;
    }
    return reached(at);
  });

  // ---- evaluation: rows arrive across their phase
  let evaluation: Evaluation | null = null;
  const ev = lab.evaluation;
  const evalKeys = ["watch", "judge", "novelty", "verdict"].filter((k) => phase(k));
  if (ev && evalKeys.some((k) => reached(startOf(k)))) {
    const reveal = <T,>(xs: T[], key: string): T[] => (phase(key) ? xs.slice(0, Math.ceil(xs.length * progress(key))) : []);
    const done = (key: string) => reached(doneAt(key) ?? end);
    evaluation = {
      viewers: reached(startOf("watch")) ? ev.viewers : [],
      reactions: reveal(ev.reactions, "watch"),
      judges: reached(startOf("judge")) ? ev.judges : [],
      ratings: reveal(ev.ratings, "judge"),
      pairwise: reveal(ev.pairwise, "judge"),
      agreement: done("judge") ? ev.agreement : {},
      novelty: done("novelty") ? ev.novelty : [],
      map: done("novelty") ? ev.map : null,
      diversity: done("novelty") ? ev.diversity : {},
      summaries: (phase("verdict") ? reached(startOf("verdict")) : evalKeys.every(done)) ? ev.summaries : [],
      verdict: done("verdict") ? ev.verdict : "",
      notes: done("verdict") ? ev.notes : [],
    };
  }

  // ---- spend grows with time
  const f = end > 0 ? Math.max(0, t) / end : 1;
  const u = lab.usage;
  const by_role: Record<string, number> = {};
  for (const [k, v] of Object.entries(u.by_role)) by_role[k] = v * f;

  return {
    ...lab,
    status: "running",
    phase: current?.key ?? "",
    phases,
    brief: reached(briefAt) ? lab.brief : null,
    feed,
    concepts,
    critiques,
    finalists: reached(picked) ? lab.finalists : [],
    single_agent: lab.single_agent.filter((id) => visible.has(id)),
    reels,
    evaluation,
    simulations: reached(doneAt("simulate") ?? end) ? lab.simulations : [],
    usage: {
      usd: u.usd * f,
      input_tokens: Math.round(u.input_tokens * f),
      output_tokens: Math.round(u.output_tokens * f),
      calls: Math.round(u.calls * f),
      by_role,
    },
    elapsed: Math.max(0, t),
    error: null,
  };
}

/**
 * Keeps `prev`'s objects wherever `next` is equal to them (two levels deep), so a poll that changed only the
 * clock doesn't re-render every section.
 */
export function reconcile<T extends object>(prev: T | null, next: T, depth = 2): T {
  if (!prev || prev === next) return next;
  let same = true;
  const out = { ...next } as Record<string, unknown>;
  for (const k of Object.keys(out)) {
    const a = (prev as Record<string, unknown>)[k];
    const b = out[k];
    if (a === b) continue;
    if (a && b && typeof a === "object" && typeof b === "object") {
      if (JSON.stringify(a) === JSON.stringify(b)) {
        out[k] = a;
        continue;
      }
      if (depth > 1 && !Array.isArray(a) && !Array.isArray(b)) out[k] = reconcile(a as object, b as object, depth - 1);
    }
    same = false;
  }
  if (same && Object.keys(prev).length === Object.keys(out).length) return prev;
  return out as T;
}
