// In-browser stand-in for the FastAPI server: same Api interface, scripted runs, timed trace events.

import { ApiError, type Api, type MaskPoint } from "../client";
import type { Mask, RunSummary, Shoot, ShootSummary, TraceEvent } from "../types";
import { makeShoot } from "./data";
import { chatScript, qcScript, reelScript, type Step } from "./scripts";

interface MockRun {
  id: string;
  shootId: string;
  mode: string;
  started: string;
  events: TraceEvent[];
  done: boolean;
  usd: number;
  summary: string | null;
  subs: Set<(e: TraceEvent) => void>;
  ends: Set<() => void>;
  /** runs queued behind this one start when it ends */
  after: (() => void)[];
}

const clone = <T>(x: T): T => structuredClone(x);

function runId(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${Math.random().toString(16).slice(2, 8)}`;
}

/** Draws a soft blob around the clicked points, standing in for SAM. Returns a PNG data URL. */
function fakeMask(points: MaskPoint[]): { url: string; bbox: number[]; area: number } {
  const w = 400;
  const h = 266;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const g = canvas.getContext("2d")!;
  g.fillStyle = "#000";
  g.fillRect(0, 0, w, h);
  const inc = points.filter((p) => p[2] === 1);
  g.fillStyle = "#fff";
  g.filter = "blur(4px)";
  for (const [x, y] of inc) {
    g.beginPath();
    g.ellipse(x * w, y * h, w * 0.06, h * 0.09, 0, 0, Math.PI * 2);
    g.fill();
  }
  g.fillStyle = "#000";
  for (const [x, y] of points.filter((p) => p[2] === 0)) {
    g.beginPath();
    g.arc(x * w, y * h, w * 0.04, 0, Math.PI * 2);
    g.fill();
  }
  const xs = inc.map((p) => p[0]);
  const ys = inc.map((p) => p[1]);
  const bbox = [Math.min(...xs) - 0.06, Math.min(...ys) - 0.09, Math.max(...xs) + 0.06, Math.max(...ys) + 0.09].map((v) =>
    Math.min(1, Math.max(0, v)),
  );
  return { url: canvas.toDataURL("image/png"), bbox, area: Math.min(1, inc.length * 0.017) };
}

export function createMockApi(speed = 1): Api {
  const shoots = new Map<string, Shoot>();
  const runs = new Map<string, MockRun>();
  let counter = 0;

  const fresh = makeShoot("demo-maple", "412 Maple Court");
  shoots.set(fresh.id, fresh);

  const get = (id: string) => {
    const s = shoots.get(id);
    if (!s) throw new ApiError(404, "not_found", `No shoot "${id}". Go back to the shoot list and pick one.`);
    return s;
  };

  function start(shoot: Shoot, mode: string, steps: Step[], instant = false, behind?: MockRun): MockRun {
    const run: MockRun = {
      id: runId() + (instant ? `-${++counter}` : ""),
      shootId: shoot.id,
      mode,
      started: new Date().toISOString(),
      events: [],
      done: false,
      usd: 0,
      summary: null,
      subs: new Set(),
      ends: new Set(),
      after: [],
    };
    runs.set(run.id, run);
    shoot.runs.push(run.id);
    const ctx = { shoot, runId: run.id };
    let clock = Date.now() - (instant ? steps.reduce((a, s) => a + s.gap, 0) : 0);
    const t0 = clock;

    const emit = (step: Step, seq: number) => {
      const data = step.data(ctx) as Record<string, unknown>;
      if (step.kind === "usage") run.usd += Number(data.usd ?? 0);
      if (step.kind === "run_end") {
        data.ms = clock - t0;
        data.usd = Math.round(run.usd * 10000) / 10000;
        run.done = true;
        run.summary = String(data.summary ?? "");
      }
      const ev = { run: run.id, seq, t: new Date(clock).toISOString(), kind: step.kind, view: step.view ?? null, data } as TraceEvent;
      run.events.push(ev);
      run.subs.forEach((f) => f(ev));
      if (run.done) {
        run.ends.forEach((f) => f());
        run.after.splice(0).forEach((f) => f());
      }
    };

    if (instant) {
      steps.forEach((step, i) => {
        clock += step.gap;
        emit(step, i + 1);
      });
    } else {
      let i = 0;
      if (behind) {
        behind.after.push(() => {
          clock = Date.now();
          next();
        });
        return run;
      }
      function next() {
        const step = steps[i];
        if (!step) return;
        setTimeout(() => {
          clock = Date.now();
          emit(step, ++i);
          next();
        }, step.gap / speed);
      }
      next();
    }
    return run;
  }

  function queueOrStart(shoot: Shoot, mode: string, build: () => Step[]): { run_id: string } {
    // docs/api.md: a request while a run is active is queued, not run in parallel.
    const busy = [...runs.values()].filter((r) => r.shootId === shoot.id && !r.done).pop();
    return { run_id: start(shoot, mode, build(), false, busy).id };
  }

  const delay = <T>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

  return {
    mode: "mock",
    async listShoots(): Promise<ShootSummary[]> {
      return delay(
        [...shoots.values()]
          .sort((a, b) => b.created_at.localeCompare(a.created_at))
          .map((s) => ({ id: s.id, title: s.title, created_at: s.created_at, view_count: s.views.length, thumb: s.views[0]?.finished ?? null })),
      );
    },
    async createDemo(preset) {
      counter += 1;
      const shoot = makeShoot(`demo-${preset}-${counter}`, preset === "chat" ? "88 Larkspur Lane" : `412 Maple Court (${counter})`);
      if (preset === "chat") start(shoot, "qc", qcScript(shoot), true);
      shoots.set(shoot.id, shoot);
      return delay(clone(shoot), 400);
    },
    async uploadShoot() {
      throw new ApiError(501, "mock_upload", "Upload needs the live server. Start `shootagent serve` and reload with ?api=live.");
    },
    async getShoot(id) {
      return delay(clone(get(id)), 60);
    },
    async setHead(id, viewId, version) {
      const v = get(id).views.find((x) => x.id === viewId);
      if (!v?.versions.some((x) => x.id === version)) throw new ApiError(404, "not_found", `No version ${version}.`);
      v.head = version;
      return delay(clone(v), 60);
    },
    async approve(id, viewId, approved) {
      const v = get(id).views.find((x) => x.id === viewId)!;
      v.approved = approved;
      return delay(clone(v), 60);
    },
    async createMask(id, viewId, points) {
      if (!points.some((p) => p[2] === 1)) throw new ApiError(400, "no_points", "Shift-click at least one point inside the object.");
      const { url, bbox, area } = fakeMask(points);
      const mask: Mask = {
        id: "m_" + Math.random().toString(16).slice(2, 10),
        view: viewId,
        blob: url,
        source: "user",
        query: null,
        bbox,
        area,
        created_at: new Date().toISOString(),
      };
      get(id).masks[mask.id] = mask;
      return delay(clone(mask), 500);
    },
    async runQc(id) {
      const shoot = get(id);
      return queueOrStart(shoot, "qc", () => qcScript(shoot));
    },
    async chat(id, body) {
      const shoot = get(id);
      return queueOrStart(shoot, "chat", () => chatScript(shoot, body.message, body.views, body.mask));
    },
    async reel(id) {
      const shoot = get(id);
      if (!shoot.views.some((v) => v.approved)) {
        throw new ApiError(400, "nothing_approved", "Approve at least one photo before building a reel.");
      }
      return queueOrStart(shoot, "reel", () => reelScript(shoot));
    },
    async listRuns(id): Promise<RunSummary[]> {
      return delay(
        [...runs.values()]
          .filter((r) => r.shootId === id)
          .map((r) => ({ id: r.id, mode: r.mode, started: r.started, done: r.done, usd: r.usd, summary: r.summary })),
        40,
      );
    },
    async cancelRun() {
      throw new ApiError(501, "mock_cancel", "Demo runs can't be cancelled; they finish in under a minute.");
    },
    subscribeRun(_id, run, onEvent, onEnd) {
      const r = runs.get(run);
      if (!r) {
        queueMicrotask(() => onEnd(new ApiError(404, "not_found", `No run ${run}.`)));
        return { close() {} };
      }
      let open = true;
      const sub = (e: TraceEvent) => open && onEvent(e);
      const end = () => open && onEnd();
      queueMicrotask(() => {
        if (!open) return;
        r.events.forEach(sub);
        if (r.done) end();
        else {
          r.subs.add(sub);
          r.ends.add(end);
        }
      });
      return {
        close() {
          open = false;
          r.subs.delete(sub);
          r.ends.delete(end);
        },
      };
    },
    blobUrl(_shootId, blob, w) {
      if (blob.startsWith("data:")) return blob;
      if (w && w <= 400 && blob.endsWith(".jpg") && !blob.includes("_w360")) return `/mock/${blob.replace(".jpg", "_w360.jpg")}`;
      return `/mock/${blob}`;
    },
    reelUrl: () => "/mock/reel-not-rendered.mp4",
  };
}
