// The creative lab's HTTP routes (src/shootagent/api/creative.py), and a demo-mode stand-in that replays a
// recorded lab from public/mock/creative/ as if it were running.

import { ApiError, passcode } from "../api/client";
import type { Listing, Shoot } from "../api/types";
import { buildTimeMap, labAt, type TimeMap } from "./replay";
import { simulateLab } from "./simModel";
import type { LabOptions, LabState, LabSummary, SimulateBody, Simulation } from "./types";

export interface ShootInfo {
  id: string;
  title: string;
  listing: Listing | null;
  /** view id -> thumbnail URL */
  photos: Record<string, string>;
  /** view id -> room label */
  labels: Record<string, string>;
}

export interface CreativeApi {
  readonly mode: "live" | "mock";
  /** how often to poll a running lab */
  readonly pollMs: number;
  shoot(shootId: string): Promise<ShootInfo>;
  listLabs(shootId: string): Promise<LabSummary[]>;
  getLab(shootId: string, labId: string): Promise<LabState>;
  startLab(shootId: string, options: Partial<LabOptions>): Promise<{ lab_id: string }>;
  cancelLab(shootId: string, labId: string): Promise<void>;
  resumeLab(shootId: string, labId: string): Promise<{ lab_id: string }>;
  simulate(shootId: string, labId: string, body: Partial<SimulateBody>): Promise<Simulation[]>;
  /** URL of a file under the lab folder: Reel.video or Reel.poster */
  fileUrl(shootId: string, labId: string, path: string): string;
}

// ---- live ----------------------------------------------------------------------------------------

async function apiError(res: Response): Promise<ApiError> {
  const text = await res.text().catch(() => "");
  try {
    const body = JSON.parse(text);
    if (body?.error?.message) return new ApiError(res.status, body.error.code ?? "error", body.error.message);
    if (body?.detail) return new ApiError(res.status, `http_${res.status}`, typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  } catch {
    /* not JSON */
  }
  if (res.status === 502 || res.status === 504 || (res.status === 500 && !text.trim())) {
    return new ApiError(res.status, "server_unreachable", "The ShootAgent server is not responding. Start it with `shootagent serve`, or switch to demo data.");
  }
  return new ApiError(res.status, `http_${res.status}`, `The server returned ${res.status}${text.trim() ? `: ${text.trim().slice(0, 200)}` : ""}.`);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const code = passcode.get();
  if (code) headers["x-demo-passcode"] = code;
  if (body !== undefined) headers["content-type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "network", "Could not reach the ShootAgent server. Check that `shootagent serve` is running.");
  }
  if (!res.ok) throw await apiError(res);
  return (await res.json()) as T;
}

const enc = encodeURIComponent;
const filePath = (path: string) => path.split("/").map(enc).join("/");

export function createHttpCreativeApi(): CreativeApi {
  const s = (id: string) => `/api/shoots/${enc(id)}`;
  const c = (id: string, lab?: string) => `${s(id)}/creative${lab ? `/${enc(lab)}` : ""}`;
  return {
    mode: "live",
    pollMs: 1000,
    async shoot(shootId) {
      const shoot = await request<Shoot>("GET", s(shootId));
      const photos: Record<string, string> = {};
      const labels: Record<string, string> = {};
      for (const v of shoot.views) {
        const blob = v.versions.find((x) => x.id === v.head)?.blob ?? v.finished;
        if (blob) photos[v.id] = `${s(shootId)}/blobs/${enc(blob)}?w=320`;
        labels[v.id] = v.label;
      }
      return { id: shoot.id, title: shoot.title, listing: shoot.listing, photos, labels };
    },
    listLabs: (shootId) => request("GET", c(shootId)),
    getLab: (shootId, labId) => request("GET", c(shootId, labId)),
    startLab: (shootId, options) => request("POST", c(shootId), options),
    cancelLab: async (shootId, labId) => {
      await request("POST", `${c(shootId, labId)}/cancel`);
    },
    resumeLab: (shootId, labId) => request("POST", `${c(shootId, labId)}/resume`),
    simulate: (shootId, labId, body) => request("POST", `${c(shootId, labId)}/simulate`, body),
    fileUrl: (shootId, labId, path) => `${c(shootId, labId)}/files/${filePath(path)}`,
  };
}

// ---- demo data: replay a recorded lab --------------------------------------------------------------

const MOCK = "/mock/creative/";
/** seconds a replay takes at speed 1 */
export const REPLAY_SECONDS = 75;

interface Replay {
  lab: LabState;
  map: TimeMap;
  /** performance.now() when replay second 0 was */
  startedAt: number;
  /** replay second it was cancelled at */
  frozenAt: number | null;
  sims: Simulation[] | null;
}

const delay = <T,>(v: T, ms: number) => new Promise<T>((r) => setTimeout(() => r(v), ms));

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new ApiError(res.status, "mock_missing", `Demo data is missing (${path}). Rebuild it with src/creator/fixture/buildFixture.ts.`);
  return (await res.json()) as T;
}

