import { ArrowUp, ChevronRight, SquareDashed, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { pct } from "../lib/format";
import { activeRun, currentView, useStore } from "../state/store";
import type { TraceState, ViewOutcome, ViewResult } from "../trace/reduce";
import { LiveClock } from "./LiveClock";
import { radioKeys } from "./radioKeys";

const QUICK = [
  "Make the kitchen brighter in every photo",
  "Remove the trash bins",
  "Match the wall and floor colours to the original",
  "Warm up the interiors slightly",
];

const OUTCOME_TEXT: Record<ViewOutcome, string> = {
  applied: "Applied",
  not_needed: "Not needed",
  failed: "Couldn't apply",
  pending: "Working",
};

export function ChatPanel() {
  const runs = useStore((s) => s.runs);
  const traces = useStore((s) => s.traces);
  const pending = useStore((s) => s.pendingChat);
  const scroller = useRef<HTMLDivElement>(null);
  const chatRuns = runs.filter((r) => r.mode === "chat").map((r) => traces[r.id]).filter((t): t is TraceState => !!t);
  const lastLen = chatRuns.reduce((n, t) => n + t.items.length, 0);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lastLen, chatRuns.length, pending]);

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scroller}>
        {chatRuns.length === 0 && !pending && (
          <div className="chat-empty">
            <h2>Ask for edits across the shoot</h2>
            <p>
              Describe the change once. ShootAgent works out which photos it applies to, edits them, checks each result,
              and tells you why it skipped the rest.
            </p>
          </div>
        )}
        {chatRuns.map((t) => (
          <Exchange key={t.runId} trace={t} />
        ))}
        {pending && (
          <div className="exchange">
            <p className="msg-user">{pending.text}</p>
            {pending.error ? (
              <p className="inline-error" role="alert">
                {pending.error}
              </p>
            ) : (
              <p className="msg-status muted">Sending…</p>
            )}
          </div>
        )}
      </div>
      <Composer />
    </div>
  );
}

