// Small shared pieces of the creator dashboard: formatting, colours, avatars, hooks.

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { useCreator } from "./store";
import type { AgentCard, LabState, Reel, ViewerPersona } from "./types";

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

// ---- colour ---------------------------------------------------------------------------------------

function rgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const h = m[1]!.length === 3 ? [...m[1]!].map((c) => c + c).join("") : m[1]!;
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function luminance(hex: string): number {
  const c = rgb(hex);
  if (!c) return 0.5;
  const [r, g, b] = c.map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Ink for text set inside a fill of this colour. */
export const inkOn = (hex: string) => (luminance(hex) > 0.36 ? "#141414" : "#ffffff");

/** The same hue lifted toward white until it reads as a mark on the dark surface. */
export function onDark(hex: string): string {
  const c = rgb(hex);
  if (!c) return hex;
  const l = luminance(hex);
  if (l >= 0.16) return hex;
  const t = Math.min(0.55, (0.16 - l) * 3.2 + 0.15);
  return `rgb(${c.map((v) => Math.round(v + (255 - v) * t)).join(" ")})`;
}

// Validated for the dark surface (dataviz validate_palette.js, all pairs): B, C and S clear the CVD and
// normal-vision floors; the template's grey is deliberately colourless and always carries its letter.
const BASELINE = "#c9c9c9";
const CREATIVE = ["#8f80f0", "#d9762b", "#d55181", "#c98500"];
const SINGLE = "#1f9fb0";

/** Colour per reel id: grey template, violet and orange for the room's picks, teal for the single agent. */
export function reelColors(reels: Pick<Reel, "id" | "kind">[]): Record<string, string> {
  const out: Record<string, string> = {};
  let i = 0;
  for (const r of reels) out[r.id] = r.kind === "baseline" ? BASELINE : r.kind === "single_agent" ? SINGLE : CREATIVE[i++ % CREATIVE.length]!;
  return out;
}

export const reelStyle = (color: string) => ({ "--reel": color }) as CSSProperties;

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
    for (const v of viewers ?? []) m.set(v.id, viewerWho(v));
    for (const a of [...(agents ?? []), ...(judges ?? [])]) m.set(a.id, a);
    return (id: string) => m.get(id) ?? unknown(id);
  }, [agents, judges, viewers]);
}

export const viewerWho = (v: ViewerPersona): Who => ({
  id: v.id,
  name: v.name,
  color: v.color,
  initials: v.initials || v.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase(),
  role: "viewer",
  lens: v.segment,
});

export function Avatar({ who, size = 28, className = "" }: { who: Who; size?: number; className?: string }) {
  return (
    <span
      className={`cl-av ${className}`}
      data-role={who.role}
      style={{ "--av": who.color, "--av-ink": inkOn(who.color), width: size, height: size, fontSize: Math.round(size * 0.36) } as CSSProperties}
      aria-hidden
    >
      {who.initials}
    </span>
  );
}

export function ReelBadge({ id, color, size = 22 }: { id: string; color: string; size?: number }) {
  return (
    <span className="cl-badge" style={{ ...reelStyle(color), "--ink": inkOn(color), width: size, height: size, fontSize: Math.round(size * 0.52) } as CSSProperties} aria-hidden>
      {id}
    </span>
  );
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

/** Scroll a dashboard section into view below the sticky bars. */
export function goToSection(id: string, reduced: boolean) {
  document.getElementById(id)?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
}

/** Photo crop centred on a beat's focus point. */
export const focusStyle = (focus: number[] | null | undefined): CSSProperties =>
  focus && focus.length >= 2 ? { objectPosition: `${(focus[0]! * 100).toFixed(0)}% ${(focus[1]! * 100).toFixed(0)}%` } : {};

// ---- section header -------------------------------------------------------------------------------

export function SectionHead({ n, id, title, sub, children }: { n: string; id: string; title: string; sub: ReactNode; children?: ReactNode }) {
  return (
    <header className="cl-sec-head">
      <span className="cl-sec-n num" aria-hidden>
        {n}
      </span>
      <div className="cl-sec-titles">
        <h2 id={id}>{title}</h2>
        <p>{sub}</p>
      </div>
      {children && <div className="cl-sec-aside">{children}</div>}
    </header>
  );
}