export function createMockCreativeApi(speed = 1): CreativeApi {
  let recorded: Promise<LabState> | null = null;
  const load = () => (recorded ??= getJson<LabState>(`${MOCK}lab.json`));
  const replays = new Map<string, Replay>();
  let recordedSims: Simulation[] | null = null;

  const replaySecond = (r: Replay) => r.frozenAt ?? (performance.now() - r.startedAt) / 1000;
  const stateOf = (r: Replay): LabState => {
    const second = replaySecond(r);
    let st = labAt(r.lab, r.map.toRecorded(second));
    if (r.frozenAt != null && second < r.map.duration) {
      st = {
        ...st,
        status: "cancelled",
        phases: st.phases.map((p) => (p.status === "running" ? { ...p, status: "failed", ended: st.elapsed, detail: "Cancelled." } : p)),
      };
    }
    return r.sims && st.status === "done" ? { ...st, simulations: r.sims } : st;
  };
  const summary = (st: LabState): LabSummary => ({ id: st.id, created: st.created, status: st.status, phase: st.phase, reels: st.reels.length, usd: st.usage.usd });

  return {
    mode: "mock",
    pollMs: 250,
    async shoot(shootId) {
      const info = await getJson<{ title: string; listing: Listing; views: { id: string; label: string }[] }>(`${MOCK}shoot.json`);
      const photos: Record<string, string> = {};
      const labels: Record<string, string> = {};
      for (const v of info.views) {
        photos[v.id] = `${MOCK}views/${v.id}.jpg`;
        labels[v.id] = v.label;
      }
      return { id: shootId, title: info.title, listing: info.listing, photos, labels };
    },
    async listLabs() {
      const rec = await load();
      const rows = [...replays.values()].map((r) => summary(stateOf(r))).reverse();
      return delay([...rows, summary(rec)], 80);
    },
    async getLab(_shootId, labId) {
      const rec = await load();
      if (labId === rec.id) return recordedSims ? { ...rec, simulations: recordedSims } : rec;
      const r = replays.get(labId);
      if (!r) throw new ApiError(404, "lab_not_found", `No lab ${labId} in the demo data. Pick a lab from the list.`);
      return stateOf(r);
    },
    async startLab(_shootId) {
      // a replay plays the recorded lab as it ran, so it keeps the recording's options
      const rec = await load();
      const running = [...replays.values()].find((r) => stateOf(r).status === "running");
      if (running) throw new ApiError(409, "lab_conflict", "A lab is already running on this shoot. Wait for it to finish or cancel it.");
      const id = `replay-${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}`;
      const lab: LabState = { ...rec, id, created: new Date().toISOString() };
      replays.set(id, { lab, map: buildTimeMap(lab, REPLAY_SECONDS / speed), startedAt: performance.now(), frozenAt: null, sims: null });
      return delay({ lab_id: id }, 250);
    },
    async cancelLab(_shootId, labId) {
      const r = replays.get(labId);
      if (r && r.frozenAt == null && replaySecond(r) < r.map.duration) r.frozenAt = replaySecond(r);
    },
    async resumeLab(_shootId, labId) {
      const r = replays.get(labId);
      if (!r || r.frozenAt == null) throw new ApiError(409, "lab_conflict", "Only a cancelled lab can be resumed.");
      r.startedAt = performance.now() - r.frozenAt * 1000;
      r.frozenAt = null;
      return { lab_id: labId };
    },
    async simulate(_shootId, labId, body) {
      const rec = await load();
      const r = replays.get(labId);
      if (r && stateOf(r).status !== "done") throw new ApiError(409, "lab_conflict", "The launch can be simulated once the lab has finished.");
      const base = r?.lab ?? rec;
      const sims = simulateLab(base, {
        followers: body.followers ?? base.options.followers,
        hours: body.hours ?? base.options.hours,
        seed: body.seed ?? base.options.seed,
      });
      if (r) r.sims = sims;
      else recordedSims = sims;
      return delay(sims, 450);
    },
    fileUrl: (_shootId, _labId, path) => `${MOCK}${filePath(path)}`,
  };
}
