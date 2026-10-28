import { ArrowLeft, CheckCheck, Play } from "lucide-react";
import { useEffect } from "react";
import { openListRoute } from "../App";
import { activeRun, useStore } from "../state/store";
import { ApiModeSwitch } from "./ApiModeSwitch";
import { Filmstrip } from "./Filmstrip";
import { LiveClock } from "./LiveClock";
import { RightPanel } from "./RightPanel";
import { Brand } from "./ShootList";
import { Viewer } from "./Viewer";

const isTyping = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

export function Workspace({ shootId }: { shootId: string }) {
  const openShoot = useStore((s) => s.openShoot);
  const closeShoot = useStore((s) => s.closeShoot);
  const shoot = useStore((s) => s.shoot);
  const error = useStore((s) => s.shootError);
  const api = useStore((s) => s.api);

  useEffect(() => {
    void openShoot(shootId);
    return closeShoot;
  }, [shootId, openShoot, closeShoot, api]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.defaultPrevented) return;
      const st = useStore.getState();
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        void (e.shiftKey ? st.redo(true) : st.undo(true));
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        void st.redo(true);
      } else if (mod || e.altKey) {
        return;
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        if ((e.target as HTMLElement | null)?.closest?.("[role=slider],[role=tablist],[role=radiogroup]")) return;
        e.preventDefault();
        st.stepView(e.key === "ArrowLeft" ? -1 : 1);
      } else if (e.key === "\\") {
        e.preventDefault();
        st.toggleBefore();
      } else if (e.key === "f") st.toggle("showFindings");
      else if (e.key === "m") st.toggle("showMask");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (error) {
    return (
      <div className="home">
        <header className="home-bar">
          <Brand />
          <ApiModeSwitch />
        </header>
        <main className="home-main">
          <div className="empty-state" role="alert">
            <h3>Couldn't open this shoot</h3>
            <p>{error.message}</p>
            <div className="row-actions">
              <button className="btn" onClick={() => void openShoot(shootId)}>
                Retry
              </button>
              <button className="btn btn-quiet" onClick={openListRoute}>
                Back to shoots
              </button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="ws" aria-busy={!shoot}>
      <TopBar />
      <Filmstrip />
      <Viewer />
      <RightPanel />
    </div>
  );
}

function TopBar() {
  const shoot = useStore((s) => s.shoot);
  const apiMode = useStore((s) => s.apiMode);
  const runs = useStore((s) => s.runs);
  const live = useStore(activeRun);
  const runQc = useStore((s) => s.runQc);
  const approveAll = useStore((s) => s.approveAll);
  const busy = useStore((s) => s.busyAction);

  const views = shoot?.views ?? [];
  const open = views.reduce((n, v) => n + v.findings.filter((f) => f.status === "open" || f.status === "failed").length, 0);
  const fixed = views.reduce((n, v) => n + v.findings.filter((f) => f.status === "fixed").length, 0);
  const approved = views.filter((v) => v.approved).length;
  const qcRan = runs.some((r) => r.mode === "qc");

  return (
    <header className="ws-top">
      <a className="icon-btn" href="#/" aria-label="Back to shoots">
        <ArrowLeft size={16} />
      </a>
      <div className="ws-title">
        <h1>{shoot?.title ?? <span className="skeleton-text" style={{ width: 160 }} />}</h1>
        {shoot && (
          <p className="ws-sub num">
            {views.length} views
            {qcRan && (
              <>
                <span aria-hidden> · </span>
                {fixed} fixed
                <span aria-hidden> · </span>
                {open} need attention
              </>
            )}
            <span aria-hidden> · </span>
            {approved} approved
          </p>
        )}
      </div>
      {apiMode === "mock" && (
        <span className="badge-demo" title="Runs are scripted replays in the browser, not the real agent.">
          Demo data
        </span>
      )}
      <div className="ws-top-actions">
        {live && (
          <span className="live-pill" role="status">
            <span className="live-dot" aria-hidden />
            <span className="live-label">Agent working</span> <LiveClock since={live.startedAt} />
          </span>
        )}
        <button className="btn" onClick={approveAll} disabled={!shoot || approved === views.length || busy === "approve-all"}>
          <CheckCheck size={14} aria-hidden /> Approve all
        </button>
        <button
          className={qcRan ? "btn" : "btn btn-primary"}
          onClick={runQc}
          disabled={!shoot || busy === "qc" || live?.mode === "qc"}
        >
          <Play size={13} aria-hidden /> {qcRan ? "Run QC again" : "Run QC"}
        </button>
      </div>
    </header>
  );
}