function Exchange({ trace }: { trace: TraceState }) {
  const setTab = useStore((s) => s.setTab);
  const setRun = useStore((s) => s.setTraceRun);
  const running = trace.status === "running";
  const results = Object.entries(trace.viewResults);
  const steps = trace.items.filter((i) => i.type === "tool").length;
  // Applied first, then the rest, so the changes are what the eye lands on.
  const order: ViewOutcome[] = ["applied", "failed", "pending", "not_needed"];
  results.sort((a, b) => order.indexOf(a[1].outcome) - order.indexOf(b[1].outcome));
  const changed = results.filter(([, r]) => r.outcome !== "not_needed");
  const skipped = results.filter(([, r]) => r.outcome === "not_needed");

  const openTrace = () => {
    if (trace.runId) setRun(trace.runId);
    setTab("trace");
  };

  return (
    <div className="exchange">
      <p className="msg-user">{trace.request}</p>
      {running && (
        <p className="msg-status">
          <span className="live-dot" aria-hidden /> Working · {steps} tool call{steps === 1 ? "" : "s"} · <LiveClock since={trace.startedAt} />
          <button className="link" onClick={openTrace}>
            Watch trace
          </button>
        </p>
      )}
      {trace.messages.map((m, i) => (
        <p key={i} className="msg-agent">
          {m}
        </p>
      ))}
      {results.length > 0 && !running && (
        <div className="results" aria-label="Result per photo">
          {changed.length > 0 && (
            <ul>
              {changed.map(([viewId, r]) => (
                <li key={viewId}>
                  <ResultRow viewId={viewId} r={r} />
                </li>
              ))}
            </ul>
          )}
          {skipped.length > 0 && (
            <details className="skipped" open={changed.length === 0}>
              <summary>
                <ChevronRight size={13} aria-hidden />
                Not needed in {skipped.length} photo{skipped.length > 1 ? "s" : ""}
              </summary>
              <ul>
                {skipped.map(([viewId, r]) => (
                  <li key={viewId}>
                    <ResultRow viewId={viewId} r={r} />
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {!running && (
        <button className="link link-quiet" onClick={openTrace}>
          {steps} tool call{steps === 1 ? "" : "s"} · see trace
        </button>
      )}
    </div>
  );
}

function ResultRow({ viewId, r }: { viewId: string; r: ViewResult }) {
  const shoot = useStore((s) => s.shoot);
  const api = useStore((s) => s.api);
  const select = useStore((s) => s.selectView);
  const setSource = useStore((s) => s.setSource);
  const view = shoot?.views.find((v) => v.id === viewId);
  const blob = r.version
    ? view?.versions.find((v) => v.id === r.version)?.blob
    : (view?.versions.find((v) => v.id === view.head)?.blob ?? view?.finished);
  return (
    <button
      className="result"
      data-outcome={r.outcome}
      onClick={() => {
        select(viewId);
        if (r.version) setSource("b", { kind: "version", id: r.version });
      }}
    >
      <span className="result-thumb">{blob && shoot && <img src={api.blobUrl(shoot.id, blob, 160)} alt="" loading="lazy" />}</span>
      <span className="result-label">{view?.label || viewId}</span>
      <span className="result-outcome">{OUTCOME_TEXT[r.outcome]}</span>
      <span className="result-reason">{r.reason}</span>
    </button>
  );
}

function Composer() {
  const send = useStore((s) => s.sendChat);
  const view = useStore(currentView);
  const shoot = useStore((s) => s.shoot);
  const live = useStore(activeRun);
  const mask = useStore((s) => s.attachedMask);
  const detach = useStore((s) => s.detachMask);
  const pending = useStore((s) => s.pendingChat);
  const [text, setText] = useState("");
  const [scope, setScope] = useState<"all" | "view">("all");
  const ta = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [text]);

  useEffect(() => {
    if (mask) ta.current?.focus();
  }, [mask]);

  const sending = !!pending && !pending.error;
  const submit = async (value = text) => {
    if (!value.trim() || sending) return;
    const ok = await send(value, scope);
    if (ok) setText("");
  };

  const maskView = mask ? shoot?.views.find((v) => v.id === mask.view) : null;

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="quick" role="group" aria-label="Suggested requests">
        {QUICK.map((q) => (
          <button key={q} type="button" className="quick-btn" onClick={() => void submit(q)} disabled={!shoot || sending}>
            {q}
          </button>
        ))}
      </div>
      <div className="composer-box">
        {mask && (
          <span className="mask-chip">
            <SquareDashed size={12} aria-hidden />
            Mask on {maskView?.label ?? mask.view}
            {mask.area != null && <span className="num"> · {pct(mask.area)}</span>}
            <button type="button" aria-label="Remove mask" onClick={detach}>
              <X size={12} />
            </button>
          </span>
        )}
        <label className="sr-only" htmlFor="chat-input">
          Request
        </label>
        <textarea
          id="chat-input"
          ref={ta}
          rows={1}
          value={text}
          placeholder={mask ? "What should happen to the marked area?" : "Describe an edit, e.g. “remove the hose from the lawn”"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void submit();
            }
          }}
        />
        <div className="composer-row">
          <div className="seg seg-sm" role="radiogroup" aria-label="Apply to" onKeyDown={radioKeys}>
            <button type="button" role="radio" aria-checked={scope === "all"} tabIndex={scope === "all" ? 0 : -1} className="seg-btn" onClick={() => setScope("all")}>
              All photos
            </button>
            <button type="button" role="radio" aria-checked={scope === "view"} tabIndex={scope === "view" ? 0 : -1} className="seg-btn" onClick={() => setScope("view")} disabled={!view}>
              This photo{view ? ` · ${view.label || view.id}` : ""}
            </button>
          </div>
          <span className="composer-hint muted">{live ? "Queues after the current run" : "Enter to send"}</span>
          <button type="submit" className="send" disabled={!text.trim() || sending} aria-label={live ? "Queue request" : "Send request"}>
            <ArrowUp size={15} strokeWidth={2.2} />
          </button>
        </div>
      </div>
    </form>
  );
}
