// Scene 2, "The creative team": the showrunner's brief, then six creators working side by side on the real
// clock of the pitch round, and their twelve pitches landing on the concept wall at once.

import "../../styles/present-pitch.css";
import { Check } from "lucide-react";
import { useMemo, type CSSProperties } from "react";
import { CONTROL_AGENT } from "../phases";
import type { AgentCard, LabState, ReelConcept } from "../types";
import { AGENT_JOB, agentIcon, hdPhoto, roomCast } from "./cast";
import { countUp, easeOut, linear, phase, type SceneProps } from "./types";

// ---- layout on the 1920x1080 stage ---------------------------------------------------------------------

const LEFT = 120;
const LANE_TOP = 626;
const LANE_H = 52;
const LANE_GAP = 8;
const TRACK_X = LEFT + 290;
const TRACK_W = 360;
const WALL_X = 880;
const WALL_Y = 286;
const CARD_W = 216;
const CARD_H = 180;
const CARD_GAP = 18;
const COLS = 4;

// ---- timeline, ms -------------------------------------------------------------------------------------------

const TICK_FROM = 3600;
const TICK_SPAN = 2200;
const FLIGHT = 950;
const STATS_FROM = 6300;

// ---- data ---------------------------------------------------------------------------------------------------

interface Lane {
  agent: AgentCard;
  /** 0-1 of the pitch round, when this creator's pitches landed */
  done: number;
  concepts: ReelConcept[];
}

interface Pitch {
  lanes: Lane[];
  /** real seconds of the pitch round */
  round: number;
  /** real seconds if the creators had taken turns */
  turns: number;
  ideas: number;
}

const plain = (text: string) => text.replace(/\*([^*\n]+)\*/g, "$1").trim();
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const STOP = /\s+(a|an|the|with|of|and|in|on|to|at|by|for)$/i;

