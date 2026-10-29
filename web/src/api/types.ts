import type { ClipJudge } from "./listing";

// JSON shapes from src/shootagent/store.py and src/shootagent/trace.py, as served by docs/api.md.

export interface Bracket {
  blob: string;
  index: number;
  ev: number | null;
  name: string | null;
}

export type FindingStatus = "open" | "fixed" | "dismissed" | "failed";

export interface Finding {
  id: string;
  type: string;
  explanation: string;
  /** [x0, y0, x1, y1] normalised 0..1 */
  region: number[] | null;
  confidence: number | null;
  status: FindingStatus;
  evidence: Record<string, unknown>;
}

/** verify.py: "unavailable" means the vision judge errored and the deterministic metrics decided. */
export interface Judge {
  verdict: "pass" | "fail" | "unavailable" | string;
  reason?: string;
  issues?: string[] | null;
  model?: string;
}

export interface CheckResult {
  version?: string;
  parent?: string | null;
  passed: boolean;
  metrics?: Record<string, number | string | null>;
  judge?: Judge | null;
  failures?: string[];
  intent?: string;
}

export interface Version {
  id: string;
  parent: string | null;
  blob: string;
  op: string;
  params: Record<string, unknown>;
  mask: string | null;
  note: string;
  run: string | null;
  created_at: string;
  check: CheckResult | null;
}

export interface View {
  id: string;
  label: string;
  room: string | null;
  brackets: Bracket[];
  finished: string | null;
  reference: string | null;
  groups: number[][] | null;
  derived_from: string | null;
  versions: Version[];
  head: string | null;
  findings: Finding[];
  approved: boolean;
  meta: Record<string, unknown>;
}

export interface Mask {
  id: string;
  view: string;
  blob: string;
  source: string;
  query: string | null;
  bbox: number[] | null;
  area: number | null;
  created_at: string;
}

export interface Listing {
  address: string | null;
  price: string | null;
  beds: number | null;
  baths: number | null;
  sqft: number | null;
  headline: string | null;
}

export interface Shoot {
  schema: number;
  id: string;
  title: string;
  created_at: string;
  source: Record<string, unknown>;
  listing: Listing;
  views: View[];
  masks: Record<string, Mask>;
  runs: string[];
}

export interface ShootSummary {
  id: string;
  title: string;
  created_at: string;
  view_count: number;
  thumb: string | null;
}

export type RunMode = "qc" | "chat" | "reel" | "film";

export interface RunSummary {
  id: string;
  mode: RunMode | string | null;
  /** chat request text (api/app.py adds it; not in docs/api.md) */
  request?: string | null;
  /** null when the run's jsonl has no run_start yet */
  started: string | null;
  done: boolean;
  status?: string;
  usd: number;
  summary: string | null;
}

// ---- trace events ---------------------------------------------------------------------------------------

export interface PlanStep {
  view?: string | null;
  intent: string;
  tool?: string | null;
}

export interface TraceDataByKind {
  run_start: { mode: RunMode | string; request?: string | null; views?: string[]; languages?: string[] };
  plan: { steps: PlanStep[]; text?: string };
  thought: { text: string };
  tool_call: { call_id: string; tool: string; input: Record<string, unknown> };
  tool_result: {
    call_id: string;
    tool: string;
    ok: boolean;
    summary: string;
    version?: string | null;
    mask?: string | null;
    images?: string[];
    ms: number;
    error?: string | null;
    /** tool-specific payload, e.g. render_reel's {url, seconds} */
    data?: Record<string, unknown> | null;
  };
  /** An edit check has `version`; a film clip check has `clip`, `shot` and a ClipJudge-shaped judge. */
  check: {
    call_id?: string | null;
    version?: string;
    clip?: string;
    shot?: number;
    attempt?: number;
    images?: string[];
    parent?: string | null;
    passed: boolean;
    /** absent on film clip checks, which report `signals` instead */
    metrics?: Record<string, number | string | null>;
    judge?: (Partial<Judge> & ClipJudge) | null;
    failures?: string[];
    intent?: string;
  };
  /** film: a clip or narration produced or served from the media cache */
  media: { shot?: number; language?: string; fingerprint: string; backend: string; cached: boolean; seconds?: number | null };
  /** film: what a shot will play */
  shot: { shot: number; end_view?: string | null; planned: string; source: string; fell_back: boolean; clips: string[]; note: string };
  /** attempt = how many fix attempts on this view have failed so far (agent/loop.py); the next try is attempt + 1 */
  retry: { attempt: number; reason: string; next: string; max_attempts?: number };
  /** images: crops showing the evidence, by blob name */
  finding: { finding: Finding; images?: string[] };
  /** chat outcome for one view, from the agent's report_view_result call (authoritative) */
  view_result: { results: { request: string; status: "applied" | "not_needed" | "failed"; reason: string }[] };
  message: { text: string };
  /** usd is absent when the model is unpriced (agent/usage.py) */
  usage: { model: string; input_tokens?: number; output_tokens?: number; usd?: number; purpose?: string };
  run_end: { ok: boolean; summary: string; ms: number; usd: number; counts?: Record<string, number> };
  error: { message: string };
}

export type TraceKind = keyof TraceDataByKind;

export type TraceEvent = {
  [K in TraceKind]: {
    run: string;
    seq: number;
    t: string;
    kind: K;
    view: string | null;
    data: TraceDataByKind[K];
  };
}[TraceKind];

export type TraceEventOf<K extends TraceKind> = Extract<TraceEvent, { kind: K }>;

export const TRACE_KINDS: readonly TraceKind[] = [
  "run_start",
  "plan",
  "thought",
  "tool_call",
  "tool_result",
  "check",
  "media",
  "shot",
  "retry",
  "finding",
  "view_result",
  "message",
  "usage",
  "run_end",
  "error",
];

export interface ApiErrorBody {
  error: { code: string; message: string };
}
