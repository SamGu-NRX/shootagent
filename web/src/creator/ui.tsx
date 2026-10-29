// Small shared pieces of the creator dashboard: formatting, reel marks, hooks.

import { ChevronRight } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { useCreator } from "./store";
import type { AgentCard, LabState, Reel } from "./types";

// ---- formatting -----------------------------------------------------------------------------------

/** m:ss, or h:mm:ss past an hour */
export function fmtClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export const fmtInt = (n: number) => Math.round(n).toLocaleString("en-US");

/** A finished span of time: "<1 s", "43 s", then m:ss. */
export function fmtSpan(seconds: number): string {
  if (seconds < 1) return "<1 s";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  return fmtClock(seconds);
}

/** Text with the agents' *emphasis* rendered as emphasis instead of asterisks. */
export function Rich({ text }: { text: string }) {
  const parts = text.split(/\*([^*\n]+)\*/g);
  return <>{parts.map((p, i) => (i % 2 ? <em key={i}>{p}</em> : p))}</>;
}

/** 940 / 9.4K / 94K / 1.2M */
export function fmtCompact(n: number): string {
  const a = Math.abs(n);
  if (a < 1000) return fmtInt(n);
  if (a < 10_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  if (a < 1_000_000) return `${Math.round(n / 1000)}K`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

export const fmtPct = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`;
export const fmtUsd = (v: number) => (v > 0 && v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);
export const fmtScore = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];
/** "six" for small counts, digits past twelve. */
export function numberWord(n: number, capital = false): string {
  const w = WORDS[n] ?? fmtInt(n);
  return capital ? w.charAt(0).toUpperCase() + w.slice(1) : w;
}

export function plural(n: number, one: string, many = `${one}s`) {
  return `${fmtInt(n)} ${n === 1 ? one : many}`;
}

// ---- reels ----------------------------------------------------------------------------------------

// Charts are grey except the reel that leads on the chart's own measure, which takes the accent. Identity
// comes from the letter labels at the line ends, not from colour.
const ACCENT = "#a593ff";
const GREYS = ["#bdbdbd", "#8a8a8a"];
const TEMPLATE = "#5c5c5c";

/** Ink per reel id for a chart: the leader in the accent, the template darkest, the rest in two greys. */
export function reelInks(reels: Pick<Reel, "id" | "kind">[], lead: string | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  let i = 0;
  for (const r of reels) out[r.id] = r.id === lead ? ACCENT : r.kind === "baseline" ? TEMPLATE : GREYS[i++ % GREYS.length]!;
  return out;
}

/** The reel's letter in a small square. */
export function ReelBadge({ id, size = 20 }: { id: string; size?: number }) {
  return (
    <span className="cl-badge" style={{ width: size, height: size, fontSize: Math.round(size * 0.5) }} aria-hidden>
      {id}
    </span>
  );
}

// ---- agents ---------------------------------------------------------------------------------------

export type Who = Pick<AgentCard, "id" | "name" | "color" | "initials"> & { role: AgentCard["role"] | "viewer"; lens?: string };

const unknown = (id: string): Who => ({ id, name: id.replace(/_/g, " "), color: "#5a5a5a", initials: id.slice(0, 2).toUpperCase(), role: "system" });

/** Every agent the lab mentions (room, judges, viewers), by id. */
export function useAgents(): (id: string) => Who {
  const agents = useCreator((s) => s.lab?.agents);
  const judges = useCreator((s) => s.lab?.evaluation?.judges);
  const viewers = useCreator((s) => s.lab?.evaluation?.viewers);
  return useMemo(() => {
    const m = new Map<string, Who>();
    for (const v of viewers ?? []) m.set(v.id, { id: v.id, name: v.name, color: v.color, initials: v.initials, role: "viewer", lens: v.segment });
    for (const a of [...(agents ?? []), ...(judges ?? [])]) m.set(a.id, a);
    return (id: string) => m.get(id) ?? unknown(id);
  }, [agents, judges, viewers]);
}

// ---- hooks ----------------------------------------------------------------------------------------

export function useReducedMotion(): boolean {
  const q = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof matchMedia === "function" && matchMedia(q).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(q);
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** An element's content width, kept current: charts draw at real pixel size so text and strokes stay crisp. */
export function useWidth<T extends HTMLElement>(fallback = 600): [RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [w, setW] = useState(0);
  const el = ref.current;
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const pad = (n: HTMLElement) => {
      const cs = getComputedStyle(n);
      return parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
    };
    setW(Math.round(node.getBoundingClientRect().width - pad(node)));
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => e && setW(Math.round(e.contentRect.width)));
    ro.observe(node);
    return () => ro.disconnect();
  }, [el]);
  return [ref, w || fallback];
}

/** Round axis ticks: a 1, 2, 2.5 or 5 step that gives about `count` intervals up to at least `max`. */
export function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * mag >= raw) ?? 10) * mag;
  const top = Math.ceil(max / step - 1e-9) * step;
  return Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
}

/** Date.now() that ticks while `active`. */
export function useNow(active: boolean, ms = 500): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [active, ms]);
  return now;
}

/** The lab's elapsed seconds; between live polls it keeps counting so the clock doesn't stutter. */
export function useLabClock(lab: Pick<LabState, "elapsed" | "status"> | null, smooth: boolean): number {
  const running = lab?.status === "running";
  const [base, setBase] = useState({ elapsed: lab?.elapsed ?? 0, at: Date.now() });
  useEffect(() => setBase({ elapsed: lab?.elapsed ?? 0, at: Date.now() }), [lab?.elapsed]);
  const now = useNow(running && smooth, 250);
  if (!lab) return 0;
  return running && smooth ? base.elapsed + Math.max(0, (now - base.at) / 1000) : lab.elapsed;
}

/** Closes a popover on an outside pointer-down or Escape. */
export function useDismiss(open: boolean, close: () => void, wrap: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => wrap.current?.contains(e.target as Node) || close();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close, wrap]);
}

/** Scroll a dashboard section into view below the sticky bars. */
export function goToSection(id: string, reduced: boolean) {
  document.getElementById(id)?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
}

/** Photo crop centred on a beat's focus point. */
export const focusStyle = (focus: number[] | null | undefined): CSSProperties =>
  focus && focus.length >= 2 ? { objectPosition: `${(focus[0]! * 100).toFixed(0)}% ${(focus[1]! * 100).toFixed(0)}%` } : {};

// ---- section header -------------------------------------------------------------------------------

export function SectionHead({ id, title, sub, children }: { id: string; title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <header className="cl-sec-head">
      <div className="cl-sec-titles">
        <h2 id={id}>{title}</h2>
        {sub && <p>{sub}</p>}
      </div>
      {children && <div className="cl-sec-aside">{children}</div>}
    </header>
  );
}

// ---- disclosure -----------------------------------------------------------------------------------

/** A quiet "Show more" toggle; the content mounts only when open. */
export function Disclosure({ label, openLabel, children, className = "" }: { label: ReactNode; openLabel?: ReactNode; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={`cl-disc ${className}`} data-open={open || undefined}>
      <button className="cl-disc-btn" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={13} className="cl-disc-chev" aria-hidden />
        {open && openLabel ? openLabel : label}
      </button>
      {open && (
        <div id={id} className="cl-disc-body">
          {children}
        </div>
      )}
    </div>
  );
}

