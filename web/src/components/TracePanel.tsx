import {
  ArrowDown,
  ArchiveRestore,
  Brush,
  Clapperboard,
  Languages,
  Mic,
  PenLine,
  Tag,
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
import { MessageText } from "./MessageText";

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
  label_views: Tag,
  plan_film: Clapperboard,
  prepare_shots: Film,
  write_script: PenLine,
  translate_script: Languages,
  narrate: Mic,
  assemble_film: Film,
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

const MODE_LABEL: Record<string, string> = { qc: "QC pass", chat: "Chat request", reel: "Reel", film: "Listing film" };
const MOTION: Record<string, string> = { generated: "generated clip", depth: "depth move", still: "slow push-in" };

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
                  {r.done ? "" : r.status && r.status !== "running" ? ` · ${r.status}` : " · running"}
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

  // A finished run shows its final report from the first line. Pinned to the very bottom, a long report showed
  // only its last lines, and its opening sat under the edge of the pane.
  const ended = trace.status === "done" || trace.status === "failed";
  const showReport = () => {
    const el = scroller.current;
    const reports = el?.querySelectorAll<HTMLElement>(".tr-message");
    const last = reports?.[reports.length - 1];
    if (el && last) el.scrollTop = Math.min(el.scrollHeight, last.offsetTop - el.offsetTop - 8);
  };

  useEffect(() => {
    pinned.current = true;
    setUnseen(0);
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
    if (ended) showReport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trace.runId]);

  // a live run that ends while the view follows it
  useLayoutEffect(() => {
    if (ended && pinned.current) showReport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ended]);

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
          {segments(trace.items).map((seg) =>
            seg.kind === "verdicts" ? (
              <VerdictBlock
                key={seg.items[0]!.key}
                items={seg.items}
                findings={trace.findings}
                animate={!replayed.current.keys.has(seg.items[0]!.key)}
              />
            ) : (
              <Row
                key={seg.item.key}
                item={seg.item}
                finding={
                  seg.item.type === "finding"
                    ? trace.findings[seg.item.findingId]
                    : seg.item.type === "tool" && seg.item.tool === "set_finding"
                      ? trace.findings[String(seg.item.input.finding)]
                      : undefined
                }
                candidates={
                  seg.item.type === "tool" && seg.item.candidates
                    ? seg.item.candidates.map((id) => trace.findings[id]).filter((f): f is Finding => !!f)
                    : undefined
                }
                endUsd={seg.item.type === "end" ? trace.totals.usd : undefined}
                lastMessage={seg.item.type === "end" ? trace.messages[trace.messages.length - 1] : undefined}
                animate={!replayed.current.keys.has(seg.item.key)}
              />
            ),
          )}
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
  const openCandidates = Object.values(trace.findings).filter((f) => f.status === "open").length;
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
            {/* while the agent is still adjudicating, open candidates aren't defects yet */}
            {status === "running" && openCandidates > 0 ? (
              <span className="tape-sep">· {openCandidates} to check</span>
            ) : (
              <>
                <span className="tape-sep">of</span>
                {t.findingsFound}
              </>
            )}
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
  candidates,
  endUsd,
  lastMessage,
  animate,
}: {
  item: TimelineItem;
  finding?: Finding;
  candidates?: Finding[];
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
      if (item.tool === "set_finding") return <VerdictRow item={item} finding={finding} time={time} viewChip={viewChip} common={common} />;
      return <ToolRow item={item} candidates={candidates} time={time} viewChip={viewChip} onFocus={focus} common={common} />;
    case "check":
      return <CheckRow item={item} time={time} onFocus={focus} common={common} />;
    case "shot":
      return (
        <li className="tr tr-shot" data-fell-back={item.fellBack || undefined} {...common}>
          {time}
          <div className="tr-body">
            <div className="tr-head">
              <Clapperboard size={13} className="tr-icon" aria-hidden />
              <span className="tr-title">
                Shot {item.shot + 1} plays a {MOTION[item.source] ?? item.source}
              </span>
              {viewChip}
            </div>
            {item.fellBack && (
              <p className="tr-text">
                Planned as a {MOTION[item.planned] ?? item.planned}; it fell back after its check.
              </p>
            )}
          </div>
        </li>
      );
    case "retry":
      return (
        <li className="tr tr-retry" {...common}>
          {time}
          <div className="tr-body">
            <div className="tr-head">
              <RotateCcw size={13} className="tr-icon" aria-hidden />
              <span className="tr-title">
                Retry · try {item.attempt + 1}
                {item.max_attempts ? ` of ${item.max_attempts}` : ""}
              </span>
              {viewChip}
            </div>
            <p className="tr-text tr-clamp" title={item.reason}>
              {item.reason}
            </p>
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
            <MessageText text={item.text} />
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
  candidates,
  time,
  viewChip,
  onFocus,
  common,
}: {
  item: ToolItem;
  candidates?: Finding[];
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
        {item.media && (
          <p className="tr-refs num">
            {item.media.made} clip{item.media.made === 1 ? "" : "s"} or narrations · {item.media.cached} from cache
          </p>
        )}
        {candidates && candidates.length > 0 && <CandidateChips findings={candidates} />}
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
          <span className="tr-title">{item.clip ? `Clip check · shot ${(item.shot ?? 0) + 1}` : "Self-check"}</span>
          {item.clip ? (
            <span className="tr-attempt num">clip {item.clip.slice(0, 8)}</span>
          ) : (
            <button className="ref-link num" onClick={() => onFocus(item.version)} title="Show this version">
              {item.version}
            </button>
          )}
        </div>
        {item.clip && item.images && item.images.length > 0 && <ClipSheet blob={item.images[0]!} />}
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
        {!item.passed && otherFailures(item).length > 0 && (
          <ul className="failures">
            {otherFailures(item).map((f) => (
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

/** The contact sheet the clip checker looked at: frames of the generated clip beside its photo. */
function ClipSheet({ blob }: { blob: string }) {
  const shoot = useStore((s) => s.shoot);
  const api = useStore((s) => s.api);
  if (!shoot) return null;
  return (
    <a className="clip-sheet" href={api.blobUrl(shoot.id, blob)} target="_blank" rel="noreferrer" title="Open the contact sheet">
      <img src={api.blobUrl(shoot.id, blob, 640)} alt="Contact sheet of the generated clip's frames" loading="lazy" decoding="async" />
    </a>
  );
}

type Segment = { kind: "item"; item: TimelineItem } | { kind: "verdicts"; items: ToolItem[] };

const isVerdict = (it: TimelineItem): it is ToolItem => it.type === "tool" && it.tool === "set_finding";

/**
 * Consecutive set_finding calls form one block: QC records its verdicts on dozens of candidates in a
 * burst, interleaving fixed and dismissed, and one row each would bury the edits.
 */
function segments(items: TimelineItem[]): Segment[] {
  const out: Segment[] = [];
  for (const it of items) {
    const last = out[out.length - 1];
    if (isVerdict(it)) {
      if (last?.kind === "verdicts") last.items.push(it);
      else out.push({ kind: "verdicts", items: [it] });
    } else out.push({ kind: "item", item: it });
  }
  return out;
}

const DISMISSALS_SHOWN = 4;

function VerdictBlock({ items, findings, animate }: { items: ToolItem[]; findings: Record<string, Finding>; animate: boolean }) {
  const viewLabel = useViewLabel();
  const [open, setOpen] = useState(false);
  const kept = items.filter((it) => it.input.status !== "dismissed");
  const dismissed = items.filter((it) => it.input.status === "dismissed");
  const shown = open ? dismissed : dismissed.slice(0, DISMISSALS_SHOWN);
  const hidden = dismissed.length - shown.length;
  const line = (it: ToolItem) => {
    const f = findings[String(it.input.finding)];
    const status = String(it.input.status ?? "");
    return (
      <li key={it.key} data-status={status}>
        {it.view && <span className="chip-view">{viewLabel(it.view)}</span>}
        <span className="verdict-type">
          {status === "fixed" ? "Fixed · " : status === "failed" ? "Needs review · " : status === "open" ? "Reopened · " : ""}
          {f ? findingLabel(f.type) : String(it.input.finding)}
        </span>
        <span className="verdict-note" title={String(it.input.note ?? "")}>
          {String(it.input.note ?? "")}
        </span>
      </li>
    );
  };
  const counts = [
    kept.filter((it) => it.input.status === "fixed").length && `${kept.filter((it) => it.input.status === "fixed").length} fixed`,
    kept.filter((it) => it.input.status === "failed").length && `${kept.filter((it) => it.input.status === "failed").length} need review`,
    dismissed.length && `${dismissed.length} dismissed`,
  ].filter(Boolean);
  return (
    <li className="tr tr-verdicts" data-enter={animate || undefined}>
      <span className="tr-time num">{clock(items[0]!.at)}</span>
      <div className="tr-body">
        <div className="tr-head">
          <Flag size={13} className="tr-icon" aria-hidden />
          <span className="tr-title">
            Verdict{items.length > 1 ? "s" : ""} on {items.length} candidate{items.length > 1 ? "s" : ""}
          </span>
          <span className="tr-dur">{counts.join(" · ")}</span>
        </div>
        {kept.length > 0 && <ul className="verdict-list verdict-kept">{kept.map(line)}</ul>}
        {dismissed.length > 0 && (
          <>
            <p className="verdict-sub">Dismissed as legitimate edits or noise</p>
            <ul className="verdict-list">{shown.map(line)}</ul>
          </>
        )}
        {hidden > 0 && (
          <button className="link link-quiet" onClick={() => setOpen(true)}>
            Show {hidden} more
          </button>
        )}
      </div>
    </li>
  );
}

function VerdictRow({
  item,
  finding,
  time,
  viewChip,
  common,
}: {
  item: ToolItem;
  finding?: Finding;
  time: React.ReactNode;
  viewChip: React.ReactNode;
  common: object;
}) {
  const status = String(item.input.status ?? "");
  const verb = status === "fixed" ? "Marked fixed" : status === "failed" ? "Needs review" : status === "open" ? "Reopened" : status;
  return (
    <li className="tr tr-finding" data-status={status} {...common}>
      {time}
      <div className="tr-body">
        <div className="tr-head">
          <Crosshair size={13} className="tr-icon" aria-hidden />
          <span className="tr-title">
            {verb} · {finding ? findingLabel(finding.type) : String(item.input.finding)}
          </span>
          {viewChip}
        </div>
        {typeof item.input.note === "string" && item.input.note && <p className="tr-text">{item.input.note}</p>}
        {item.result && !item.result.ok && <p className="tr-text c-fail">{item.result.error ?? item.result.summary}</p>}
      </div>
    </li>
  );
}

/** Candidates a QC check reported, grouped by type; each chip follows the agent's later verdict. */
function CandidateChips({ findings }: { findings: Finding[] }) {
  return (
    <ul className="cand-chips" aria-label="Candidates">
      {findings.map((f) => (
        <li key={f.id} className="cand" data-status={f.status} title={f.explanation}>
          {findingLabel(f.type)}
          {f.confidence != null && <span className="num"> {f.confidence.toFixed(2)}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Check failures, minus the "vision judge: …" line that repeats the quoted judge reason. */
function otherFailures(item: CheckItem): string[] {
  const reason = item.judge?.reason?.trim();
  return item.failures.filter((f) => !(reason && f.includes(reason)));
}
