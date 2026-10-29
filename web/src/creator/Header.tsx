import { ArrowLeft, Check, ChevronDown, Circle, Minus, Play, RotateCw, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { radioKeys } from "../components/radioKeys";
import { phaseLabel, SECTIONS, stepPhases, type StepPhase } from "./phases";
import { creatorRoute, useCreator } from "./store";
import { DEFAULT_OPTIONS, type LabOptions, type LabState, type PhaseStatus } from "./types";
import { fmtClock, fmtUsd, goToSection, plural, useLabClock, useReducedMotion } from "./ui";

export function LabHeader() {
  const lab = useCreator((s) => s.lab);
  const shoot = useCreator((s) => s.shoot);
  const shootId = useCreator((s) => s.shootId);
  const mode = useCreator((s) => s.api?.mode);
  const elapsed = useLabClock(lab, mode === "live");
  const listing = shoot?.listing;
  const title = listing?.address || lab?.title || shoot?.title || "";
  const facts = [
    listing?.price,
    listing?.beds != null ? `${listing.beds} bed` : null,
    listing?.baths != null ? `${listing.baths} bath` : null,
    listing?.sqft != null ? `${listing.sqft.toLocaleString("en-US")} sq ft` : null,
  ].filter(Boolean);

  return (
    <header className="cl-top">
      <a className="icon-btn" href={shootId ? `#/s/${encodeURIComponent(shootId)}` : "#/"} aria-label="Back to the shoot">
        <ArrowLeft size={16} />
      </a>
      <div className="cl-top-title">
        <span className="cl-kicker">
          <LabMark /> Creative lab
        </span>
        <h1>{title || <span className="skeleton-text" style={{ width: 180 }} />}</h1>
      </div>
      {facts.length > 0 && <p className="cl-top-facts num">{facts.join(" · ")}</p>}
      <div className="cl-top-right">
        {mode === "mock" && (
          <span className="badge-demo" title="A recorded lab replayed in the browser, sped up. No agents are running.">
            Demo replay
          </span>
        )}
        <LabPicker />
        {lab && <StatusPill lab={lab} elapsed={elapsed} />}
        {lab && (
          <span className="cl-cost num" title={`${lab.usage.calls} model calls, ${(lab.usage.input_tokens / 1000).toFixed(0)}K tokens in, ${(lab.usage.output_tokens / 1000).toFixed(0)}K out`}>
            {fmtUsd(lab.usage.usd)}
          </span>
        )}
        <RunControls />
      </div>
    </header>
  );
}

export function LabMark() {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" aria-hidden className="cl-mark">
      <path d="M2 7V2h5M18 7V2h-5M2 13v5h5M18 13v5h-5" stroke="currentColor" strokeWidth="1.8" fill="none" />
      <circle cx="10" cy="10" r="2.6" fill="var(--agent)" />
    </svg>
  );
}

function LabPicker() {
  const labs = useCreator((s) => s.labs);
  const labId = useCreator((s) => s.labId);
  const shootId = useCreator((s) => s.shootId);
  if (!shootId || labs.length < 1) return null;
  const when = (iso: string) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  };
  return (
    <label className="cl-picker">
      <span className="sr-only">Lab</span>
      <select value={labId ?? ""} onChange={(e) => (location.hash = creatorRoute(shootId, e.target.value))}>
        {labs.map((l) => (
          <option key={l.id} value={l.id}>
            {when(l.created)} · {l.status} · {plural(l.reels, "reel")} · {fmtUsd(l.usd)}
          </option>
        ))}
      </select>
      <ChevronDown size={13} aria-hidden />
    </label>
  );
}

