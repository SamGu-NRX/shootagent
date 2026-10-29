// Folds a run's trace events (trace.py) into the state the Trace and Chat panels render.
// Pure and idempotent by `seq`: an SSE reconnect replays every past event, and replaying them again
// must leave the state unchanged.

import type { Finding, PlanStep, TraceDataByKind, TraceEvent } from "../api/types";

export type ViewOutcome = "pending" | "applied" | "not_needed" | "failed";

export interface ViewResult {
  outcome: ViewOutcome;
  reason: string;
  version?: string;
}

interface Base {
  key: string;
  seq: number;
  /** ms since run_start */
  at: number;
  view: string | null;
}

export interface ToolItem extends Base {
  type: "tool";
  callId: string;
  tool: string;
  input: Record<string, unknown>;
  attempt: number;
  status: "running" | "ok" | "failed";
  result?: TraceDataByKind["tool_result"];
  /** findings first reported while this call ran (run_qc_checks candidates), shown inside its row */
  candidates?: string[];
  /** film: clips and narrations this call produced, and how many came from the media cache */
  media?: { made: number; cached: number };
}

export interface CheckItem extends Base {
  type: "check";
  callId: string | null;
  tool: string | null;
  attempt: number;
  /** edit version, or "" for a film clip check */
  version: string;
  passed: boolean;
  metrics: Record<string, number | string | null>;
  judge: TraceDataByKind["check"]["judge"];
  failures: string[];
  /** film clip checks: the clip fingerprint, the shot index and the contact sheet blobs */
  clip?: string;
  shot?: number;
  images?: string[];
}

export interface ShotItem extends Base {
  type: "shot";
  shot: number;
  endView: string | null;
  planned: string;
  source: string;
  fellBack: boolean;
  note: string;
}

export type TimelineItem =
  | ToolItem
  | CheckItem
  | ShotItem
  | (Base & { type: "thought"; text: string })
  | (Base & { type: "retry"; attempt: number; reason: string; next: string; max_attempts?: number })
  | (Base & { type: "finding"; findingId: string; status: Finding["status"]; first: boolean })
  | (Base & { type: "message"; text: string })
  | (Base & { type: "error"; message: string })
  | (Base & { type: "end"; ok: boolean; summary: string })
  | (Base & { type: "unknown"; kind: string });

export interface PlanStepState extends PlanStep {
  done: boolean;
}

export interface TraceTotals {
  usd: number;
  inputTokens: number;
  outputTokens: number;
  toolCalls: number;
  checksPassed: number;
  checksFailed: number;
  retries: number;
  findingsFound: number;
  findingsFixed: number;
  /** authoritative duration from run_end, else null while running */
  ms: number | null;
}

export interface TraceState {
  runId: string | null;
  mode: string | null;
  request: string | null;
  views: string[];
  status: "idle" | "running" | "done" | "failed";
  /** epoch ms of run_start */
  startedAt: number | null;
  lastSeq: number;
  plan: { steps: PlanStepState[]; text: string };
  items: TimelineItem[];
  totals: TraceTotals;
  findings: Record<string, Finding>;
  viewResults: Record<string, ViewResult>;
  messages: string[];
  summary: string | null;
  // bookkeeping the reducer needs between events
  attempts: Record<string, number>;
  pendingStepByCall: Record<string, number>;
  editByCall: Record<string, { view: string | null; version: string; summary: string }>;
  lastNoteByView: Record<string, string>;
  lastFailByView: Record<string, string>;
  /** views the agent closed with report_view_result; their outcome is final */
  reportedViews: Record<string, true>;
  /** evidence crops per finding id, from finding events */
  findingImages: Record<string, string[]>;
}

export function emptyTrace(): TraceState {
  return {
    runId: null,
    mode: null,
    request: null,
    views: [],
    status: "idle",
    startedAt: null,
    lastSeq: 0,
    plan: { steps: [], text: "" },
    items: [],
    totals: {
      usd: 0,
      inputTokens: 0,
      outputTokens: 0,
      toolCalls: 0,
      checksPassed: 0,
      checksFailed: 0,
      retries: 0,
      findingsFound: 0,
      findingsFixed: 0,
      ms: null,
    },
    findings: {},
    viewResults: {},
    messages: [],
    summary: null,
    attempts: {},
    pendingStepByCall: {},
    editByCall: {},
    lastNoteByView: {},
    lastFailByView: {},
    reportedViews: {},
    findingImages: {},
  };
}

const viewKey = (view: string | null | undefined) => view ?? "_";

