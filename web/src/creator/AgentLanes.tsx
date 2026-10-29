// The writers' room as a trace: one lane per agent, a bar while it works and a mark when it posts, so the
// parallel work reads at a glance. Times are the lab's own seconds. A post lands when its model call ends, so a
// bar runs from the start of the phase (or the agent's previous post in it) to the post. While a lab runs, the
// agents a phase is waiting on show live bars up to a "now" line.

import "../styles/creator-lanes.css";
import { Fragment, useMemo, useState, type CSSProperties } from "react";
import { CONTROL_AGENT, messagePhase } from "./phases";
import { agentIcon, roomCast, dashColor } from "./present/cast";
import { useCreator } from "./store";
import type { AgentCard, LabState, RoomKind, RoomMessage } from "./types";
import { fmtClock, numberWord, useLabClock, useWidth } from "./ui";

type Shape = "pill" | "tick" | "dot" | "star";
type Crew = "showrunner" | "creators" | "critics";

const SHAPE: Partial<Record<RoomKind, Shape>> = { brief: "pill", pitch: "pill", remix: "pill", steal: "pill", critique: "tick", vote: "dot", greenlight: "star" };
const KIND_WORD: Partial<Record<RoomKind, string>> = { brief: "Brief", pitch: "Pitch", critique: "Critique", remix: "Remix", steal: "Steal", vote: "Vote", greenlight: "Greenlight" };
const BAND_LABEL: Record<string, string> = { brief: "Brief", pitch: "Pitch", critique: "Critique", remix: "Remix & steal", vote: "Vote", greenlight: "Greenlight" };
/** who a band waits on while it runs; with two rounds the critics' second pass runs alongside the greenlight */
const expects = (band: string, crew: Crew, rounds: number) =>
  ({ brief: crew === "showrunner", pitch: crew === "creators", critique: crew === "critics", remix: crew === "creators", vote: crew === "creators", greenlight: crew === "showrunner" || (rounds >= 2 && crew === "critics") })[band] ?? false;
const ROOM_PHASES = ["brief", "pitch", "critique", "remix", "greenlight"];
/** posts closer together than this (seconds) share one mark */
const CLUSTER_S = 1.2;
/** stacked marks in a cluster, px apart */
const STACK: Record<Shape, number> = { pill: 6, tick: 4, dot: 6, star: 7 };

interface Band {
  key: string;
  label: string;
  from: number;
  to: number;
  running: boolean;
  /** most agents working at once */
  parallel: number;
  crew: string;
}
interface Span {
  band: string;
  from: number;
  to: number;
  live: boolean;
}
interface Mark {
  key: string;
  t: number;
  shape: Shape;
  msgs: RoomMessage[];
}
interface Lane {
  agent: AgentCard;
  crew: Crew;
  spans: Span[];
  marks: Mark[];
}

/** Most spans open at the same moment. */
function maxOverlap(spans: { from: number; to: number }[]): number {
  const ev = spans.flatMap((s) => [
    { t: s.from, d: 1 },
    { t: s.to, d: -1 },
  ]);
  ev.sort((a, b) => a.t - b.t || a.d - b.d);
  let n = 0;
  let best = 0;
  for (const e of ev) best = Math.max(best, (n += e.d));
  return best;
}

