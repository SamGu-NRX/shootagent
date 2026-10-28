import type { Mask, RunSummary, Shoot, ShootSummary, TraceEvent, View } from "./types";

export type DemoPreset = "qc-tour" | "chat";

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
    if (attempts > 4) {
      return finish(new ApiError(0, "stream_lost", "Lost the live trace connection. Reload the page to reconnect."));
    }
    await new Promise((r) => setTimeout(r, 500 * 2 ** attempts));
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
  };
}