/** A hero detail cut to a short label: the part before a colon, else whole words up to `max`. */
export function shortDetail(text: string, max = 40): string {
  const head = text.split(/[:;(]/)[0]!.trim().replace(/\.$/, "");
  if (head.length <= max + 4) return head;
  let cut = head.slice(0, max);
  cut = cut.slice(0, Math.max(cut.lastIndexOf(" "), 1)).replace(/[,;:\-–—\s]+$/, "");
  while (STOP.test(cut)) cut = cut.replace(STOP, "");
  return `${cut}…`;
}

function buildPitch(lab: LabState): Pitch {
  const { creators } = roomCast(lab);
  const round = lab.phases.find((p) => p.key === "pitch");
  const start = round?.started ?? 0;
  const span = Math.max(1, (round?.ended ?? start + 1) - start);
  const ideas = lab.concepts.filter(
    (c) => c.round === 1 && c.agent !== "template" && c.agent !== CONTROL_AGENT && !lab.single_agent.includes(c.id),
  );
  const lanes = creators.map((agent) => {
    const landed = lab.feed.filter((m) => m.kind === "pitch" && m.agent === agent.id).map((m) => m.t);
    const at = landed.length ? Math.max(...landed) : start + span;
    return {
      agent,
      done: Math.min(1, Math.max(0.08, (at - start) / span)),
      concepts: ideas.filter((c) => c.agent === agent.id).sort((a, b) => a.id.localeCompare(b.id)),
    };
  });
  return {
    lanes,
    round: span,
    turns: lanes.reduce((a, l) => a + l.done * span, 0),
    ideas: ideas.length,
  };
}

// ---- scene --------------------------------------------------------------------------------------------------

export function ScenePitch({ lab, shoot, story, t }: SceneProps) {
  const data = useMemo(() => buildPitch(lab), [lab]);
  const { showrunner } = useMemo(() => roomCast(lab), [lab]);
  const photos = useMemo(
    () =>
      Object.keys(shoot?.photos ?? {})
        .sort()
        .slice(0, 8)
        .map((v) => hdPhoto(shoot!.photos[v])!),
    [shoot],
  );
  const heroes = useMemo(() => (lab.brief?.hero_features ?? []).slice(0, 3).map((f) => shortDetail(f.feature)), [lab.brief]);
  const tick = phase(t, TICK_FROM, TICK_SPAN, linear);

  // every card's slot on the wall, filled creator by creator
  const cards = data.lanes.flatMap((lane, i) => lane.concepts.map((c, j) => ({ c, lane, i, j }))).slice(0, COLS * 3);

  return (
    <div className="pz-pitch">
      <h1 className="pz-pitch-head">
        <span style={rise(t, 0)}>A creative team for every listing.</span>
        <span className="pz-pitch-dim" style={rise(t, 250)}>
          {story.roomAgents} AI agents. {story.creators} creators pitch at once.
        </span>
      </h1>

      {/* the brief */}
      <section className="pz-pitch-show" style={rise(t, 500)}>
        {showrunner && (
          <div className="pz-pitch-node">
            <AgentDot agent={showrunner} size={52} />
            <div>
              <p className="pz-pitch-name">{showrunner.name}</p>
              <p className="pz-pitch-job">{AGENT_JOB[showrunner.id] ?? showrunner.lens}</p>
            </div>
          </div>
        )}
        <div className="pz-pitch-film">
          {photos.map((src, i) => (
            <img key={src} src={src} alt="" style={{ opacity: phase(t, 650 + i * 70, 400), transform: `translateY(${(1 - phase(t, 650 + i * 70, 400)) * 14}px)` }} />
          ))}
        </div>
        <ul className="pz-pitch-heroes">
          {heroes.map((h, k) => {
            const from = 1100 + k * 450;
            const shown = Math.round(h.length * phase(t, from, 700, linear));
            if (t < from) return <li key={h} className="pz-pitch-hero-empty" />;
            return (
              <li key={h}>
                <span>
                  {h.slice(0, shown)}
                  {shown < h.length && <i className="pz-pitch-caret" />}
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      {/* six creators on the real clock of the pitch round */}
      <div className="pz-pitch-lanes-head" style={rise(t, 2400)}>
        <span className="pz-pitch-lanes-title">{data.lanes.length} creators</span>
        <span className="pz-pitch-live">
          <i style={{ opacity: tick > 0 && tick < 1 ? 0.45 + 0.55 * Math.abs(Math.cos(t / 260)) : 1 }} /> In parallel
        </span>
        <span className="pz-pitch-ticker">
          <span className="num">{clock(tick * data.round)}</span>
          <small> / {clock(data.round)} real time</small>
        </span>
      </div>
      {data.lanes.map((lane, i) => {
        const shown = phase(t, 2500 + i * 110, 500);
        const width = Math.min(tick, lane.done);
        const finished = tick >= lane.done;
        const doneAt = TICK_FROM + lane.done * TICK_SPAN;
        const thinking = t >= 2500 && !finished;
        return (
          <div
            key={lane.agent.id}
            className="pz-pitch-lane"
            style={{ top: LANE_TOP + i * (LANE_H + LANE_GAP), opacity: shown, transform: `translateX(${(1 - shown) * -24}px)` }}
          >
            <AgentDot agent={lane.agent} size={40} pulse={thinking ? ((t + i * 190) % 1100) / 1100 : null} />
            <div className="pz-pitch-lane-text">
              <p className="pz-pitch-lane-name">{lane.agent.name}</p>
              <p className="pz-pitch-lane-job">{AGENT_JOB[lane.agent.id] ?? lane.agent.lens}</p>
            </div>
            <span className="pz-pitch-track">
              <span className="pz-pitch-fill" style={{ width: `${width * 100}%` }} />
              {tick > 0 && !finished && <span className="pz-pitch-head-dot" style={{ left: `${width * 100}%` }} />}
            </span>
            <span className="pz-pitch-done" style={{ opacity: phase(t, doneAt, 300) }}>
              <Check size={15} strokeWidth={2.6} aria-hidden />
              <span>{lane.concepts.length}</span>
            </span>
          </div>
        );
      })}

      {/* the concept wall */}
      {cards.map(({ c, lane, i, j }, k) => {
        const launch = TICK_FROM + lane.done * TICK_SPAN + j * 90;
        if (t < launch) return null;
        const p = phase(t, launch, FLIGHT, easeOut);
        const x0 = TRACK_X + lane.done * TRACK_W;
        const y0 = LANE_TOP + i * (LANE_H + LANE_GAP) + LANE_H / 2;
        const x1 = WALL_X + (k % COLS) * (CARD_W + CARD_GAP) + CARD_W / 2;
        const y1 = WALL_Y + Math.floor(k / COLS) * (CARD_H + CARD_GAP) + CARD_H / 2;
        const cx = x0 + (x1 - x0) * p;
        const cy = y0 + (y1 - y0) * p - Math.sin(p * Math.PI) * 36;
        const s = 0.22 + 0.78 * p;
        const Icon = agentIcon(lane.agent.id);
        return (
          <article
            key={c.id}
            className="pz-pitch-card"
            style={{
              opacity: phase(t, launch, 260, linear),
              transform: `translate(${cx - CARD_W / 2}px, ${cy - CARD_H / 2}px) scale(${s})`,
            }}
          >
            <p className="pz-pitch-card-by">
              <Icon size={14} strokeWidth={2.2} aria-hidden />
              {lane.agent.name}
            </p>
            <p className="pz-pitch-card-title">{plain(c.title)}</p>
            <p className="pz-pitch-card-hook">“{plain(c.hook.text)}”</p>
          </article>
        );
      })}

      <div className="pz-pitch-stats">
        <Stat t={t} k={0} value={String(Math.round(countUp(t, STATS_FROM, 900, data.ideas)))} label="ideas pitched" />
        <Stat t={t} k={1} value={String(Math.round(countUp(t, STATS_FROM + 180, 700, data.lanes.length)))} label="creators at once" />
        <Stat t={t} k={2} value={clock(data.round)} label={`real time · ${clock(data.turns)} if they took turns`} />
      </div>
    </div>
  );
}

// ---- pieces -------------------------------------------------------------------------------------------------

function rise(t: number, from: number, span = 700): CSSProperties {
  const p = phase(t, from, span);
  return { opacity: p, transform: `translateY(${(1 - p) * 24}px)` };
}

function AgentDot({ agent, size, pulse = null }: { agent: AgentCard; size: number; pulse?: number | null }) {
  const Icon = agentIcon(agent.id);
  return (
    <span className="pz-pitch-icon" style={{ width: size, height: size }}>
      <Icon size={Math.round(size * 0.46)} strokeWidth={2} aria-hidden />
      {pulse != null && <span className="pz-pitch-ring" style={{ transform: `scale(${1 + pulse * 0.45})`, opacity: 0.55 * (1 - pulse) }} />}
    </span>
  );
}

function Stat({ t, k, value, label }: { t: number; k: number; value: string; label: string }) {
  return (
    <div className="pz-pitch-stat" style={rise(t, STATS_FROM + k * 180)}>
      <b className="num">{value}</b>
      <span>{label}</span>
    </div>
  );
}
