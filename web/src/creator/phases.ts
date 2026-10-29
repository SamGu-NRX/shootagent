// The lab's phases, the dashboard sections they belong to, and the writers' room feed grouped by phase.

import type { LabPhase, LabState, PhaseStatus, RoomKind, RoomMessage } from "./types";

export type SectionKey = "room" | "reels" | "scorecard" | "launch";

export interface PhaseInfo {
  key: string;
  label: string;
  section: SectionKey;
}

/** The contract's phase keys and labels, in run order. */
export const PHASES: PhaseInfo[] = [
  { key: "brief", label: "Brief", section: "room" },
  { key: "pitch", label: "Pitches", section: "room" },
  { key: "critique", label: "Critique", section: "room" },
  { key: "remix", label: "Remix", section: "room" },
  { key: "greenlight", label: "Greenlight", section: "room" },
  { key: "single_agent", label: "Single-agent control", section: "room" },
  { key: "render", label: "Render", section: "reels" },
  { key: "watch", label: "Audience", section: "scorecard" },
  { key: "judge", label: "Judges", section: "scorecard" },
  { key: "novelty", label: "Novelty", section: "scorecard" },
  { key: "verdict", label: "Verdict", section: "scorecard" },
  { key: "simulate", label: "Launch sim", section: "launch" },
];

export const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: "room", label: "Writers' room" },
  { key: "reels", label: "Reels" },
  { key: "scorecard", label: "Scorecard" },
  { key: "launch", label: "Launch" },
];

const INFO = new Map(PHASES.map((p) => [p.key, p]));

export const phaseLabel = (key: string) => INFO.get(key)?.label ?? key.replace(/_/g, " ");
export const phaseSection = (key: string): SectionKey => INFO.get(key)?.section ?? "room";

export interface StepPhase extends LabPhase {
  section: SectionKey;
}

/**
 * Every phase the stepper shows: the lab's own phases (in the server's order) with the contract's phases it
 * has not listed yet filled in as pending, so the stepper keeps its shape from the first poll.
 */
export function stepPhases(lab: LabState | null): StepPhase[] {
  const own = lab?.phases ?? [];
  const byKey = new Map(own.map((p) => [p.key, p]));
  const out: StepPhase[] = PHASES.map((info) => {
    const p = byKey.get(info.key);
    return p
      ? { ...p, section: info.section }
      : { key: info.key, label: info.label, status: "pending", started: null, ended: null, detail: "", section: info.section };
  });
  // Phases the contract doesn't know yet go after the last phase of their neighbour's section.
  own.forEach((p, i) => {
    if (INFO.has(p.key)) return;
    const prev = own.slice(0, i).reverse().find((q) => INFO.has(q.key));
    const section = prev ? phaseSection(prev.key) : "room";
    const at = out.map((q) => q.section).lastIndexOf(section);
    out.splice(at + 1, 0, { ...p, section });
  });
  return out;
}

/** Seconds a phase has run: to its end, or to `now` while it runs. */
export function phaseSeconds(p: LabPhase, now: number): number | null {
  if (p.started == null) return null;
  return Math.max(0, (p.ended ?? now) - p.started);
}

// ---- feed grouping --------------------------------------------------------------------------------

const KIND_PHASE: Partial<Record<RoomKind, string>> = {
  brief: "brief",
  pitch: "pitch",
  critique: "critique",
  remix: "remix",
  steal: "remix",
  vote: "greenlight",
  greenlight: "greenlight",
};

export interface FeedGroup {
  /** unique per group: phase key plus the index of its first message */
  id: string;
  phase: string;
  label: string;
  status: PhaseStatus;
  start: number;
  messages: RoomMessage[];
}

/** The single-agent control's id (creative/personas.py SINGLE_AGENT). */
export const CONTROL_AGENT = "single_agent";

/** True when the one-agent ablation wrote the message; it never talks to the room. */
export const isControlMessage = (m: RoomMessage) => m.agent === CONTROL_AGENT;

/** How close (seconds) a message must be to a phase's start or end to be filed under it when phases overlap. */
const EDGE_SECONDS = 1.5;

/** The phase a message was posted in: the phase running at its time, then its kind, then the last phase started before it. */
export function messagePhase(phases: LabPhase[], m: RoomMessage): string {
  const room = phases.filter((p) => p.key !== "single_agent" && p.started != null);
  const running = room.filter((p) => p.started! <= m.t && (p.ended == null || m.t <= p.ended));
  const byKind = KIND_PHASE[m.kind];
  if (running.length) {
    const match = byKind ? running.find((p) => p.key === byKind) : undefined;
    if (match) return match.key;
    // phases can overlap (the audience, the judges and novelty run together): a "done" message lands at its
    // phase's end and a "starting" one at its start, so the nearest edge decides before the latest start
    const nearest = (edge: (p: LabPhase) => number | null) =>
      running
        .map((p) => ({ p, d: edge(p) == null ? Infinity : Math.abs(m.t - edge(p)!) }))
        .filter((x) => x.d <= EDGE_SECONDS)
        .sort((a, b) => a.d - b.d)[0]?.p;
    const edge = nearest((p) => p.ended) ?? nearest((p) => p.started);
    if (edge) return edge.key;
    return running.reduce((a, b) => (b.started! > a.started! ? b : a)).key;
  }
  if (byKind) return byKind;
  const before = room.filter((p) => p.started! <= m.t);
  if (before.length) return before.reduce((a, b) => (b.started! > a.started! ? b : a)).key;
  return "brief";
}

/**
 * The room's feed as runs of messages per phase, in time order, plus the single-agent control's messages
 * kept apart (the control never talks to the room).
 */
export function groupFeed(lab: Pick<LabState, "feed" | "phases">): { room: FeedGroup[]; control: RoomMessage[] } {
  const room: FeedGroup[] = [];
  const control: RoomMessage[] = [];
  const status = new Map(lab.phases.map((p) => [p.key, p.status]));
  const labels = new Map(lab.phases.map((p) => [p.key, p.label]));
  const sorted = [...lab.feed].sort((a, b) => a.t - b.t);
  sorted.forEach((m, i) => {
    if (isControlMessage(m)) {
      control.push(m);
      return;
    }
    const phase = messagePhase(lab.phases, m);
    const last = room[room.length - 1];
    if (last && last.phase === phase) last.messages.push(m);
    else
      room.push({
        id: `${phase}-${i}`,
        phase,
        label: labels.get(phase) || phaseLabel(phase),
        status: status.get(phase) ?? "done",
        start: m.t,
        messages: [m],
      });
  });
  return { room, control };
}