function markStep(steps: PlanStepState[], index: number): PlanStepState[] {
  // Steps without a tool are narrative ("compare against brackets"); once a later step completes,
  // everything before it is treated as done, since the plan is executed in order.
  return steps.map((s, i) => (i === index || (i < index && !s.tool) ? { ...s, done: true } : s));
}

// Tool-name words too common to tie a tool to a plan step ("compare against the brackets").
const GENERIC_WORDS = new Set(["views", "photo", "region", "regions", "brackets", "image", "check"]);

/**
 * Whether a finished tool call satisfies a plan step. The agent's plan steps often omit `tool`, so a
 * step without one matches a tool whose distinctive name word (5+ letters) appears in the step's intent:
 * run_qc_checks satisfies "Run QC checks on every view", label_views satisfies "label rooms".
 */
function stepMatches(step: PlanStepState, tool: string, view: string | null): boolean {
  if (step.view != null && step.view !== view) return false;
  // run 20261028-001209-9e8f42 planned "gloom_place_on_screen" for place_on_screen: accept a prefixed name
  if (step.tool) return step.tool === tool || step.tool.endsWith(`_${tool}`);
  const intent = step.intent.toLowerCase();
  return tool
    .split("_")
    .some((w) => w.length >= 5 && !GENERIC_WORDS.has(w) && intent.includes(w));
}

function findStep(steps: PlanStepState[], tool: string, view: string | null): number {
  return steps.findIndex((s) => !s.done && stepMatches(s, tool, view));
}

