import { ArrowRight, FolderUp, ImagePlus, MessageSquareText, ScanSearch } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ApiError, passcode, type DemoPreset } from "../api/client";
import type { ShootSummary } from "../api/types";
import { openShootRoute } from "../App";
import { relTime } from "../lib/format";
import { message, useStore } from "../state/store";
import { ApiModeSwitch } from "./ApiModeSwitch";
import { NewShootDialog } from "./NewShootDialog";

type Load = { state: "loading" } | { state: "ready"; shoots: ShootSummary[] } | { state: "error"; error: ApiError };

const PRESETS: { id: DemoPreset; title: string; body: string; icon: typeof ScanSearch }[] = [
  {
    id: "qc-tour",
    title: "QC tour",
    body: "Seven views from the AutoHDR defect set, before any checks. Run QC and watch the agent find, fix and verify defects.",
    icon: ScanSearch,
  },
  {
    id: "chat",
    title: "Chat edits",
    body: "Finished photos from one property, ready for requests like “brighten the kitchen in every photo”.",
    icon: MessageSquareText,
  },
];

export function ShootList() {
  const api = useStore((s) => s.api);
  const apiMode = useStore((s) => s.apiMode);
  const setApiMode = useStore((s) => s.setApiMode);
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [creating, setCreating] = useState<DemoPreset | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [code, setCode] = useState("");

  const refresh = useCallback(() => {
    setLoad({ state: "loading" });
    api
      .listShoots()
      .then((shoots) => setLoad({ state: "ready", shoots }))
      .catch((e) => setLoad({ state: "error", error: e instanceof ApiError ? e : new ApiError(0, "error", message(e)) }));
  }, [api]);

  useEffect(refresh, [refresh]);

  const createDemo = async (preset: DemoPreset) => {
    setCreating(preset);
    setCreateError(null);
    try {
      const shoot = await api.createDemo(preset);
      openShootRoute(shoot.id);
    } catch (e) {
      setCreateError(message(e));
    } finally {
      setCreating(null);
    }
  };

  const needsPasscode = load.state === "error" && (load.error.status === 401 || load.error.status === 403);

  return (
    <div className="home">
      <header className="home-bar">
        <Brand />
        <ApiModeSwitch />
      </header>

      <main className="home-main">
        <section className="home-intro" aria-labelledby="home-title">
          <h1 id="home-title">Shoots</h1>
          <p>
            Each shoot holds every view's exposure brackets and AutoHDR's finished photo. ShootAgent checks the finished
            photos against the brackets, fixes what it can prove is wrong, and takes edit requests in chat.
          </p>
        </section>

        <section className="start-grid" aria-label="Start a shoot">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              className="start-card"
              onClick={() => createDemo(p.id)}
              disabled={creating !== null || load.state === "error"}
              aria-busy={creating === p.id}
            >
              <p.icon size={18} strokeWidth={1.6} aria-hidden />
              <span className="start-card-title">
                {p.title}
                <span className="start-card-kicker">Demo shoot</span>
              </span>
              <span className="start-card-body">{p.body}</span>
              <span className="start-card-cta">
                {creating === p.id ? "Building shoot…" : "Create"} <ArrowRight size={14} aria-hidden />
              </span>
            </button>
          ))}
          <button className="start-card" onClick={() => setUploadOpen(true)} disabled={load.state === "error"}>
            <FolderUp size={18} strokeWidth={1.6} aria-hidden />
            <span className="start-card-title">Upload a shoot</span>
            <span className="start-card-body">
              Add each view's bracket JPEGs and the finished photo AutoHDR delivered for it.
            </span>
            <span className="start-card-cta">
              Choose files <ArrowRight size={14} aria-hidden />
            </span>
          </button>
        </section>
        {createError && (
          <p className="inline-error" role="alert">
            {createError}
          </p>
        )}

        <section className="shoot-list" aria-labelledby="list-title">
          <h2 id="list-title" className="eyebrow">
            Recent
          </h2>
          {load.state === "loading" && (
            <ul className="shoot-rows" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <li key={i} className="shoot-row skeleton-row" />
              ))}
            </ul>
          )}
          {load.state === "error" && !needsPasscode && (
            <div className="empty-state" role="alert">
              <h3>Can't reach the ShootAgent server</h3>
              {load.error.code !== "server_unreachable" && load.error.code !== "network" && <p>{load.error.message}</p>}
              <p className="muted">
                Start it from the repo with <code>.venv/Scripts/python -m shootagent.cli serve</code>, then retry.
              </p>
              <div className="row-actions">
                <button className="btn" onClick={refresh}>
                  Retry
                </button>
                {apiMode === "live" && (
                  <button className="btn btn-quiet" onClick={() => setApiMode("mock")}>
                    Use demo data instead
                  </button>
                )}
              </div>
            </div>
          )}
          {needsPasscode && (
            <form
              className="empty-state"
              onSubmit={(e) => {
                e.preventDefault();
                passcode.set(code.trim());
                refresh();
              }}
            >
              <h3>This server needs the demo passcode</h3>
              <label className="field">
                <span>Passcode</span>
                <input value={code} onChange={(e) => setCode(e.target.value)} autoFocus autoComplete="off" />
              </label>
              <button className="btn btn-primary" type="submit" disabled={!code.trim()}>
                Unlock
              </button>
            </form>
          )}
          {load.state === "ready" && load.shoots.length === 0 && (
            <div className="empty-state">
              <ImagePlus size={20} strokeWidth={1.5} aria-hidden />
              <p>No shoots yet. Create a demo shoot above or upload your own brackets and finished photos.</p>
            </div>
          )}
          {load.state === "ready" && load.shoots.length > 0 && (
            <ul className="shoot-rows">
              {load.shoots.map((s) => (
                <li key={s.id}>
                  <a className="shoot-row" href={`#/s/${encodeURIComponent(s.id)}`}>
                    <span className="shoot-thumb">
                      {s.thumb && <img src={api.blobUrl(s.id, s.thumb, 320)} alt="" loading="lazy" decoding="async" />}
                    </span>
                    <span className="shoot-title">{s.title}</span>
                    <span className="shoot-meta num">{s.view_count} views</span>
                    <span className="shoot-meta">{relTime(s.created_at)}</span>
                    <ArrowRight size={14} className="shoot-go" aria-hidden />
                  </a>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
      {uploadOpen && <NewShootDialog onClose={() => setUploadOpen(false)} />}
    </div>
  );
}

export function Brand() {
  return (
    <a className="brand" href="#/" aria-label="ShootAgent, all shoots">
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden>
        <path d="M2 7V2h5M18 7V2h-5M2 13v5h5M18 13v5h-5" stroke="currentColor" strokeWidth="1.8" fill="none" />
        <circle cx="10" cy="10" r="2.4" fill="var(--agent)" />
      </svg>
      <span>ShootAgent</span>
    </a>
  );
}
