import type {
  CommentView,
  ComplianceReport,
  KitInfo,
  LintResult,
  Listing,
  Mask,
  PolicyResult,
  PublicationSummary,
  PublicPage,
  ReviewShoot,
  RunSummary,
  Share,
  Shoot,
  ShootSummary,
  TraceEvent,
  View,
} from "./types";

export type DemoPreset = "showcase" | "qc-tour" | "chat";

export interface ChatBody {
  message: string;
  views?: string[];
  mask?: string;
}

/** [x, y, label] normalised; label 1 = include, 0 = exclude */
export type MaskPoint = [number, number, 0 | 1];

export interface RunSubscription {
  close(): void;
}

export interface ReviewCommentBody {
  view: string;
  text: string;
  /** [x, y] normalised, where the client tapped */
  point?: [number, number];
}

export interface Api {
  readonly mode: "live" | "mock";
  listShoots(): Promise<ShootSummary[]>;
  createDemo(preset: DemoPreset): Promise<Shoot>;
  uploadShoot(form: FormData): Promise<Shoot>;
  getShoot(id: string): Promise<Shoot>;
  setHead(id: string, view: string, version: string): Promise<View>;
  approve(id: string, view: string, approved: boolean): Promise<View>;
  createMask(id: string, view: string, points: MaskPoint[], box?: number[]): Promise<Mask>;
  runQc(id: string, views?: string[]): Promise<{ run_id: string }>;
  chat(id: string, body: ChatBody): Promise<{ run_id: string }>;
  reel(id: string): Promise<{ run_id: string }>;
  listRuns(id: string): Promise<RunSummary[]>;
  cancelRun(id: string, run: string): Promise<void>;
  /** Streams past events first, then live ones. `onEnd` fires once the stream ends after run_end. */
  subscribeRun(
    id: string,
    run: string,
    onEvent: (e: TraceEvent) => void,
    onEnd: (error?: ApiError) => void,
  ): RunSubscription;
  blobUrl(shootId: string, blob: string, w?: number): string;
  reelUrl(shootId: string): string;

  // ---- delivery (api/delivery.py) ----
  getCompliance(id: string): Promise<ComplianceReport>;
  /** the photographer's ruling on a QC finding that might be an alteration */
  setFindingVerdict(id: string, view: string, finding: string, verdict: "disclose" | "not_alteration"): Promise<View>;
  publish(id: string): Promise<PublicationSummary>;
  patchListing(id: string, fields: Partial<Listing>): Promise<Shoot>;
  createShare(id: string, label?: string): Promise<Share>;
  revokeShare(id: string, token: string): Promise<void>;
  listComments(id: string): Promise<CommentView[]>;
  buildKit(id: string): Promise<KitInfo>;
  /** null when no kit has been built */
  getKit(id: string): Promise<KitInfo | null>;
  lintListing(text: string): Promise<LintResult>;
  lintRequest(text: string): Promise<PolicyResult>;
  /** the public disclosure page (no passcode) */
  getPublic(token: string): Promise<PublicPage>;
  publicBlobUrl(token: string, blob: string, w?: number): string;
  /** the client review link (no passcode) */
  getReview(token: string): Promise<ReviewShoot>;
  reviewBlobUrl(token: string, blob: string, w?: number): string;
  reviewVerdict(token: string, view: string, status: "approved" | "changes_requested" | null): Promise<ReviewShoot>;
  reviewComment(token: string, body: ReviewCommentBody): Promise<CommentView>;
  reviewUndo(token: string, commentId: string): Promise<CommentView>;
  subscribeReviewRun(
    token: string,
    run: string,
    onEvent: (e: TraceEvent) => void,
    onEnd: (error?: ApiError) => void,
  ): RunSubscription;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const PASSCODE_KEY = "shootagent.passcode";
export const passcode = {
  get: () => localStorage.getItem(PASSCODE_KEY) ?? "",
  set: (v: string) => {
    localStorage.setItem(PASSCODE_KEY, v);
    // <img> and <video> can't send headers; the server also accepts the passcode as this cookie.
    document.cookie = `demo_passcode=${encodeURIComponent(v)}; path=/; SameSite=Strict`;
  },
};

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const p = passcode.get();
  if (p) h["x-demo-passcode"] = p;
  return h;
}