export function reduceTrace(state: TraceState, event: TraceEvent): TraceState {
  if (event.seq <= state.lastSeq) return state;
  const s: TraceState = { ...state, lastSeq: event.seq };
  // api/app.py synthesises a run_end with t=null for runs whose process died; place it at the last event.
  const parsed = event.t ? Date.parse(event.t) : NaN;
  const lastAt = s.items[s.items.length - 1]?.at ?? 0;
  const epoch = Number.isFinite(parsed) ? parsed : (s.startedAt ?? 0) + lastAt;
  const at = s.startedAt == null ? 0 : Math.max(0, epoch - s.startedAt);
  const base = { key: `${event.run}:${event.seq}`, seq: event.seq, at, view: event.view };
  const vk = viewKey(event.view);

  switch (event.kind) {
    case "run_start": {
      const views = event.data.views ?? [];
      s.runId = event.run;
      s.mode = event.data.mode;
      s.request = event.data.request ?? null;
      s.views = views;
      s.status = "running";
      s.startedAt = epoch;
      s.viewResults = Object.fromEntries(views.map((v) => [v, { outcome: "pending", reason: "" } as ViewResult]));
      return s;
    }
    case "plan": {
      // A plan can arrive after the agent has already run some tools (perception first, then a plan);
      // steps those finished tools satisfy start checked.
      let steps: PlanStepState[] = event.data.steps.map((st) => ({ ...st, done: false }));
      for (const it of s.items) {
        if (it.type !== "tool" || it.status !== "ok" || it.result?.version) continue;
        const i = findStep(steps, it.tool, it.view);
        if (i >= 0) steps = markStep(steps, i);
      }
      s.plan = { steps, text: event.data.text ?? "" };
      return s;
    }
    case "thought": {
      if (event.view) s.lastNoteByView = { ...s.lastNoteByView, [event.view]: event.data.text };
      s.items = [...s.items, { ...base, type: "thought", text: event.data.text }];
      return s;
    }
    case "tool_call": {
      s.totals = { ...s.totals, toolCalls: s.totals.toolCalls + 1 };
      s.items = [
        ...s.items,
        {
          ...base,
          type: "tool",
          callId: event.data.call_id,
          tool: event.data.tool,
          input: event.data.input ?? {},
          attempt: s.attempts[vk] ?? 1,
          status: "running",
        },
      ];
      return s;
    }
    case "tool_result": {
      const d = event.data;
      const idx = s.items.findIndex((it) => it.type === "tool" && it.callId === d.call_id);
      if (idx >= 0) {
        const item = s.items[idx] as ToolItem;
        const items = s.items.slice();
        items[idx] = { ...item, status: d.ok ? "ok" : "failed", result: d };
        s.items = items;
      } else {
        // A result without its call (stream joined late): show it as its own completed row.
        s.items = [
          ...s.items,
          {
            ...base,
            type: "tool",
            callId: d.call_id,
            tool: d.tool,
            input: {},
            attempt: s.attempts[vk] ?? 1,
            status: d.ok ? "ok" : "failed",
            result: d,
          },
        ];
      }
      if (d.ok) {
        const step = findStep(s.plan.steps, d.tool, event.view);
        if (d.version) {
          s.editByCall = { ...s.editByCall, [d.call_id]: { view: event.view, version: d.version, summary: d.summary } };
          if (step >= 0) s.pendingStepByCall = { ...s.pendingStepByCall, [d.call_id]: step };
        } else if (step >= 0) {
          s.plan = { ...s.plan, steps: markStep(s.plan.steps, step) };
        }
      } else if (event.view) {
        s.lastFailByView = { ...s.lastFailByView, [event.view]: d.error ?? d.summary };
      }
      return s;
    }
    case "check": {
      const d = event.data;
      const callId = d.call_id ?? null;
      const tool = callId
        ? ((s.items.find((it) => it.type === "tool" && it.callId === callId) as ToolItem | undefined)?.tool ?? null)
        : null;
      s.items = [
        ...s.items,
        {
          ...base,
          type: "check",
          callId,
          tool,
          attempt: d.attempt ?? s.attempts[vk] ?? 1,
          version: d.version ?? "",
          passed: d.passed,
          metrics: d.metrics ?? {},
          judge: clipJudge(d.judge),
          failures: d.failures ?? [],
          ...(d.clip ? { clip: d.clip, shot: d.shot, images: d.images ?? [] } : {}),
        },
      ];
      s.totals = d.passed
        ? { ...s.totals, checksPassed: s.totals.checksPassed + 1 }
        : { ...s.totals, checksFailed: s.totals.checksFailed + 1 };
      // A film clip check is about a clip, not an edit to the photo: it settles no per-view outcome.
      if (d.clip) return s;
      const edit = callId ? s.editByCall[callId] : undefined;
      const view = event.view ?? edit?.view ?? null;
      if (d.passed) {
        if (callId && callId in s.pendingStepByCall) {
          s.plan = { ...s.plan, steps: markStep(s.plan.steps, s.pendingStepByCall[callId]!) };
        }
        if (view && view in s.viewResults && !s.reportedViews[view]) {
          s.viewResults = {
            ...s.viewResults,
            [view]: { outcome: "applied", reason: edit?.summary ?? d.judge?.reason ?? "", version: d.version },
          };
        }
      } else if (view) {
        s.lastFailByView = { ...s.lastFailByView, [view]: checkFailReason(d) };
      }
      return s;
    }
    case "media": {
      // A film run makes dozens of clips and narrations; count them on the call that made them instead of a row each.
      const host = findLastIndex(s.items, (it) => it.type === "tool" && it.status === "running");
      if (host < 0) return s;
      const items = s.items.slice();
      const tool = items[host] as ToolItem;
      const m = tool.media ?? { made: 0, cached: 0 };
      items[host] = { ...tool, media: { made: m.made + 1, cached: m.cached + (event.data.cached ? 1 : 0) } };
      s.items = items;
      return s;
    }
    case "shot": {
      const d = event.data;
      s.items = [
        ...s.items,
        { ...base, type: "shot", shot: d.shot, endView: d.end_view ?? null, planned: d.planned, source: d.source, fellBack: d.fell_back, note: d.note },
      ];
      return s;
    }
    case "view_result": {
      const view = event.view;
      if (!view) return s;
      const res = event.data.results ?? [];
      const outcome: ViewOutcome = res.some((x) => x.status === "applied")
        ? "applied"
        : res.some((x) => x.status === "failed")
          ? "failed"
          : "not_needed";
      const prev = s.viewResults[view];
      s.viewResults = {
        ...s.viewResults,
        [view]: { outcome, reason: res.map((x) => x.reason).filter(Boolean).join(" "), version: prev?.version },
      };
      s.reportedViews = { ...s.reportedViews, [view]: true };
      return s;
    }
    case "retry": {
      // attempt counts failures so far; tool calls after this retry belong to attempt + 1
      s.attempts = { ...s.attempts, [vk]: event.data.attempt + 1 };
      s.totals = { ...s.totals, retries: s.totals.retries + 1 };
      s.items = [...s.items, { ...base, type: "retry", ...event.data }];
      return s;
    }
    case "finding": {
      const f = event.data.finding;
      const first = !(f.id in s.findings);
      s.findings = { ...s.findings, [f.id]: f };
      if (event.data.images?.length) s.findingImages = { ...s.findingImages, [f.id]: event.data.images };
      const all = Object.values(s.findings);
      s.totals = {
        ...s.totals,
        // candidates the agent dismissed were never defects, so they don't count toward "fixed of found"
        findingsFound: all.filter((x) => x.status !== "dismissed").length,
        findingsFixed: all.filter((x) => x.status === "fixed").length,
      };
      // run_qc_checks reports its candidates as finding events before its tool_result; list them in
      // that call's row instead of one timeline row each.
      const host = first
        ? s.items.findIndex((it) => it.type === "tool" && it.status === "running" && it.tool === "run_qc_checks" && it.view === event.view)
        : -1;
      // A status change the agent made with set_finding is already shown by that call's row.
      if (!first && s.items.some((it) => it.type === "tool" && it.tool === "set_finding" && it.input.finding === f.id)) return s;
      if (host >= 0) {
        const items = s.items.slice();
        const tool = items[host] as ToolItem;
        items[host] = { ...tool, candidates: [...(tool.candidates ?? []), f.id] };
        s.items = items;
        return s;
      }
      s.items = [...s.items, { ...base, type: "finding", findingId: f.id, status: f.status, first }];
      return s;
    }
    case "message": {
      if (event.view) s.lastNoteByView = { ...s.lastNoteByView, [event.view]: event.data.text };
      s.messages = [...s.messages, event.data.text];
      s.items = [...s.items, { ...base, type: "message", text: event.data.text }];
      return s;
    }
    case "usage": {
      const d = event.data;
      s.totals = {
        ...s.totals,
        usd: s.totals.usd + (d.usd ?? 0),
        inputTokens: s.totals.inputTokens + (d.input_tokens ?? 0),
        outputTokens: s.totals.outputTokens + (d.output_tokens ?? 0),
      };
      return s;
    }
    case "run_end": {
      const d = event.data;
      s.status = d.ok ? "done" : "failed";
      s.summary = d.summary;
      // run ended ok: steps no tool call could be tied to were carried out; a tool step that never passed stays open
      if (d.ok) s.plan = { ...s.plan, steps: s.plan.steps.map((st) => (st.done || st.tool ? st : { ...st, done: true })) };
      // api/app.py ends a run whose process died with ms 0 and usd 0; keep what the events measured
      s.totals = { ...s.totals, usd: d.usd || s.totals.usd, ms: d.ms || at };
      s.viewResults = settleViews(s);
      s.items = [...s.items, { ...base, type: "end", ok: d.ok, summary: d.summary }];
      return s;
    }
    case "error": {
      s.items = [...s.items, { ...base, type: "error", message: event.data.message }];
      return s;
    }
    default: {
      const kind = (event as { kind: string }).kind;
      s.items = [...s.items, { ...base, type: "unknown", kind }];
      return s;
    }
  }
}

