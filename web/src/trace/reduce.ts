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
}

export interface CheckItem extends Base {
  type: "check";
  callId: string | null;
  tool: string | null;
  attempt: number;
  version: string;
  passed: boolean;
  metrics: Record<string, number | string | null>;
  judge: TraceDataByKind["check"]["judge"];
  failures: string[];
}

export type TimelineItem =
  | ToolItem
  | CheckItem
  | (Base & { type: "thought"; text: string })
  | (Base & { type: "retry"; attempt: number; reason: string; next: string })
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
  };
}

const viewKey = (view: string | null | undefined) => view ?? "_";

function markStep(steps: PlanStepState[], index: number): PlanStepState[] {
  // Steps without a tool are narrative ("compare against brackets"); once a later step completes,
  // everything before it is treated as done, since the plan is executed in order.
  return steps.map((s, i) => (i === index || (i < index && !s.tool) ? { ...s, done: true } : s));
}

function findStep(steps: PlanStepState[], tool: string, view: string | null): number {
  return steps.findIndex((s) => !s.done && s.tool === tool && (s.view == null || s.view === view));
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
          attempt: s.attempts[vk] ?? 1,
          version: d.version,
          passed: d.passed,
          metrics: d.metrics ?? {},
          judge: d.judge ?? null,
          failures: d.failures ?? [],
        },
      ];
      s.totals = d.passed
        ? { ...s.totals, checksPassed: s.totals.checksPassed + 1 }
        : { ...s.totals, checksFailed: s.totals.checksFailed + 1 };
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
      s.attempts = { ...s.attempts, [vk]: event.data.attempt };
      s.totals = { ...s.totals, retries: s.totals.retries + 1 };
      s.items = [...s.items, { ...base, type: "retry", ...event.data }];
      return s;
    }
    case "finding": {
      const f = event.data.finding;
      const first = !(f.id in s.findings);
      s.findings = { ...s.findings, [f.id]: f };
      const all = Object.values(s.findings);
      s.totals = {
        ...s.totals,
        findingsFound: all.length,
        findingsFixed: all.filter((x) => x.status === "fixed").length,
      };
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
      s.totals = { ...s.totals, usd: d.usd ?? s.totals.usd, ms: d.ms ?? at };
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

function checkFailReason(d: TraceDataByKind["check"]): string {
  if (d.failures?.length) return d.failures.join("; ");
  return d.judge?.reason ?? "The self-check failed.";
}

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
    } else out[view] = { outcome: "not_needed", reason: s.lastNoteByView[view] ?? "No change was needed." };
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
