import { ArrowLeft, Check, ChevronDown, Minus, MonitorPlay, Play, RotateCw, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { radioKeys } from "../components/radioKeys";
import { phaseLabel, SECTIONS, stepPhases, type StepPhase } from "./phases";
import { usePresenter } from "./present/Presenter";
import { creatorRoute, useCreator } from "./store";
import { DEFAULT_OPTIONS, type LabOptions, type LabState, type PhaseStatus } from "./types";
import { fmtClock, fmtSpan, fmtUsd, goToSection, useDismiss, useLabClock, useReducedMotion } from "./ui";

// ---- header: one line -----------------------------------------------------------------------------

export function LabHeader() {
  const lab = useCreator((s) => s.lab);
  const shoot = useCreator((s) => s.shoot);
  const shootId = useCreator((s) => s.shootId);
  const mode = useCreator((s) => s.api?.mode);
  const elapsed = useLabClock(lab, mode === "live");
  const listing = shoot?.listing;
  const title = listing?.address || lab?.title || shoot?.title || "";
  const meta = [
    listing?.price,
    listing?.beds != null ? `${listing.beds} bed` : null,
    listing?.baths != null ? `${listing.baths} bath` : null,
    listing?.sqft != null ? `${listing.sqft.toLocaleString("en-US")} sq ft` : null,
  ].filter(Boolean);

  return (
    <header className="cl-top">
      <div className="cl-wrap cl-top-row">
        <a className="icon-btn" href={shootId ? `#/s/${encodeURIComponent(shootId)}` : "#/"} aria-label="Back to the shoot">
          <ArrowLeft size={16} />
        </a>
        <h1 className="cl-top-title">{title || <span className="skeleton-text" style={{ width: 180 }} />}</h1>
        <p className="cl-top-meta">
          {["Creative lab", ...meta].map((m) => (
            <span key={m}>{m}</span>
          ))}
        </p>
        <div className="cl-top-right">
          {mode === "mock" && (
            <span className="cl-top-note" title="A recorded lab replayed in the browser, sped up. No agents are running.">
              Demo replay
            </span>
          )}
          {lab && <StatusPill lab={lab} elapsed={elapsed} />}
          <LabPicker />
          {lab?.status === "done" && lab.evaluation && (
            <button className="btn btn-primary" onClick={() => usePresenter.getState().show()} title="A one-minute, full-screen walkthrough of this lab">
              <MonitorPlay size={14} aria-hidden /> Present
            </button>
          )}
          <RunControls />
        </div>
      </div>
    </header>
  );
}

/** State, time and spend in one line: "Done · 10:50 · $10.22". */
function StatusPill({ lab, elapsed }: { lab: LabState; elapsed: number }) {
  const running = lab.status === "running";
  const current = running ? (lab.phases.find((p) => p.key === lab.phase) ?? lab.phases.find((p) => p.status === "running")) : undefined;
  const ENDED: Record<string, string> = { done: "Done", failed: "Failed", cancelled: "Cancelled" };
  const state = running ? (current ? phaseLabel(current.key) : "Running") : (ENDED[lab.status] ?? lab.status);
  return (
    <span className="cl-pill" data-status={lab.status} role="status" title={[current?.detail, lab.error, `${lab.usage.calls} model calls`].filter(Boolean).join(" · ")}>
      {running && <span className="live-dot" aria-hidden />}
      <span className="cl-pill-state">{state}</span>
      <span className="num">{fmtClock(running ? elapsed : lab.elapsed)}</span>
      <span className="num">{fmtUsd(lab.usage.usd)}</span>
    </span>
  );
}

function LabPicker() {
  const labs = useCreator((s) => s.labs);
  const labId = useCreator((s) => s.labId);
  const shootId = useCreator((s) => s.shootId);
  if (!shootId || labs.length < 2) return null;
  const when = (iso: string) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  };
  return (
    <label className="cl-picker" title="Switch lab">
      <span className="sr-only">Lab</span>
      <select value={labId ?? ""} onChange={(e) => (location.hash = creatorRoute(shootId, e.target.value))}>
        {labs.map((l) => (
          <option key={l.id} value={l.id}>
            {when(l.created)}
            {l.status === "done" ? "" : ` · ${l.status}`} · {fmtUsd(l.usd)}
          </option>
        ))}
      </select>
      <ChevronDown size={13} aria-hidden />
    </label>
  );
}

const OPTIONS_KEY = "shootagent.creative.options";
function savedOptions(): LabOptions {
  try {
    return { ...DEFAULT_OPTIONS, ...(JSON.parse(localStorage.getItem(OPTIONS_KEY) ?? "{}") as Partial<LabOptions>) };
  } catch {
    return DEFAULT_OPTIONS;
  }
}

