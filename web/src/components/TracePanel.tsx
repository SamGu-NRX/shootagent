import {
  ArrowDown,
  ArchiveRestore,
  Brush,
  Circle,
  CircleAlert,
  Crosshair,
  Eraser,
  Eye,
  Film,
  Flag,
  LayoutGrid,
  Layers,
  type LucideIcon,
  Paintbrush,
  Pipette,
  RotateCcw,
  ScanSearch,
  SquareDashed,
  Sun,
  Thermometer,
  Undo2,
  Wand2,
} from "lucide-react";
import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Finding, Shoot } from "../api/types";
import { clock, duration, findingLabel, inputSummary, metricRows, relTime, toolLabel, usd } from "../lib/format";
import { useStore } from "../state/store";
import { currentStep, type CheckItem, type TimelineItem, type ToolItem, type TraceState } from "../trace/reduce";
import { LiveClock } from "./LiveClock";

const TOOL_ICONS: Record<string, LucideIcon> = {
  list_views: LayoutGrid,
  view_photo: Eye,
  run_qc_checks: ScanSearch,
  find_regions: Crosshair,
  mask_from_box: SquareDashed,
  combine_masks: SquareDashed,
  grow_mask: SquareDashed,
  sample_color: Pipette,
  compare_to_brackets: Layers,
  adjust_exposure: Sun,
  adjust_white_balance: Thermometer,
  recolor_region: Paintbrush,
  remove_object: Eraser,
  generative_fill: Brush,
  restore_from_brackets: ArchiveRestore,
  regroup_brackets: Layers,
  revert: Undo2,
  set_finding: Flag,
  approve_view: Flag,
};

/** Tools that usually return an image: their rows reserve the thumbnail slot so the row doesn't grow. */
const IMAGE_TOOLS = new Set([
  "view_photo",
  "adjust_exposure",
  "adjust_white_balance",
  "recolor_region",
  "remove_object",
  "generative_fill",
  "restore_from_brackets",
  "regroup_brackets",
  "place_on_screen",
  "add_watermark",
  "draw_lot_lines",
  "revert",
]);

const MODE_LABEL: Record<string, string> = { qc: "QC pass", chat: "Chat request", reel: "Reel" };

export function TracePanel() {
  const runs = useStore((s) => s.runs);
  const traces = useStore((s) => s.traces);
  const runId = useStore((s) => s.traceRunId);
  const setRun = useStore((s) => s.setTraceRun);
  const runQc = useStore((s) => s.runQc);
  const shoot = useStore((s) => s.shoot);
  const trace = runId ? traces[runId] : undefined;

  if (!runs.length) {
    return (
      <div className="panel-empty">
        <h2>No agent runs yet</h2>
        <p>
          Run QC to have the agent compare every photo with its brackets. Its plan, each tool call, every self-check and
          any retry are recorded here as they happen.
        </p>
        <button className="btn btn-primary" onClick={runQc} disabled={!shoot}>
          Run QC
        </button>
      </div>
    );
  }

  return (
    <div className="trace">
      {runs.length > 1 && (
        <label className="run-pick">
          <span className="sr-only">Run</span>
          <select value={runId ?? ""} onChange={(e) => setRun(e.target.value)}>
            {[...runs].reverse().map((r) => {
              const t = traces[r.id];
              const req = t?.request ?? r.request;
              const what = req ? `“${req.slice(0, 40)}${req.length > 40 ? "…" : ""}”` : (MODE_LABEL[r.mode ?? ""] ?? r.mode ?? "Run");
              return (
                <option key={r.id} value={r.id}>
                  {what} · {relTime(r.started)}
                  {r.done ? "" : " · running"}
                </option>
              );
            })}
          </select>
        </label>
      )}
      {trace ? <TraceView trace={trace} /> : <div className="panel-empty"><span className="spinner" aria-label="Loading trace" /></div>}
    </div>
  );
}

