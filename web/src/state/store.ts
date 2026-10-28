import { create } from "zustand";
import { ApiError, createHttpApi, type Api, type MaskPoint, type RunSubscription } from "../api/client";
import { createMockApi } from "../api/mock/mockApi";
import type { Mask, RunSummary, Shoot, TraceEvent, View } from "../api/types";
import { emptyTrace, reduceTrace, type TraceState } from "../trace/reduce";

export type ApiMode = "live" | "mock";

export type Source =
  | { kind: "finished" }
  | { kind: "head" }
  | { kind: "version"; id: string }
  | { kind: "reference" }
  | { kind: "bracket"; index: number };

export type CompareMode = "split" | "before" | "after";
export type RightTab = "chat" | "trace" | "reel" | "deliver";
export type CanvasTone = "dark" | "mid" | "black";

interface MaskDraft {
  viewId: string;
  points: MaskPoint[];
  mask: Mask | null;
  loading: boolean;
  error: string | null;
}

/** A chat message sent but not yet acknowledged with a run id. */
interface PendingChat {
  text: string;
  error: string | null;
}

interface State {
  api: Api;
  apiMode: ApiMode;
  shoot: Shoot | null;
  shootError: ApiError | null;
  loadingShoot: boolean;
  viewId: string | null;
  a: Source;
  b: Source;
  compare: CompareMode;
  /** compare mode to return to when `\` releases "before" */
  lastCompare: CompareMode;
  showFindings: boolean;
  showMask: boolean;
  canvasTone: CanvasTone;
  runs: RunSummary[];
  traces: Record<string, TraceState>;
  traceRunId: string | null;
  tab: RightTab;
  maskDraft: MaskDraft | null;
  attachedMask: Mask | null;
  pendingChat: PendingChat | null;
  toast: { text: string; tone: "info" | "error" } | null;
  busyAction: string | null;
  /** set when a version change came from the keyboard, so the viewer swaps without a crossfade */
  instantSwap: boolean;

  setApiMode(mode: ApiMode): void;
  openShoot(id: string): Promise<void>;
  closeShoot(): void;
  refreshShoot(): Promise<void>;
  selectView(id: string): void;
  stepView(delta: number): void;
  setSource(which: "a" | "b", src: Source): void;
  setCompare(mode: CompareMode): void;
  toggleBefore(): void;
  toggle(key: "showFindings" | "showMask"): void;
  setCanvasTone(t: CanvasTone): void;
  setTab(t: RightTab): void;
  setTraceRun(id: string): void;
  runQc(): Promise<void>;
  sendChat(text: string, scope: "all" | "view"): Promise<boolean>;
  buildReel(): Promise<void>;
  moveHead(viewId: string, versionId: string, instant?: boolean): Promise<void>;
  undo(instant?: boolean): Promise<void>;
  redo(instant?: boolean): Promise<void>;
  approve(viewId: string, approved: boolean): Promise<void>;
  approveAll(): Promise<void>;
  addMaskPoint(x: number, y: number, label: 0 | 1): void;
  clearMaskDraft(): void;
  attachDraftMask(): void;
  detachMask(): void;
  showToast(text: string, tone?: "info" | "error"): void;
  dismissToast(): void;
}

function initialMode(): ApiMode {
  const q = new URLSearchParams(location.search).get("api");
  if (q === "mock" || q === "live") {
    localStorage.setItem("shootagent.api", q);
    return q;
  }
  return localStorage.getItem("shootagent.api") === "mock" ? "mock" : "live";
}

function makeApi(mode: ApiMode): Api {
  const speed = Number(new URLSearchParams(location.search).get("speed") ?? "1") || 1;
  return mode === "mock" ? createMockApi(speed) : createHttpApi();
}

const subs = new Map<string, RunSubscription>();
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let maskTimer: ReturnType<typeof setTimeout> | null = null;
let toastTimer: ReturnType<typeof setTimeout> | null = null;