function StatusPill({ lab, elapsed }: { lab: LabState; elapsed: number }) {
  if (lab.status === "running") {
    const current = lab.phases.find((p) => p.key === lab.phase) ?? lab.phases.find((p) => p.status === "running");
    return (
      <span className="live-pill cl-status" role="status">
        <span className="live-dot" aria-hidden />
        <span className="cl-status-text">
          {current ? phaseLabel(current.key) : "Running"}
          {current?.detail ? <span className="cl-status-detail"> · {current.detail}</span> : null}
        </span>
        <span className="num">{fmtClock(elapsed)}</span>
      </span>
    );
  }
  const text = lab.status === "done" ? "Done in" : lab.status === "cancelled" ? "Cancelled at" : "Failed at";
  return (
    <span className="cl-status" data-status={lab.status} role="status" title={lab.error ?? undefined}>
      {lab.status === "done" ? <Check size={13} aria-hidden /> : <X size={13} aria-hidden />}
      {text} <span className="num">{fmtClock(lab.elapsed)}</span>
    </span>
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

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => wrap.current?.contains(e.target as Node) || setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

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
        <button className="btn" onClick={() => void cancel()}>
          <Square size={11} aria-hidden /> Cancel
        </button>
      )}
      {(lab?.status === "cancelled" || lab?.status === "failed") && (
        <button className="btn" onClick={() => void resume()}>
          <RotateCw size={13} aria-hidden /> Resume
        </button>
      )}
      <div className="cl-split">
        <button className="btn btn-agent" onClick={run} disabled={running || starting}>
          <Play size={12} aria-hidden /> {starting ? "Starting…" : "Run creative lab"}
        </button>
        <button
          className="btn btn-agent cl-split-more"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-label="Lab options"
          disabled={running || starting}
        >
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
          <button className="btn btn-agent cl-pop-go" onClick={run}>
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

// ---- the phase stepper ------------------------------------------------------------------------------

const STATUS_WORD: Record<PhaseStatus, string> = { pending: "waiting", running: "running", done: "done", failed: "failed", skipped: "skipped" };

function StepIcon({ status }: { status: PhaseStatus }) {
  switch (status) {
    case "done":
      return <Check size={12} strokeWidth={2.4} aria-hidden />;
    case "failed":
      return <X size={12} strokeWidth={2.4} aria-hidden />;
    case "skipped":
      return <Minus size={12} aria-hidden />;
    case "running":
      return <span className="cl-step-pulse" aria-hidden />;
    default:
      return <Circle size={9} aria-hidden />;
  }
}

function stepMeta(p: StepPhase, now: number): string {
  if (p.status === "skipped") return "skipped";
  if (p.started == null) return "";
  const s = Math.max(0, (p.ended ?? now) - p.started);
  return fmtClock(s);
}

export function PhaseStepper() {
  const lab = useCreator((s) => s.lab);
  const mode = useCreator((s) => s.api?.mode);
  const now = useLabClock(lab, mode === "live");
  const reduced = useReducedMotion();
  const phases = stepPhases(lab);
  return (
    <nav className="cl-steps" aria-label="Lab phases">
      {SECTIONS.map((sec) => {
        const mine = phases.filter((p) => p.section === sec.key);
        if (!mine.length) return null;
        return (
          <div key={sec.key} className="cl-step-group" style={{ flexGrow: mine.length }}>
            <a className="cl-step-group-label" href={`#cl-${sec.key}`} onClick={(e) => (e.preventDefault(), goToSection(`cl-${sec.key}`, reduced))}>
              {sec.label}
            </a>
            <ol>
              {mine.map((p) => (
                <li key={p.key}>
                  <button
                    className="cl-step"
                    data-status={p.status}
                    onClick={() => goToSection(`cl-${sec.key}`, reduced)}
                    title={p.detail ? `${p.label}: ${p.detail}` : p.label}
                    aria-label={`${p.label}, ${STATUS_WORD[p.status]}${p.detail ? `: ${p.detail}` : ""}`}
                  >
                    <span className="cl-step-bar" aria-hidden />
                    <span className="cl-step-name">
                      <StepIcon status={p.status} />
                      <span>{p.label}</span>
                    </span>
                    <span className="cl-step-meta num">{stepMeta(p, now)}</span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
        );
      })}
    </nav>
  );
}