async function parseError(res: Response): Promise<ApiError> {
  const text = await res.text().catch(() => "");
  try {
    const body = JSON.parse(text);
    if (body?.error?.message) return new ApiError(res.status, body.error.code ?? "error", body.error.message);
    // FastAPI's own validation errors use {detail}
    if (body?.detail) {
      const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      return new ApiError(res.status, "http_" + res.status, detail);
    }
  } catch {
    /* body was not JSON */
  }
  // Vite's dev proxy answers 500 with an empty body when nothing listens on :8000.
  if (res.status === 502 || res.status === 504 || (res.status === 500 && !text.trim())) {
    return new ApiError(
      res.status,
      "server_unreachable",
      "The ShootAgent server is not responding on :8000. Start it with `shootagent serve`, or switch to demo data.",
    );
  }
  const detail = text.trim().slice(0, 200);
  return new ApiError(res.status, "http_" + res.status, `The server returned ${res.status}${detail ? `: ${detail}` : ` ${res.statusText}`}.`);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: headers(body instanceof FormData || body === undefined ? undefined : { "content-type": "application/json" }),
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "Could not reach the ShootAgent server. Check that `shootagent serve` is running.");
  }
  if (!res.ok) throw await parseError(res);
  return (await res.json()) as T;
}

/** Minimal SSE reader over fetch, so the passcode header can be sent (EventSource cannot set headers). */
function streamEvents(
  url: string,
  onEvent: (e: TraceEvent) => void,
  onEnd: (error?: ApiError) => void,
): RunSubscription {
  const ctrl = new AbortController();
  let ended = false;
  let sawEnd = false;
  let attempts = 0;
  let lastSeq = 0;
  const finish = (err?: ApiError) => {
    if (ended) return;
    ended = true;
    onEnd(err);
  };

  const connect = async (): Promise<void> => {
    let res: Response;
    try {
      const extra: Record<string, string> = { accept: "text/event-stream" };
      if (lastSeq) extra["last-event-id"] = String(lastSeq);
      res = await fetch(url, { headers: headers(extra), signal: ctrl.signal });
    } catch {
      if (ctrl.signal.aborted) return;
      return retry();
    }
    // 5xx is usually the dev proxy or a restarting server: retry. 4xx means the run is gone or forbidden.
    if (res.status >= 500) return retry();
    if (!res.ok || !res.body) return finish(await parseError(res));
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        let cut: number;
        while ((cut = buf.search(/\r?\n\r?\n/)) >= 0) {
          const block = buf.slice(0, cut);
          buf = buf.slice(cut).replace(/^\r?\n\r?\n/, "");
          const data = block
            .split(/\r?\n/)
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).replace(/^ /, ""))
            .join("\n");
          if (!data) continue;
          const ev = JSON.parse(data) as TraceEvent;
          if (ev.kind === "run_end") sawEnd = true;
          lastSeq = Math.max(lastSeq, ev.seq);
          attempts = 0;
          onEvent(ev);
        }
      }
    } catch {
      if (ctrl.signal.aborted) return;
    }
    if (sawEnd) return finish();
    return retry();
  };

  // Reconnects send Last-Event-ID; if the server replays anyway, the trace reducer drops repeats by seq.
  const retry = async () => {
    if (ctrl.signal.aborted) return;
    attempts += 1;
    // about 90 s of consecutive failures (backoff capped at 8 s) before telling the user
    if (attempts > 14) {
      return finish(new ApiError(0, "stream_lost", "Lost the live trace connection. Reload the page to reconnect."));
    }
    await new Promise((r) => setTimeout(r, Math.min(8000, 500 * 2 ** attempts)));
    return connect();
  };

  void connect();
  return {
    close() {
      ended = true;
      ctrl.abort();
    },
  };
}