export function buildLanes(lab: LabState, now: number) {
  const cast = roomCast(lab);
  const crewOf = new Map<string, Crew>();
  if (cast.showrunner) crewOf.set(cast.showrunner.id, "showrunner");
  cast.creators.forEach((a) => crewOf.set(a.id, "creators"));
  cast.critics.forEach((a) => crewOf.set(a.id, "critics"));
  const live = lab.status === "running";
  const phase = (k: string) => lab.phases.find((p) => p.key === k);

  const feed = lab.feed.filter((m) => crewOf.has(m.agent) && m.agent !== CONTROL_AGENT && SHAPE[m.kind] && m.t <= now + 1e-6).sort((a, b) => a.t - b.t);
  const bandOf = (m: RoomMessage) => {
    const ph = messagePhase(lab.phases, m);
    return ph === "greenlight" ? (m.kind === "vote" ? "vote" : "greenlight") : ph;
  };

  // ---- bands: the room's phases, with the greenlight phase split at the last creator's vote
  const bands: Band[] = [];
  for (const key of ROOM_PHASES) {
    const p = phase(key);
    if (!p || p.started == null) continue;
    const running = live && p.ended == null;
    const to = p.ended ?? Math.max(p.started, now);
    if (key !== "greenlight") {
      bands.push({ key, label: BAND_LABEL[key]!, from: p.started, to, running, parallel: 0, crew: "" });
      continue;
    }
    const votes = feed.filter((m) => m.kind === "vote");
    if (!votes.length && !running) {
      bands.push({ key, label: BAND_LABEL[key]!, from: p.started, to, running, parallel: 0, crew: "" });
      continue;
    }
    const voted = new Set(votes.map((v) => v.agent)).size >= cast.creators.length;
    const voteEnd = votes.length && (voted || !running) ? Math.max(...votes.map((v) => v.t)) : to;
    bands.push({ key: "vote", label: BAND_LABEL.vote!, from: p.started, to: voteEnd, running: running && !voted, parallel: 0, crew: "" });
    if (voted || !running) bands.push({ key: "greenlight", label: BAND_LABEL.greenlight!, from: voteEnd, to, running, parallel: 0, crew: "" });
  }
  const bandStart = new Map(bands.map((b) => [b.key, b.from]));

  // ---- lanes: a bar per stretch of work, a mark per post (posts at the same moment stack)
  const lanes: Lane[] = cast.room.map((agent) => {
    const crew = crewOf.get(agent.id)!;
    const mine = feed.filter((m) => m.agent === agent.id);
    const spans: Span[] = [];
    const last = new Map<string, number>();
    for (const m of mine) {
      const band = bandOf(m);
      const from = last.get(band) ?? bandStart.get(band) ?? m.t;
      if (m.t - from > 0.5) spans.push({ band, from, to: m.t, live: false });
      last.set(band, m.t);
    }
    for (const b of bands) if (b.running && expects(b.key, crew, lab.options.rounds) && !last.has(b.key) && now - b.from > 0.5) spans.push({ band: b.key, from: b.from, to: now, live: true });
    const marks: Mark[] = [];
    for (const m of mine) {
      const shape = SHAPE[m.kind]!;
      const prev = marks[marks.length - 1];
      if (prev && prev.shape === shape && m.t - prev.msgs[prev.msgs.length - 1]!.t <= CLUSTER_S) prev.msgs.push(m);
      else marks.push({ key: m.id, t: m.t, shape, msgs: [m] });
    }
    return { agent, crew, spans, marks };
  });

  for (const b of bands) {
    const inBand = lanes.flatMap((l) => l.spans.filter((s) => s.band === b.key).map((s) => ({ ...s, crew: l.crew })));
    b.parallel = maxOverlap(inBand);
    const crews = new Set(inBand.map((s) => s.crew));
    b.crew = crews.size === 1 ? [...crews][0]! : "agents";
  }

  const ends = lab.phases.filter((p) => ROOM_PHASES.includes(p.key) && p.started != null).map((p) => p.ended ?? now);
  const roomDone = ROOM_PHASES.every((k) => {
    const p = phase(k);
    return !p || p.status === "done" || p.status === "skipped";
  });
  const known = Math.max(0, ...ends, ...feed.map((m) => m.t));
  // a live room leaves headroom and grows in 30 s steps, so marks don't slide on every tick
  const domain = !live || roomDone ? Math.max(1, known) : Math.ceil((Math.max(now, known, 45) * 1.12) / 30) * 30;
  const elapsed = !live || roomDone ? known : now;
  return { lanes, bands, domain, elapsed, posts: feed.length, live: live && !roomDone };
}

const crewWord = (crew: string, n: number) => (crew === "creators" ? (n === 1 ? "creator" : "creators") : crew === "critics" ? (n === 1 ? "critic" : "critics") : "agents");

/** The longest note that fits a band `px` wide: "6 creators in parallel", "6 in parallel", or nothing. */
function parallelNote(b: Band, px: number): string | null {
  if (b.parallel < 2) return null;
  const full = `${numberWord(b.parallel, true)} ${crewWord(b.crew, b.parallel)} in parallel`;
  if (full.length * 6.3 + 12 <= px) return full;
  const short = `${b.parallel} in parallel`;
  return short.length * 6.3 + 12 <= px ? short : null;
}

const clip = (s: string, n = 120) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);