function findLastIndex<T>(xs: T[], pred: (x: T) => boolean): number {
  for (let i = xs.length - 1; i >= 0; i--) if (pred(xs[i]!)) return i;
  return -1;
}

/** A film clip judge reports `pass` and a list of changes; give it the edit judge's verdict and reason. */
function clipJudge(j: TraceDataByKind["check"]["judge"]): TraceDataByKind["check"]["judge"] {
  if (!j || j.verdict || j.pass === undefined) return j ?? null;
  const changes = (j.changes ?? []).map((c) => `${c.object} ${c.change.replace(/_/g, " ")}${c.first_frame ? ` (${c.first_frame})` : ""}`);
  return { ...j, verdict: j.pass ? "pass" : "fail", reason: changes.length ? changes.join("; ") : j.pass ? "No change to the room seen." : "" };
}

function checkFailReason(d: TraceDataByKind["check"]): string {
  if (d.failures?.length) return d.failures.join("; ");
  return d.judge?.reason ?? "The self-check failed.";
}

export const NOT_REACHED = "Not reached: the run stopped first.";

/** At run end, every view still pending becomes "not needed" (no edit made) or "failed" (edits all failed). */
function settleViews(s: TraceState): Record<string, ViewResult> {
  const out: Record<string, ViewResult> = {};
  for (const [view, r] of Object.entries(s.viewResults)) {
    if (r.outcome !== "pending") {
      out[view] = r;
      continue;
    }
    const unchecked = Object.values(s.editByCall).filter((e) => e.view === view);
    const fail = s.lastFailByView[view];
    if (fail) out[view] = { outcome: "failed", reason: fail };
    else if (unchecked.length) {
      const last = unchecked[unchecked.length - 1]!;
      out[view] = { outcome: "applied", reason: last.summary, version: last.version };
    } else if (s.status === "failed") out[view] = { outcome: "failed", reason: NOT_REACHED };
    else out[view] = { outcome: "not_needed", reason: s.lastNoteByView[view] ?? "No change was needed." };
  }
  return out;
}

export function reduceAll(events: TraceEvent[], start: TraceState = emptyTrace()): TraceState {
  return events.reduce(reduceTrace, start);
}

/** Index of the plan step the agent is working on, or -1 when the run is not running. */
export function currentStep(state: TraceState): number {
  if (state.status !== "running") return -1;
  return state.plan.steps.findIndex((s) => !s.done);
}