function TraceView({ trace }: { trace: TraceState }) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const [unseen, setUnseen] = useState(0);
  const lastCount = useRef(trace.items.length);
  // Items present at first render are replayed history: they appear without the entry animation.
  const replayed = useRef<{ run: string | null; keys: Set<string> }>({ run: null, keys: new Set() });
  if (replayed.current.run !== trace.runId) {
    replayed.current = { run: trace.runId, keys: new Set(trace.items.map((i) => i.key)) };
    lastCount.current = trace.items.length;
  }

  // Pinned-to-bottom state comes from a sentinel at the end of the list, so scrolling never reads layout.
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = scroller.current;
    const el = sentinel.current;
    if (!root || !el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        pinned.current = !!entry?.isIntersecting;
        if (pinned.current) setUnseen(0);
      },
      { root, rootMargin: "0px 0px 48px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const added = trace.items.length - lastCount.current;
    lastCount.current = trace.items.length;
    if (pinned.current) el.scrollTop = el.scrollHeight;
    else if (added > 0) setUnseen((n) => n + added);
  }, [trace.items.length, trace.items]);

  useEffect(() => {
    pinned.current = true;
    setUnseen(0);
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [trace.runId]);

  return (
    <>
      <Tape trace={trace} />
      <div className="trace-scroll" ref={scroller}>
        {trace.request && (
          <p className="trace-request">
            <span className="eyebrow">Request</span>“{trace.request}”
          </p>
        )}
        {trace.plan.steps.length > 0 && <Plan trace={trace} />}
        <ol className="timeline" aria-label="Agent steps">
          {trace.items.map((it) => (
            <Row
              key={it.key}
              item={it}
              finding={it.type === "finding" ? trace.findings[it.findingId] : undefined}
              endUsd={it.type === "end" ? trace.totals.usd : undefined}
              lastMessage={it.type === "end" ? trace.messages[trace.messages.length - 1] : undefined}
              animate={!replayed.current.keys.has(it.key)}
            />
          ))}
          {trace.status === "running" && (
            <li className="tr tr-waiting" aria-hidden>
              <span className="tr-time num" />
              <span className="tr-body">
                <span className="wait-bar" />
              </span>
            </li>
          )}
        </ol>
        <div ref={sentinel} aria-hidden />
      </div>
      {unseen > 0 && (
        <button
          className="new-pill"
          onClick={() => {
            const el = scroller.current!;
            el.scrollTo({ top: el.scrollHeight, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
          }}
        >
          <ArrowDown size={12} aria-hidden /> {unseen} new step{unseen > 1 ? "s" : ""}
        </button>
      )}
    </>
  );
}

/** Running totals, like a tape counter. Fixed height so streaming never shifts the timeline. */
function Tape({ trace }: { trace: TraceState }) {
  const t = trace.totals;
  const status = trace.status;
  return (
    <div className="tape" data-status={status}>
      <div className="tape-status">
        {status === "running" ? (
          <>
            <span className="live-dot" aria-hidden />
            <span>Recording</span>
          </>
        ) : status === "failed" ? (
          <span className="tape-failed">Run failed</span>
        ) : (
          <span>{MODE_LABEL[trace.mode ?? ""] ?? "Run"} · done</span>
        )}
        <span className="tape-clock">
          <LiveClock since={trace.startedAt} frozenMs={status === "running" ? null : t.ms} />
        </span>
      </div>
      <TapePlan trace={trace} />
      <dl className="tape-stats">
        <div>
          <dt>Cost</dt>
          <dd className="num">{usd(t.usd)}</dd>
        </div>
        <div>
          <dt>Tool calls</dt>
          <dd className="num">{t.toolCalls}</dd>
        </div>
        <div>
          <dt>Checks</dt>
          <dd className="num">
            <span className="c-pass">{t.checksPassed}</span>
            <span className="tape-sep">/</span>
            <span className={t.checksFailed ? "c-fail" : undefined}>{t.checksFailed}</span>
            <span className="sr-only"> passed / failed</span>
          </dd>
        </div>
        <div>
          <dt>Retries</dt>
          <dd className={t.retries ? "num c-retry" : "num"}>{t.retries}</dd>
        </div>
        <div>
          <dt>Fixed</dt>
          <dd className="num">
            {t.findingsFixed}
            <span className="tape-sep">of</span>
            {t.findingsFound}
          </dd>
        </div>
      </dl>
    </div>
  );
}

/** One-line plan progress pinned in the tape, so the plan stays visible while the timeline scrolls. */
function TapePlan({ trace }: { trace: TraceState }) {
  const steps = trace.plan.steps;
  const cur = currentStep(trace);
  const done = steps.filter((s) => s.done).length;
  const viewLabel = useViewLabel();
  let now: string;
  if (!steps.length) now = trace.status === "running" ? "Planning…" : "No plan recorded";
  else if (cur >= 0) now = `Now: ${steps[cur]!.view ? `${viewLabel(steps[cur]!.view!)} · ` : ""}${steps[cur]!.intent}`;
  else now = `Plan: ${done} of ${steps.length} steps done`;
  return (
    <div className="tape-plan">
      <span className="tape-plan-bar" aria-hidden>
        {steps.map((s, i) => (
          <span key={i} data-state={s.done ? "done" : i === cur ? "current" : "todo"} />
        ))}
      </span>
      <span className="tape-plan-now" title={now}>
        {now}
      </span>
    </div>
  );
}

function Plan({ trace }: { trace: TraceState }) {
  const cur = currentStep(trace);
  const done = trace.plan.steps.filter((s) => s.done).length;
  const viewLabel = useViewLabel();
  return (
    <section className="plan" aria-label="Plan">
      <header className="plan-head">
        <span className="eyebrow">Plan</span>
        <span className="num muted">
          {done} of {trace.plan.steps.length}
        </span>
      </header>
      {trace.plan.text && <p className="plan-text">{trace.plan.text}</p>}
      <ol className="plan-steps">
        {trace.plan.steps.map((s, i) => {
          const state = s.done ? "done" : i === cur ? "current" : trace.status === "running" ? "todo" : "skipped";
          return (
            <li key={i} className="plan-step" data-state={state}>
              <span className="plan-box" aria-hidden>
                <svg viewBox="0 0 12 12" width="12" height="12">
                  <path d="M2.5 6.2l2.3 2.3 4.7-5" />
                </svg>
              </span>
              <span className="plan-intent">
                {s.view && <span className="chip-view">{viewLabel(s.view)}</span>}
                {s.intent}
                <span className="sr-only">
                  {state === "done" ? " (done)" : state === "current" ? " (in progress)" : state === "skipped" ? " (not done)" : ""}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function useViewLabel() {
  const shoot = useStore((s) => s.shoot);
  return (id: string) => {
    const i = shoot?.views.findIndex((v) => v.id === id) ?? -1;
    return i >= 0 ? `${i + 1} ${shoot!.views[i]!.label || id}` : id;
  };
}

function versionBlob(shoot: Shoot | null, view: string | null, version: string | null | undefined) {
  if (!shoot || !view || !version) return null;
  return shoot.views.find((v) => v.id === view)?.versions.find((x) => x.id === version)?.blob ?? null;
}

/** Props are per-item values (not the whole trace) so memo skips unchanged rows while events stream. */
const Row = memo(function Row({
  item,
  finding,
  endUsd,
  lastMessage,
  animate,
}: {
  item: TimelineItem;
  finding?: Finding;
  endUsd?: number;
  lastMessage?: string;
  animate: boolean;
}) {
  const viewLabel = useViewLabel();
  const select = useStore((s) => s.selectView);
  const setSource = useStore((s) => s.setSource);

  const focus = (version?: string | null) => {
    if (!item.view) return;
    select(item.view);
    if (version) setSource("b", { kind: "version", id: version });
  };

  const time = <span className="tr-time num">{clock(item.at)}</span>;
  const viewChip = item.view ? (
    <button className="chip-view" onClick={() => focus()} title="Show this photo" tabIndex={-1}>
      {viewLabel(item.view)}
    </button>
  ) : null;
  const common = { "data-enter": animate || undefined } as const;

  switch (item.type) {
    case "tool":
      return <ToolRow item={item} time={time} viewChip={viewChip} onFocus={focus} common={common} />;
    case "check":
      return <CheckRow item={item} time={time} onFocus={focus} common={common} />;
    case "retry":
      return (
        <li className="tr tr-retry" {...common}>
          {time}
          <div className="tr-body">
            <div className="tr-head">
              <RotateCcw size={13} className="tr-icon" aria-hidden />
              <span className="tr-title">Retry · attempt {item.attempt}</span>
              {viewChip}
            </div>
            <p className="tr-text">{item.reason}</p>
            <p className="tr-next">
              <span>Next</span> {item.next}
            </p>
          </div>
        </li>
      );
    case "thought":
      return (
        <li className="tr tr-thought" {...common}>
          {time}
          <div className="tr-body">
            <p className="tr-text">
              {viewChip}
              {item.text}
            </p>
          </div>
        </li>
      );
    case "finding": {
      const f = finding;
      if (!f) return null;
      const verb = item.first ? "Found" : item.status === "fixed" ? "Fixed" : item.status === "failed" ? "Couldn't fix" : item.status === "dismissed" ? "Dismissed" : "Updated";
      return (
        <li className="tr tr-finding" data-status={item.status} data-first={item.first} {...common}>
          {time}
          <div className="tr-body">
            <div className="tr-head">
              <Crosshair size={13} className="tr-icon" aria-hidden />
              <span className="tr-title">
                {verb} · {findingLabel(f.type)}
              </span>
              {viewChip}
              {item.first && f.status !== "open" && (
                <span className="tr-badge" data-status={f.status}>
                  {f.status === "failed" ? "needs review" : f.status}
                </span>
              )}
            </div>
            {item.first && <p className="tr-text">{f.explanation}</p>}
          </div>
        </li>
      );
    }
    case "message":
      return (
        <li className="tr tr-message" {...common}>
          {time}
          <div className="tr-body">
            <p>{item.text}</p>
          </div>
        </li>
      );
    case "error":
      return (
        <li className="tr tr-error" role="alert" {...common}>
          {time}
          <div className="tr-body">
            <div className="tr-head">
              <CircleAlert size={13} className="tr-icon" aria-hidden />
              <span className="tr-title">Error</span>
            </div>
            <p className="tr-text">{item.message}</p>
          </div>
        </li>
      );
    case "end":
      return (
        <li className="tr tr-end" data-ok={item.ok} {...common}>
          {time}
          <div className="tr-body">
            <div className="tr-head">
              <Flag size={13} className="tr-icon" aria-hidden />
              <span className="tr-title">{item.ok ? "Run finished" : "Run stopped"}</span>
              <span className="tr-dur num">{usd(endUsd ?? 0)}</span>
            </div>
            {/* the agent often ends with its final message verbatim as the summary */}
            {item.summary && !(lastMessage ?? "").startsWith(item.summary) && <p className="tr-text">{item.summary}</p>}
          </div>
        </li>
      );
    case "unknown":
      return (
        <li className="tr tr-thought" {...common}>
          {time}
          <div className="tr-body">
            <p className="tr-text">Unrecognised event “{item.kind}”. The UI may need updating for this server.</p>
          </div>
        </li>
      );
  }
});

function ToolRow({
  item,
  time,
  viewChip,
  onFocus,
  common,
}: {
  item: ToolItem;
  time: React.ReactNode;
  viewChip: React.ReactNode;
  onFocus: (version?: string | null) => void;
  common: object;
}) {
  const shoot = useStore((s) => s.shoot);
  const api = useStore((s) => s.api);
  const Icon = TOOL_ICONS[item.tool] ?? (item.tool.includes("reel") ? Film : Wand2);
  const r = item.result;
  const thumbBlob = r?.images?.[0] ?? versionBlob(shoot, item.view, r?.version);
  const reserveThumb = IMAGE_TOOLS.has(item.tool) || !!thumbBlob;
  const input = inputSummary(item.input);

  return (
    <li className="tr tr-tool" data-status={item.status} data-attempt={item.attempt > 1 || undefined} data-thumb={reserveThumb || undefined} {...common}>
      {time}
      <div className="tr-body">
        <div className="tr-head">
          <Icon size={13} className="tr-icon" aria-hidden />
          <span className="tr-tool-name">{toolLabel(item.tool)}</span>
          {item.attempt > 1 && <span className="tr-attempt">attempt {item.attempt}</span>}
          <span className="tr-dur num">{r ? duration(r.ms) : <span className="spinner spinner-sm" aria-label="Running" />}</span>
        </div>
        {(input || viewChip) && (
          <p className="tr-input num" title={input || undefined}>
            {viewChip}
            <span className="tr-input-text">{input}</span>
          </p>
        )}
        <p className="tr-result" data-ok={r ? r.ok : undefined}>
          {r ? (r.ok ? r.summary : `Failed: ${r.error ?? r.summary}`) : <span className="muted">Running…</span>}
        </p>
        {(r?.version || r?.mask) && (
          <p className="tr-refs num">
            {r.version && (
              <button className="ref-link" onClick={() => onFocus(r.version)} title="Show this version in the viewer">
                → {r.version}
              </button>
            )}
            {r.mask && <span>mask {r.mask}</span>}
          </p>
        )}
      </div>
      {reserveThumb && (
        <button
          className="tr-thumb"
          tabIndex={r?.version ? -1 : 0}
          disabled={!thumbBlob || !item.view}
          onClick={() => onFocus(r?.version)}
          aria-label={r?.version ? `Show ${r.version} in the viewer` : "Show this photo"}
        >
          {thumbBlob && shoot && <img src={api.blobUrl(shoot.id, thumbBlob, 160)} alt="" loading="lazy" decoding="async" />}
        </button>
      )}
    </li>
  );
}

function CheckRow({ item, time, onFocus, common }: { item: CheckItem; time: React.ReactNode; onFocus: (v?: string | null) => void; common: object }) {
  const rows = metricRows(item.metrics);
  const judge = item.judge;
  return (
    <li className="tr tr-check" data-passed={item.passed} data-attempt={item.attempt > 1 || undefined} {...common}>
      {time}
      <div className="tr-body">
        <div className="tr-head">
          <span className="stamp" data-passed={item.passed}>
            {item.passed ? "Pass" : "Fail"}
          </span>
          <span className="tr-title">Self-check</span>
          <button className="ref-link num" onClick={() => onFocus(item.version)} title="Show this version">
            {item.version}
          </button>
        </div>
        {rows.length > 0 && (
          <dl className="metrics">
            {rows.map((m) => (
              <div key={m.label} data-ok={m.ok ?? undefined}>
                <dt>{m.label}</dt>
                <dd className="num">{m.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {judge?.reason && (
          <blockquote className="judge" data-verdict={judge.verdict}>
            <p>{judge.reason}</p>
            <footer className="num">
              {judge.verdict === "unavailable" ? "Vision judge unavailable; metrics decided" : `Vision judge${judge.model ? ` · ${judge.model}` : ""}`}
            </footer>
          </blockquote>
        )}
        {!item.passed && item.failures.length > 0 && (
          <ul className="failures">
            {item.failures.map((f) => (
              <li key={f}>
                <Circle size={5} aria-hidden fill="currentColor" /> {f}
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}
