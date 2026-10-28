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
  /** the client's verdict from the review link */
  client?: ClientVerdict | null;
  meta: Record<string, unknown>;
}

export interface ClientVerdict {
  status: "approved" | "changes_requested";
  at: string;
  share: string | null;
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
  description?: string | null;
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
  public_token?: string | null;
  publication?: Publication | null;
  shares?: Share[];
  comments?: Comment[];
}

export interface ShootSummary {
  id: string;
  title: string;
  created_at: string;
  view_count: number;
  thumb: string | null;
}

export type RunMode = "qc" | "chat" | "reel";

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

// ---- delivery: policy, disclosures, publication, review, kit (compliance/, api/delivery.py) --------------

export type PolicyVerdict = "decline" | "disclose" | "overlay" | "exempt" | "unclear";

export interface PolicyItem {
  text: string;
  verdict: PolicyVerdict;
  category: string;
  reason: string;
}

export interface PolicyResult {
  verdict: PolicyVerdict;
  items: PolicyItem[];
}

export type ChangeKind = "alteration" | "adjustment" | "restoration" | "overlay" | "possible_alteration";

export interface Change {
  kind: ChangeKind;
  what: string;
  detail?: string;
  by: "shootagent" | "editor";
  requested_by?: "customer" | "client" | "qc" | "order" | "unknown";
  category?: string | null;
  version?: string | null;
  finding?: string | null;
  request?: string | null;
}

export type PhotoStatus = "altered" | "unaltered" | "unverified" | "review" | "superseded";

export interface PhotoReport {
  view: string;
  label: string;
  room: string | null;
  status: PhotoStatus;
  changes: Change[];
  disclosure: string | null;
  head: string | null;
  final_blob: string | null;
  delivered_blob: string | null;
  has_brackets: boolean;
  qc_checked: boolean;
  deliver: boolean;
  notes: string[];
}

export interface PublishedImage {
  blob: string;
  sha256: string;
  width: number;
  height: number;
}

export interface ChainEntry {
  hash: string;
  body: string;
}

export interface PublishedPhotoSummary {
  view: string;
  label: string;
  order: number;
  status: string;
  disclosure: string | null;
  head: string;
  final: PublishedImage;
  original: PublishedImage | null;
  original_kind: "capture" | "delivered" | null;
}

export interface PublicationSummary {
  token: string;
  version: number;
  created_at: string;
  root: string;
  photos: PublishedPhotoSummary[];
}

export interface Publication extends Omit<PublicationSummary, "photos"> {
  heads: Record<string, string>;
  photos: (PublishedPhotoSummary & { room: string | null; changes: Change[]; capture: PublishedImage | null; chain: ChainEntry[] })[];
  manifest_blob: string | null;
}

export interface ComplianceReport {
  shoot: string;
  generated_at: string;
  photos: PhotoReport[];
  counts: Record<string, number>;
  ready: boolean;
  issues: string[];
  /** views in the listing set, in showing order */
  listing: string[];
  stale: boolean;
  publication: PublicationSummary | null;
  public_token: string | null;
}

export interface PublicPhoto {
  view: string;
  label: string;
  room: string | null;
  order: number;
  status: string;
  disclosure: string | null;
  changes: { kind: ChangeKind; what: string; by: string; category: string | null }[];
  head: string;
  final: PublishedImage;
  original: PublishedImage | null;
  original_kind: "capture" | "delivered" | null;
  capture: PublishedImage | null;
  chain: ChainEntry[];
}

export interface PublicPage {
  token: string;
  title: string;
  listing: Listing;
  version: number;
  published_at: string;
  root: string;
  photos: PublicPhoto[];
}

export interface Share {
  token: string;
  role: "client";
  label: string;
  created_at: string;
  revoked: boolean;
  max_requests: number;
}

export type CommentState =
  | "sent"
  | "declined"
  | "started"
  | "undone"
  | "queued"
  | "working"
  | "stopped"
  | "applied"
  | "not_needed"
  | "failed"
  | "done";

export interface Comment {
  id: string;
  view: string;
  text: string;
  author: "client" | "photographer";
  share: string | null;
  point: number[] | null;
  mask: string | null;
  run: string | null;
  base_version: string | null;
  policy: PolicyResult | null;
  status: "sent" | "declined" | "started" | "undone";
  note: string;
  created_at: string;
}

/** A comment with its live state, as the review and comments routes return it. */
export interface CommentView extends Comment {
  state: CommentState;
  reason: string;
  message: string;
  steps: number;
  result_version: string | null;
  result_blob: string | null;
  current: string | null;
}

export interface ReviewView {
  id: string;
  label: string;
  room: string | null;
  blob: string;
  original_blob: string;
  edited: boolean;
  head: string;
  approved: boolean;
  client: ClientVerdict | null;
  status: PhotoStatus;
  disclosure: string | null;
}

export interface ReviewShoot {
  token: string;
  label: string;
  title: string;
  listing: Listing;
  requests_left: number;
  agent_available: boolean;
  public_token: string | null;
  views: ReviewView[];
  comments: CommentView[];
}

export interface KitInfo {
  name: string;
  size: number;
  files: { path: string; size: number }[];
  built_at: string;
  publication: number;
  photos: number;
  altered: number;
  url: string;
  /** mock mode: no zip exists */
  demo?: boolean;
}

export interface LintFlag {
  phrase: string;
  start: number;
  end: number;
  severity: "avoid" | "review";
  suggestion: string;
  why: string;
}

export interface LintResult {
  ok: boolean;
  flags: LintFlag[];
  note: string;
}

export interface PlanStep {
  view?: string | null;
  intent: string;
  tool?: string | null;
}

export interface TraceDataByKind {
  run_start: {
    mode: RunMode | string;
    request?: string | null;
    views?: string[];
    /** "client" when the request came from the client review link */
    origin?: string | null;
    comment?: string | null;
    /** compliance/policy.py verdict on a chat request */
    policy?: PolicyResult | null;
  };
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
  check: {
    call_id?: string | null;
    version: string;
    parent?: string | null;
    passed: boolean;
    metrics: Record<string, number | string | null>;
    judge?: Judge | null;
    failures?: string[];
    intent?: string;
  };
  /** attempt = how many fix attempts on this view have failed so far (agent/loop.py); the next try is attempt + 1 */
  retry: { attempt: number; reason: string; next: string; max_attempts?: number };
  /** images: crops showing the evidence, by blob name */
  finding: { finding: Finding; images?: string[] };
  /** chat outcome for one view, from the agent's report_view_result call (authoritative) */
  view_result: { results: { request: string; status: "applied" | "not_needed" | "failed" | "declined"; reason: string }[] };
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