function RunControls() {
  const lab = useCreator((s) => s.lab);
  const starting = useCreator((s) => s.starting);
  const start = useCreator((s) => s.start);
  const cancel = useCreator((s) => s.cancel);
  const resume = useCreator((s) => s.resume);
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState(savedOptions);
  const wrap = useRef<HTMLDivElement>(null);
  const running = lab?.status === "running";
  useDismiss(open, () => setOpen(false), wrap);

  const set = <K extends keyof LabOptions>(k: K, v: LabOptions[K]) =>
    setOpts((o) => {
      const next = { ...o, [k]: v };
      localStorage.setItem(OPTIONS_KEY, JSON.stringify(next));
      return next;
    });
  const run = () => {
    setOpen(false);
    const { finalists, rounds, voice, single_agent, render_single_agent } = opts;
    void start({ finalists, rounds, voice, single_agent, render_single_agent: single_agent && render_single_agent });
  };

  return (
    <div className="cl-run" ref={wrap}>
      {running && (
        <button className="btn btn-quiet" onClick={() => void cancel()}>
          <Square size={11} aria-hidden /> Cancel
        </button>
      )}
      {(lab?.status === "cancelled" || lab?.status === "failed") && (
        <button className="btn btn-quiet" onClick={() => void resume()}>
          <RotateCw size={13} aria-hidden /> Resume
        </button>
      )}
      <div className="cl-split">
        <button className="btn" onClick={run} disabled={running || starting}>
          <Play size={12} aria-hidden /> {starting ? "Starting…" : "Run creative lab"}
        </button>
        <button className="btn cl-split-more" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="dialog" aria-label="Lab options" disabled={running || starting}>
          <ChevronDown size={14} aria-hidden />
        </button>
      </div>
      {open && (
        <div className="cl-pop" role="dialog" aria-label="Lab options">
          <p className="cl-pop-title">Run a new lab</p>
          <div className="cl-field">
            <span>Creative finalists</span>
            <Seg value={opts.finalists} options={[1, 2, 3].map((n) => [n, String(n)])} onChange={(v) => set("finalists", v)} label="Creative finalists" />
          </div>
          <div className="cl-field">
            <span>Rounds</span>
            <Seg
              value={opts.rounds}
              options={[
                [1, "Pitch only"],
                [2, "Pitch, critique, remix"],
              ]}
              onChange={(v) => set("rounds", v)}
              label="Rounds"
            />
          </div>
          <Toggle checked={opts.voice} onChange={(v) => set("voice", v)} label="Voiceover" hint="Text to speech on the creative reels" />
          <Toggle checked={opts.single_agent} onChange={(v) => set("single_agent", v)} label="Single-agent control" hint="One agent, same brief, no room: the comparison" />
          <Toggle
            checked={opts.single_agent && opts.render_single_agent}
            disabled={!opts.single_agent}
            onChange={(v) => set("render_single_agent", v)}
            label="Render the single agent's reel"
            hint="Adds reel S to the screening"
          />
          <button className="btn btn-primary cl-pop-go" onClick={run}>
            <Play size={12} aria-hidden /> Start lab
          </button>
        </div>
      )}
    </div>
  );
}

