// The creator dashboard's state: which lab is open, its LabState (polled while it runs), the shoot's photos,
// a re-run launch, and the concept drawer.

import { create } from "zustand";
import { createHttpCreativeApi, createMockCreativeApi, type CreativeApi, type ShootInfo } from "./api";
import { reconcile } from "./replay";
import type { LabOptions, LabState, LabSummary, SimulateBody, Simulation } from "./types";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

let mockApi: CreativeApi | null = null;
let liveApi: CreativeApi | null = null;
/** One API per mode, so a demo replay keeps running when the page is left and reopened. */
export function creativeApi(mode: "live" | "mock"): CreativeApi {
  if (mode === "mock") {
    const speed = Number(new URLSearchParams(location.search).get("speed") ?? "1") || 1;
    return (mockApi ??= createMockCreativeApi(speed));
  }
  return (liveApi ??= createHttpCreativeApi());
}

export const creatorRoute = (shootId: string, labId?: string | null) =>
  `#/creator/${encodeURIComponent(shootId)}${labId ? `/${encodeURIComponent(labId)}` : ""}`;

interface CreatorState {
  api: CreativeApi | null;
  shootId: string | null;
  shoot: ShootInfo | null;
  labs: LabSummary[];
  labsLoaded: boolean;
  labId: string | null;
  lab: LabState | null;
  loading: boolean;
  /** opening failed: nothing to show */
  error: string | null;
  /** polling failed: the last state is still shown */
  stale: string | null;
  /** an action failed (start, cancel, simulate) */
  notice: string | null;
  starting: boolean;
  /** the latest re-run of the launch; replaces lab.simulations */
  sims: Simulation[] | null;
  simBusy: boolean;
  /** bumps on every new set of simulations, so the launch replays from hour 0 */
  simRun: number;
  drawer: string | null;

  open(api: CreativeApi, shootId: string, labId: string | null): Promise<void>;
  close(): void;
  start(options: Partial<LabOptions>): Promise<void>;
  cancel(): Promise<void>;
  resume(): Promise<void>;
  simulate(body: Partial<SimulateBody>): Promise<void>;
  openConcept(id: string | null): void;
  dismissNotice(): void;
}

let generation = 0;
let pollTimer: ReturnType<typeof setTimeout> | null = null;

export const useCreator = create<CreatorState>()((set, get) => {
  const stopPolling = () => {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
  };

  const refreshLabs = async (gen: number) => {
    const { api, shootId } = get();
    if (!api || !shootId) return;
    try {
      const labs = await api.listLabs(shootId);
      if (gen === generation) set({ labs, labsLoaded: true });
    } catch {
      /* the picker keeps its last list */
    }
  };

  const poll = (gen: number, failures = 0) => {
    stopPolling();
    const { api, shootId, labId } = get();
    if (!api || !shootId || !labId) return;
    pollTimer = setTimeout(async () => {
      try {
        const next = await api.getLab(shootId, labId);
        if (gen !== generation) return;
        const lab = reconcile(get().lab, next);
        const labs = get().labs.map((l) =>
          l.id === next.id ? { ...l, status: next.status, phase: next.phase, reels: next.reels.length, usd: next.usage.usd } : l,
        );
        set({ lab, labs, stale: null });
        if (next.status === "running") poll(gen);
        else void refreshLabs(gen);
      } catch (e) {
        if (gen !== generation) return;
        set({ stale: `Lost contact with the lab (${message(e)}). Retrying…` });
        poll(gen, failures + 1);
      }
    }, failures ? Math.min(8000, api.pollMs * 2 ** failures) : api.pollMs);
  };

  const load = async (gen: number, labId: string) => {
    const { api, shootId } = get();
    if (!api || !shootId) return;
    set({ labId, loading: true, error: null, stale: null, sims: null, drawer: null });
    try {
      const lab = await api.getLab(shootId, labId);
      if (gen !== generation) return;
      set({ lab, loading: false });
      if (lab.status === "running") poll(gen);
    } catch (e) {
      if (gen === generation) set({ lab: null, loading: false, error: message(e) });
    }
  };

  return {
    api: null,
    shootId: null,
    shoot: null,
    labs: [],
    labsLoaded: false,
    labId: null,
    lab: null,
    loading: false,
    error: null,
    stale: null,
    notice: null,
    starting: false,
    sims: null,
    simBusy: false,
    simRun: 0,
    drawer: null,

    async open(api, shootId, labId) {
      const same = get().api === api && get().shootId === shootId;
      if (same && labId && labId === get().labId) return;
      const gen = ++generation;
      stopPolling();
      if (!same) {
        set({ api, shootId, shoot: null, labs: [], labsLoaded: false, lab: null, labId: null, error: null, notice: null });
        api.shoot(shootId).then(
          (shoot) => gen === generation && set({ shoot }),
          () => {
            /* photos and the listing line are optional */
          },
        );
      }
      if (labId) {
        void refreshLabs(gen);
        return load(gen, labId);
      }
      set({ loading: true });
      try {
        const labs = await api.listLabs(shootId);
        if (gen !== generation) return;
        set({ labs, labsLoaded: true });
        const latest = labs[0];
        if (!latest) {
          set({ loading: false, lab: null, labId: null });
          return;
        }
        history.replaceState(null, "", creatorRoute(shootId, latest.id));
        return load(gen, latest.id);
      } catch (e) {
        if (gen === generation) set({ loading: false, error: message(e) });
      }
    },

    close() {
      generation++;
      stopPolling();
      set({ lab: null, labId: null, shootId: null, api: null, drawer: null, sims: null });
    },

    async start(options) {
      const { api, shootId } = get();
      if (!api || !shootId) return;
      set({ starting: true, notice: null });
      try {
        const { lab_id } = await api.startLab(shootId, options);
        location.hash = creatorRoute(shootId, lab_id);
      } catch (e) {
        set({ notice: message(e) });
      } finally {
        set({ starting: false });
      }
    },

    async cancel() {
      const { api, shootId, labId } = get();
      if (!api || !shootId || !labId) return;
      try {
        await api.cancelLab(shootId, labId);
      } catch (e) {
        set({ notice: message(e) });
      }
    },

    async resume() {
      const { api, shootId, labId } = get();
      if (!api || !shootId || !labId) return;
      try {
        const { lab_id } = await api.resumeLab(shootId, labId);
        const gen = ++generation;
        if (lab_id !== labId) location.hash = creatorRoute(shootId, lab_id);
        else {
          set({ lab: { ...get().lab!, status: "running" } });
          poll(gen);
        }
      } catch (e) {
        set({ notice: message(e) });
      }
    },

    async simulate(body) {
      const { api, shootId, labId } = get();
      if (!api || !shootId || !labId) return;
      set({ simBusy: true, notice: null });
      try {
        const sims = await api.simulate(shootId, labId, body);
        if (get().labId === labId) set((s) => ({ sims, simRun: s.simRun + 1 }));
      } catch (e) {
        set({ notice: message(e) });
      } finally {
        set({ simBusy: false });
      }
    },

    openConcept(id) {
      set({ drawer: id });
    },
    dismissNotice() {
      set({ notice: null });
    },
  };
});