export function AgentLanes() {
  const lab = useCreator((s) => s.lab);
  const mode = useCreator((s) => s.api?.mode);
  const now = useLabClock(lab, mode === "live");
  const [box, W] = useWidth<HTMLDivElement>(900);
  const [hover, setHover] = useState<{ lane: number; mark: Mark } | null>(null);
  const data = useMemo(() => (lab ? buildLanes(lab, now) : null), [lab, now]);
  if (!lab || !data || !data.lanes.length) return null;
  const { lanes, bands, domain, elapsed, posts, live } = data;
  const pct = (t: number) => Math.min(100, Math.max(0, (t / domain) * 100));
  const step = domain > 900 ? 120 : domain > 240 ? 60 : 30;
  const ticks = Array.from({ length: Math.floor(domain / step) + 1 }, (_, i) => i * step);
  const groupStart = (i: number) => i > 0 && lanes[i]!.crew !== lanes[i - 1]!.crew;

  return (
    <section className="cl-lanes" aria-label="The room at work: one lane per agent" data-live={live || undefined}>
      <div className="cl-lanes-top">
        <h3 className="cl-h3">The room at work</h3>
        <p className="cl-lanes-sum">
          {lanes.length} agents · <span className="num">{posts}</span> posts · <span className="num">{lab.critiques.length}</span> critiques ·{" "}
          <span className="num">{fmtClock(elapsed)}</span>
        </p>
        <ul className="cl-lanes-key" aria-label="Legend">
          <li>
            <i data-k="bar" /> working
          </li>
          <li>
            <i data-k="pill" /> pitch, remix
          </li>
          <li>
            <i data-k="tick" /> critique
          </li>
          <li>
            <i data-k="dot" /> vote
          </li>
          <li>
            <i data-k="star" /> greenlight
          </li>
        </ul>
      </div>

      <div className="cl-lanes-grid" style={{ "--rows": lanes.length } as CSSProperties}>
        <div className="cl-lanes-bands" ref={box} aria-hidden>
          {bands.map((b, i) => (
            <div key={b.key} className="cl-lanes-band" data-odd={i % 2 || undefined} data-running={b.running || undefined} style={{ left: `${pct(b.from)}%`, width: `${pct(b.to) - pct(b.from)}%` }} />
          ))}
          {live && <div className="cl-lanes-now" style={{ left: `${pct(now)}%` }} />}
        </div>

        <div className="cl-lanes-head">
          {bands.map((b) => {
            const px = ((pct(b.to) - pct(b.from)) / 100) * W;
            const note = parallelNote(b, px);
            const fits = b.label.length * 6.8 + 10 <= px;
            return (
              <div key={b.key} className="cl-lanes-label" data-running={b.running || undefined} style={{ left: `${pct(b.from)}%`, width: `${pct(b.to) - pct(b.from)}%` }} title={b.label}>
                {fits && <b>{b.label}</b>}
                {fits && note && <span>{note}</span>}
              </div>
            );
          })}
        </div>

        {lanes.map((l, i) => {
          const Icon = agentIcon(l.agent.id);
          const style = { "--ag": dashColor(l.agent.color), gridRow: i + 2 } as CSSProperties;
          return (
            <Fragment key={l.agent.id}>
              <div className="cl-lanes-who" style={style} data-group-start={groupStart(i) || undefined} title={l.agent.lens}>
                <span className="cl-lanes-chip" aria-hidden>
                  <Icon size={15} strokeWidth={2} />
                </span>
                <span className="cl-lanes-name">{l.agent.name}</span>
              </div>
              <div className="cl-lanes-track" style={style} data-group-start={groupStart(i) || undefined} role="group" aria-label={`${l.agent.name}'s work`}>
                {l.spans.map((s) => (
                  <span key={`${s.band}-${s.from}`} className="cl-lanes-bar" data-live={s.live || undefined} style={{ left: `${pct(s.from)}%`, width: `${Math.max(0.2, pct(s.to) - pct(s.from))}%` }} />
                ))}
                {l.marks.map((m) => {
                  const first = m.msgs[0]!;
                  const n = m.msgs.length;
                  const w = (n - 1) * STACK[m.shape] + 14;
                  return (
                    <button
                      key={m.key}
                      className="cl-lanes-mark"
                      data-shape={m.shape}
                      // a burst at either end stays inside the track
                      style={{ left: `clamp(${w / 2}px, ${pct(m.t)}%, calc(100% - ${w / 2}px))`, width: w }}
                      onPointerEnter={() => setHover({ lane: i, mark: m })}
                      onPointerLeave={() => setHover((h) => (h?.mark === m ? null : h))}
                      onFocus={() => setHover({ lane: i, mark: m })}
                      onBlur={() => setHover((h) => (h?.mark === m ? null : h))}
                      aria-label={`${l.agent.name}, ${n > 1 ? `${n} ${KIND_WORD[first.kind]?.toLowerCase()}s` : KIND_WORD[first.kind]?.toLowerCase()} at ${fmtClock(m.t)}: ${clip(first.text, 160)}`}
                    >
                      {m.msgs.map((msg, k) => (
                        <i key={msg.id} style={{ left: `calc(50% + ${(k - (n - 1) / 2) * STACK[m.shape]}px)` }} />
                      ))}
                    </button>
                  );
                })}
                {hover?.lane === i && (
                  <div className="cl-tip cl-lanes-tip" style={{ left: Math.min(Math.max((pct(hover.mark.t) / 100) * W, 150), W - 150) }} role="status">
                    <p className="cl-lanes-tip-head">
                      <b>{l.agent.name}</b> · {KIND_WORD[hover.mark.msgs[0]!.kind]}
                      {hover.mark.msgs.length > 1 ? ` ×${hover.mark.msgs.length}` : ""} · <span className="num">{fmtClock(hover.mark.t)}</span>
                    </p>
                    <p>{clip(hover.mark.msgs[0]!.text)}</p>
                  </div>
                )}
              </div>
            </Fragment>
          );
        })}

        <div className="cl-lanes-axis" style={{ gridRow: lanes.length + 2 }} aria-hidden>
          {ticks.map((t) => (
            <span key={t} className="num" data-edge={t === 0 ? "start" : pct(t) > 97 ? "end" : undefined} style={{ left: `${pct(t)}%` }}>
              {fmtClock(t)}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}