export function createHttpApi(): Api {
  const s = (id: string) => `/api/shoots/${encodeURIComponent(id)}`;
  const r = (token: string) => `/api/review/${encodeURIComponent(token)}`;
  return {
    mode: "live",
    listShoots: () => request("GET", "/api/shoots"),
    createDemo: (preset) => request("POST", "/api/shoots/demo", { preset }),
    uploadShoot: (form) => request("POST", "/api/shoots", form),
    getShoot: (id) => request("GET", s(id)),
    setHead: (id, view, version) => request("POST", `${s(id)}/views/${view}/head`, { version }),
    approve: (id, view, approved) => request("POST", `${s(id)}/views/${view}/approve`, { approved }),
    createMask: (id, view, points, box) =>
      request("POST", `${s(id)}/views/${view}/masks`, box ? { points, box } : { points }),
    runQc: (id, views) => request("POST", `${s(id)}/qc`, views ? { views } : {}),
    chat: (id, body) => request("POST", `${s(id)}/chat`, body),
    reel: (id) => request("POST", `${s(id)}/reel`, {}),
    listRuns: (id) => request("GET", `${s(id)}/runs`),
    cancelRun: async (id, run) => {
      await request("POST", `${s(id)}/runs/${run}/cancel`);
    },
    subscribeRun: (id, run, onEvent, onEnd) => streamEvents(`${s(id)}/runs/${run}/events`, onEvent, onEnd),
    blobUrl: (shootId, blob, w) => `${s(shootId)}/blobs/${encodeURIComponent(blob)}${w ? `?w=${w}` : ""}`,
    reelUrl: (shootId) => `${s(shootId)}/reel/latest.mp4`,

    getCompliance: (id) => request("GET", `${s(id)}/compliance`),
    setFindingVerdict: (id, view, finding, verdict) =>
      request("POST", `${s(id)}/views/${encodeURIComponent(view)}/findings/${encodeURIComponent(finding)}`, { verdict }),
    publish: (id) => request("POST", `${s(id)}/publish`),
    patchListing: (id, fields) => request("PATCH", `${s(id)}/listing`, fields),
    createShare: (id, label) => request("POST", `${s(id)}/shares`, { label: label ?? "" }),
    revokeShare: async (id, token) => {
      await request("DELETE", `${s(id)}/shares/${encodeURIComponent(token)}`);
    },
    listComments: (id) => request("GET", `${s(id)}/comments`),
    buildKit: (id) => request("POST", `${s(id)}/kit`, { include_reel: true }),
    getKit: async (id) => {
      try {
        return await request<KitInfo>("GET", `${s(id)}/kit`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    lintListing: (text) => request("POST", "/api/lint/listing", { text }),
    lintRequest: (text) => request("POST", "/api/lint/request", { text }),
    getPublic: (token) => request("GET", `/api/public/${encodeURIComponent(token)}`),
    publicBlobUrl: (token, blob, w) =>
      `/api/public/${encodeURIComponent(token)}/blobs/${encodeURIComponent(blob)}${w ? `?w=${w}` : ""}`,
    getReview: (token) => request("GET", `${r(token)}`),
    reviewBlobUrl: (token, blob, w) => `${r(token)}/blobs/${encodeURIComponent(blob)}${w ? `?w=${w}` : ""}`,
    reviewVerdict: (token, view, status) => request("POST", `${r(token)}/views/${encodeURIComponent(view)}/verdict`, { status }),
    reviewComment: (token, body) => request("POST", `${r(token)}/comments`, body),
    reviewUndo: (token, id) => request("POST", `${r(token)}/comments/${encodeURIComponent(id)}/undo`),
    subscribeReviewRun: (token, run, onEvent, onEnd) => streamEvents(`${r(token)}/runs/${encodeURIComponent(run)}/events`, onEvent, onEnd),
  };
}