export const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function currentView(s: Pick<State, "shoot" | "viewId">): View | null {
  return s.shoot?.views.find((v) => v.id === s.viewId) ?? null;
}

export function activeRun(s: Pick<State, "traces">): TraceState | null {
  return Object.values(s.traces).find((t) => t.status === "running") ?? null;
}

const mode0 = initialMode();

export const useStore = create<State>()((set, get) => {
  const closeSubs = () => {
    subs.forEach((s) => s.close());
    subs.clear();
  };

  const watchRun = (shootId: string, runId: string) => {
    if (subs.has(runId)) return;
    const onEvent = (ev: TraceEvent) => {
      set((st) => ({ traces: { ...st.traces, [runId]: reduceTrace(st.traces[runId] ?? emptyTrace(), ev) } }));
      if (ev.kind === "tool_result" || ev.kind === "check" || ev.kind === "finding" || ev.kind === "run_end") {
        scheduleRefresh();
      }
      if (ev.kind === "run_end") void get().api.listRuns(shootId).then((runs) => get().shoot?.id === shootId && set({ runs }));
    };
    const sub = get().api.subscribeRun(shootId, runId, onEvent, (err) => {
      subs.delete(runId);
      if (err) get().showToast(err.message, "error");
    });
    subs.set(runId, sub);
  };

  const scheduleRefresh = () => {
    if (refreshTimer) return;
    refreshTimer = setTimeout(() => {
      refreshTimer = null;
      void get().refreshShoot();
    }, 250);
  };

  const replaceView = (view: View) =>
    set((st) => (st.shoot ? { shoot: { ...st.shoot, views: st.shoot.views.map((v) => (v.id === view.id ? view : v)) } } : {}));

  const guard = async (label: string, fn: () => Promise<void>) => {
    set({ busyAction: label });
    try {
      await fn();
    } catch (e) {
      get().showToast(message(e), "error");
    } finally {
      set({ busyAction: null });
    }
  };

  return {
    api: makeApi(mode0),
    apiMode: mode0,
    shoot: null,
    shootError: null,
    loadingShoot: false,
    viewId: null,
    a: { kind: "finished" },
    b: { kind: "head" },
    compare: "split",
    lastCompare: "split",
    showFindings: true,
    showMask: false,
    canvasTone: (localStorage.getItem("shootagent.canvas") as CanvasTone | null) ?? "dark",
    runs: [],
    traces: {},
    traceRunId: null,
    tab: "trace",
    maskDraft: null,
    attachedMask: null,
    pendingChat: null,
    toast: null,
    busyAction: null,
    instantSwap: false,

    setApiMode(mode) {
      localStorage.setItem("shootagent.api", mode);
      closeSubs();
      set({ apiMode: mode, api: makeApi(mode), shoot: null, shootError: null, runs: [], traces: {}, traceRunId: null });
    },

    async openShoot(id) {
      closeSubs();
      set({
        loadingShoot: true,
        shootError: null,
        shoot: null,
        runs: [],
        traces: {},
        traceRunId: null,
        maskDraft: null,
        attachedMask: null,
        pendingChat: null,
        a: { kind: "finished" },
        b: { kind: "head" },
        compare: "split",
      });
      try {
        const api = get().api;
        const [shoot, runs] = await Promise.all([api.getShoot(id), api.listRuns(id)]);
        const latest = runs[runs.length - 1];
        set({
          shoot,
          runs,
          viewId: shoot.views[0]?.id ?? null,
          traceRunId: latest?.id ?? null,
          loadingShoot: false,
        });
        runs.forEach((r) => watchRun(id, r.id));
      } catch (e) {
        set({ loadingShoot: false, shootError: e instanceof ApiError ? e : new ApiError(0, "error", message(e)) });
      }
    },

    closeShoot() {
      closeSubs();
      set({ shoot: null, runs: [], traces: {}, traceRunId: null, shootError: null });
    },

    async refreshShoot() {
      const s = get().shoot;
      if (!s) return;
      try {
        const shoot = await get().api.getShoot(s.id);
        if (get().shoot?.id === shoot.id) set({ shoot, instantSwap: false });
      } catch (e) {
        get().showToast(message(e), "error");
      }
    },

    selectView(id) {
      if (id === get().viewId) return;
      set({ viewId: id, b: { kind: "head" }, a: { kind: "finished" }, maskDraft: null });
    },

    stepView(delta) {
      const { shoot, viewId } = get();
      if (!shoot?.views.length) return;
      const i = shoot.views.findIndex((v) => v.id === viewId);
      const n = shoot.views.length;
      get().selectView(shoot.views[(i + delta + n) % n]!.id);
    },

    setSource(which, src) {
      set(which === "a" ? { a: src } : { b: src });
    },
    setCompare(mode) {
      set({ compare: mode, lastCompare: mode === "before" ? get().lastCompare : mode });
    },
    toggleBefore() {
      const { compare, lastCompare } = get();
      if (compare === "before") set({ compare: lastCompare === "before" ? "split" : lastCompare });
      else set({ compare: "before", lastCompare: compare });
    },
    toggle(key) {
      set({ [key]: !get()[key] } as Pick<State, typeof key>);
    },
    setCanvasTone(t) {
      localStorage.setItem("shootagent.canvas", t);
      set({ canvasTone: t });
    },
    setTab(t) {
      set({ tab: t });
    },
    setTraceRun(id) {
      set({ traceRunId: id });
    },

    async runQc() {
      const shoot = get().shoot;
      if (!shoot) return;
      await guard("qc", async () => {
        const { run_id } = await get().api.runQc(shoot.id);
        set((st) => ({
          traceRunId: run_id,
          tab: "trace",
          runs: [...st.runs, { id: run_id, mode: "qc", started: new Date().toISOString(), done: false, usd: 0, summary: null }],
        }));
        watchRun(shoot.id, run_id);
      });
    },

    async sendChat(text, scope) {
      const { shoot, viewId, attachedMask } = get();
      if (!shoot || !text.trim()) return false;
      set({ pendingChat: { text, error: null } });
      try {
        const views = scope === "view" && viewId ? [viewId] : attachedMask ? [attachedMask.view] : undefined;
        const { run_id } = await get().api.chat(shoot.id, {
          message: text.trim(),
          ...(views ? { views } : {}),
          ...(attachedMask ? { mask: attachedMask.id } : {}),
        });
        set((st) => ({
          pendingChat: null,
          attachedMask: null,
          maskDraft: null,
          // the overlay was on for drawing; leave it on and it hides the edit the request produces
          showMask: attachedMask ? false : st.showMask,
          traceRunId: run_id,
          runs: [...st.runs, { id: run_id, mode: "chat", started: new Date().toISOString(), done: false, usd: 0, summary: null }],
          // Optimistic seed so the message shows before the server's run_start echo arrives.
          traces: { ...st.traces, [run_id]: { ...emptyTrace(), runId: run_id, mode: "chat", request: text.trim(), status: "running" } },
        }));
        watchRun(shoot.id, run_id);
        return true;
      } catch (e) {
        set({ pendingChat: { text, error: message(e) } });
        return false;
      }
    },

    async buildReel() {
      const shoot = get().shoot;
      if (!shoot) return;
      await guard("reel", async () => {
        const { run_id } = await get().api.reel(shoot.id);
        set((st) => ({
          traceRunId: run_id,
          runs: [...st.runs, { id: run_id, mode: "reel", started: new Date().toISOString(), done: false, usd: 0, summary: null }],
        }));
        watchRun(shoot.id, run_id);
      });
    },

    async moveHead(viewId, versionId, instant = false) {
      const shoot = get().shoot;
      if (!shoot) return;
      await guard("head", async () => {
        set({ instantSwap: instant });
        replaceView(await get().api.setHead(shoot.id, viewId, versionId));
      });
    },

    async undo(instant = false) {
      const v = currentView(get());
      const head = v?.versions.find((x) => x.id === v.head);
      if (!v || !head?.parent) return;
      await get().moveHead(v.id, head.parent, instant);
    },

    async redo(instant = false) {
      const v = currentView(get());
      if (!v) return;
      const children = v.versions.filter((x) => x.parent === v.head);
      const next = children[children.length - 1];
      if (next) await get().moveHead(v.id, next.id, instant);
    },

    async approve(viewId, approved) {
      const shoot = get().shoot;
      if (!shoot) return;
      await guard("approve", async () => replaceView(await get().api.approve(shoot.id, viewId, approved)));
    },

    async approveAll() {
      const shoot = get().shoot;
      if (!shoot) return;
      await guard("approve-all", async () => {
        const pending = shoot.views.filter((v) => !v.approved);
        const views = await Promise.all(pending.map((v) => get().api.approve(shoot.id, v.id, true)));
        views.forEach(replaceView);
      });
    },

    addMaskPoint(x, y, label) {
      const { shoot, viewId, maskDraft } = get();
      if (!shoot || !viewId) return;
      const points: MaskPoint[] = [...(maskDraft?.viewId === viewId ? maskDraft.points : []), [x, y, label]];
      set({ maskDraft: { viewId, points, mask: maskDraft?.viewId === viewId ? maskDraft.mask : null, loading: true, error: null } });
      if (maskTimer) clearTimeout(maskTimer);
      maskTimer = setTimeout(async () => {
        try {
          const mask = await get().api.createMask(shoot.id, viewId, points);
          const d = get().maskDraft;
          if (d?.viewId === viewId && d.points.length === points.length) set({ maskDraft: { ...d, mask, loading: false }, showMask: true });
        } catch (e) {
          const d = get().maskDraft;
          if (d?.viewId === viewId) set({ maskDraft: { ...d, loading: false, error: message(e) } });
        }
      }, 300);
    },
    clearMaskDraft() {
      set({ maskDraft: null });
    },
    attachDraftMask() {
      const d = get().maskDraft;
      if (d?.mask) set({ attachedMask: d.mask, maskDraft: null, tab: "chat" });
    },
    detachMask() {
      set({ attachedMask: null });
    },

    showToast(text, tone = "info") {
      if (toastTimer) clearTimeout(toastTimer);
      set({ toast: { text, tone } });
      // Errors stay until dismissed or replaced; info clears itself.
      if (tone === "info") toastTimer = setTimeout(() => set({ toast: null }), 3500);
    },
    dismissToast() {
      if (toastTimer) clearTimeout(toastTimer);
      set({ toast: null });
    },
  };
});

/** Blob name for a compare source on a view, or null when the view lacks it. */
export function sourceBlob(view: View, src: Source): string | null {
  switch (src.kind) {
    case "finished":
      return view.finished ?? view.versions[0]?.blob ?? null;
    case "head":
      return view.versions.find((v) => v.id === view.head)?.blob ?? view.finished;
    case "version":
      return view.versions.find((v) => v.id === src.id)?.blob ?? null;
    case "reference":
      return view.reference;
    case "bracket":
      return view.brackets.find((b) => b.index === src.index)?.blob ?? null;
  }
}

export function sourceLabel(view: View, src: Source): string {
  switch (src.kind) {
    case "finished":
      return "AutoHDR original";
    case "head":
      return view.head && view.head !== view.versions[0]?.id ? `Current · ${view.head}` : "Current";
    case "version":
      return src.id;
    case "reference":
      return "Bracket fusion";
    case "bracket": {
      const b = view.brackets.find((x) => x.index === src.index);
      return `Bracket ${src.index + 1}${b?.ev != null ? ` (${b.ev > 0 ? "+" : ""}${b.ev.toFixed(1)} EV)` : ""}`;
    }
  }
}