function Seg<T extends number>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg seg-sm" role="radiogroup" aria-label={label} onKeyDown={radioKeys}>
      {options.map(([v, text]) => (
        <button key={v} role="radio" aria-checked={value === v} tabIndex={value === v ? 0 : -1} className="seg-btn" onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

function Toggle({ checked, onChange, label, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint: string; disabled?: boolean }) {
  return (
    <label className="cl-toggle" data-disabled={disabled || undefined}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="cl-switch" aria-hidden />
      <span className="cl-toggle-text">
        {label}
        <small>{hint}</small>
      </span>
    </label>
  );
}

// ---- four stages, sticky ----------------------------------------------------------------------------

const STATUS_WORD: Record<PhaseStatus, string> = { pending: "waiting", running: "running", done: "done", failed: "failed", skipped: "skipped" };

function PhaseIcon({ status }: { status: PhaseStatus }) {
  switch (status) {
    case "done":
      return <Check size={12} strokeWidth={2.2} aria-hidden />;
    case "failed":
      return <X size={12} strokeWidth={2.2} aria-hidden />;
    case "skipped":
      return <Minus size={12} aria-hidden />;
    case "running":
      return <span className="cl-stage-pulse" aria-hidden />;
    default:
      return <span className="cl-phase-wait" aria-hidden />;
  }
}

export interface StageSummary {
  key: string;
  label: string;
  status: PhaseStatus;
  /** 0-1 */
  progress: number;
  meta: string;
  phases: StepPhase[];
}

/** One stage's state from its phases: failed beats running beats done; meta is the live phase or the time it took. */
export function summarizeStage(key: string, label: string, phases: StepPhase[], now: number, labRunning: boolean): StageSummary {
  const n = phases.length || 1;
  const finished = phases.filter((p) => p.status === "done" || p.status === "skipped").length;
  const running = phases.filter((p) => p.status === "running");
  let status: PhaseStatus = "pending";
  if (phases.some((p) => p.status === "failed")) status = "failed";
  else if (running.length || (labRunning && finished > 0 && finished < phases.length)) status = "running";
  else if (phases.length && finished === phases.length) status = phases.every((p) => p.status === "skipped") ? "skipped" : "done";
  const starts = phases.map((p) => p.started).filter((x): x is number => x != null);
  const ends = phases.map((p) => p.ended).filter((x): x is number => x != null);
  let meta = "";
  if (running.length) {
    const p = running.reduce((a, b) => ((b.started ?? 0) > (a.started ?? 0) ? b : a));
    meta = `${p.label} · ${fmtClock(Math.max(0, now - (p.started ?? now)))}`;
  } else if (status === "done" && starts.length && ends.length) meta = fmtSpan(Math.max(...ends) - Math.min(...starts));
  else if (status === "skipped") meta = "skipped";
  return { key, label, status, progress: (finished + running.length * 0.5) / n, meta, phases };
}

/** The section whose top has passed under the sticky bar. */
function useActiveSection(ids: string[]): string | null {
  const [active, setActive] = useState<string | null>(null);
  useEffect(() => {
    let raf = 0;
    const measure = () => {
      raf = 0;
      const line = (document.querySelector(".cl-stages")?.getBoundingClientRect().bottom ?? 0) + 80;
      let current: string | null = null;
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= line) current = id;
      }
      setActive(current);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [ids]);
  return active;
}

const SECTION_IDS = SECTIONS.map((s) => `cl-${s.key}`);

export function StageBar() {
  const lab = useCreator((s) => s.lab);
  const mode = useCreator((s) => s.api?.mode);
  const now = useLabClock(lab, mode === "live");
  const reduced = useReducedMotion();
  const active = useActiveSection(SECTION_IDS);
  const phases = stepPhases(lab);
  const stages = SECTIONS.map((sec) => summarizeStage(sec.key, sec.label, phases.filter((p) => p.section === sec.key), now, lab?.status === "running"));
  return (
    <nav className="cl-stages" aria-label="Lab stages">
      <div className="cl-wrap cl-stages-row">
        <ol className="cl-stage-list">
          {stages.map((st) => {
            const id = `cl-${st.key}`;
            return (
              <li key={st.key}>
                <button
                  className="cl-stage"
                  data-status={st.status}
                  aria-current={active === id ? "true" : undefined}
                  onClick={() => goToSection(id, reduced)}
                  title={st.phases.map((p) => `${p.label}: ${STATUS_WORD[p.status]}${p.started != null ? ` (${fmtSpan((p.ended ?? now) - p.started)})` : ""}`).join("\n")}
                >
                  {(st.status === "running" || st.status === "failed") && <PhaseIcon status={st.status} />}
                  <span className="cl-stage-name">{st.label}</span>
                  {st.meta && <span className="cl-stage-meta">{st.meta}</span>}
                  {st.status === "running" && (
                    <span className="cl-stage-track" aria-hidden>
                      <span style={{ width: `${Math.round(st.progress * 100)}%` }} />
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ol>
        <PhasesPopover phases={phases} now={now} />
      </div>
    </nav>
  );
}

function PhasesPopover({ phases, now }: { phases: StepPhase[]; now: number }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useDismiss(open, () => setOpen(false), wrap);
  return (
    <div className="cl-phases" ref={wrap}>
      <button className="cl-phases-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="dialog">
        {phases.length} phases <ChevronDown size={12} aria-hidden />
      </button>
      {open && (
        <div className="cl-pop cl-phases-pop" role="dialog" aria-label="All phases">
          {SECTIONS.map((sec) => {
            const mine = phases.filter((p) => p.section === sec.key);
            if (!mine.length) return null;
            return (
              <div key={sec.key} className="cl-phases-group">
                <p className="cl-phases-label">{sec.label}</p>
                <ul>
                  {mine.map((p) => (
                    <li key={p.key} data-status={p.status}>
                      <PhaseIcon status={p.status} />
                      <span className="cl-phases-name">{p.label}</span>
                      <span className="cl-phases-time num">{p.started != null ? fmtSpan((p.ended ?? now) - p.started) : ""}</span>
                      {p.detail && <span className="cl-phases-detail">{p.detail}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
