// Scene 3, "Critique & remix" (12 s): the critics sweep the wall of pitches and stamp real verdicts, two real
// critic notes pop up (a cliché and a fact check against the photos), remixes are drawn from the ideas they
// stole, and the showrunner's two greenlit concepts move to the centre.

import "../../styles/present-critique.css";
import { Crown } from "lucide-react";
import { useMemo, type CSSProperties, type ReactNode } from "react";
import { CONTROL_AGENT } from "../phases";
import { criticScore, firstSentence } from "../story";
import type { AgentCard, CritiqueVerdict, LabState, ReelConcept } from "../types";
import { focusStyle } from "../ui";
import { AGENT_JOB, agentIcon, hdPhoto, roomCast } from "./cast";
import { countUp, easeBack, easeInOut, linear, phase, type SceneProps } from "./types";

// ---- layout, in stage pixels ----------------------------------------------------------------------

const WALL_X = 480;
const WALL_Y = 292;
const WALL_W = 980;
const WALL_H = 572;
const COLS = 4;
const GAP = 16;
const CARD_W = (WALL_W - (COLS - 1) * GAP) / COLS;
const CARD_H = (WALL_H - 2 * GAP) / 3;
const COL_X = 1500;
const COL_W = 320;
// the critics' notes sit in the right column until the remixes need it
const NOTE_X = 1494;
const NOTE_W = 396;
const REMIX_H = 128;
const REMIX_GAP = 20;
const FIN_W = 700;
const FIN_H = 400;
const FIN_Y = 330;

// ---- timeline, in ms --------------------------------------------------------------------------------

const SCAN_FROM = 500;
const SCAN_SPAN = 3400;
const REMIX_FROM = 4900;
const REMIX_STEP = 520;
const FINALE = 8000;
const CLICHE_TOP = 296;
const TRUTH_TOP = 512;

const VERDICT_WORD: Record<CritiqueVerdict, string> = { kill: "Cut", revise: "Rewrite", greenlight: "Keep" };

interface WallCard {
  c: ReelConcept;
  who: AgentCard | undefined;
  verdict: CritiqueVerdict;
  x: number;
  y: number;
  stampAt: number;
}

interface Remix {
  c: ReelConcept;
  who: AgentCard | undefined;
  from: number;
  fromWho: AgentCard | undefined;
  steal: boolean;
  y: number;
  at: number;
}

interface Plan {
  wall: WallCard[];
  critics: { a: AgentCard; count: number }[];
  cliche: { card: number; critic: AgentCard; flag: string; note: string } | null;
  truth: { card: number; critic: AgentCard; claim: string; fix: string; view: string | null } | null;
  remixes: Remix[];
  finalists: { c: ReelConcept; who: AgentCard | undefined; reel: { id: string; label: string } | undefined; score: number; from: { x: number; y: number } | null; x: number }[];
}

/** Majority verdict; a tie goes to the Cliche Hunter, who reads the wall for templates first. */
function verdictOf(vs: { critic: string; verdict: CritiqueVerdict }[]): CritiqueVerdict {
  const n: Record<CritiqueVerdict, number> = { kill: 0, revise: 0, greenlight: 0 };
  for (const v of vs) n[v.verdict]++;
  const max = Math.max(n.kill, n.revise, n.greenlight);
  const top = (Object.keys(n) as CritiqueVerdict[]).filter((k) => n[k] === max);
  const hunter = vs.find((v) => v.critic === "cliche_hunter")?.verdict;
  return top.length > 1 && hunter && top.includes(hunter) ? hunter : top[0]!;
}

const CLAIM = /^["“]([^"”]{6,80})["”]\s*:\s*(.{20,140})$/;

function plan(lab: LabState): Plan {
  const agent = new Map(lab.agents.map((a) => [a.id, a]));
  const isRoom = (c: ReelConcept) => c.agent !== "template" && c.agent !== CONTROL_AGENT && !lab.single_agent.includes(c.id);
  const first = lab.concepts.filter((c) => isRoom(c) && c.round <= 1 && !c.parents.length).sort((a, b) => a.id.localeCompare(b.id)).slice(0, 12);
  const wall: WallCard[] = first.map((c, i) => {
    const col = i % COLS;
    const row = Math.floor(i / COLS);
    const x = WALL_X + col * (CARD_W + GAP);
    const y = WALL_Y + row * (CARD_H + GAP);
    return {
      c,
      who: agent.get(c.agent),
      verdict: verdictOf(lab.critiques.filter((k) => k.concept === c.id)),
      x,
      y,
      stampAt: SCAN_FROM + SCAN_SPAN * ((x + CARD_W / 2 - WALL_X) / WALL_W) + row * 90,
    };
  });
  const onWall = new Map(wall.map((w, i) => [w.c.id, i]));
  const { critics } = roomCast(lab);

  // a cliché the Cliche Hunter flagged on the wall, preferring a pitch the room killed
  const clicheCands = lab.critiques
    .filter((k) => onWall.has(k.concept) && k.cliche_flags.length)
    .sort((a, b) => Number(b.critic === "cliche_hunter") - Number(a.critic === "cliche_hunter") || Number(wall[onWall.get(b.concept)!]!.verdict === "kill") - Number(wall[onWall.get(a.concept)!]!.verdict === "kill"));
  const ck = clicheCands[0];
  let note = ck ? firstSentence(ck.notes) : "";
  if (note.length > 70 && note.includes(",")) note = `${note.slice(0, note.indexOf(","))}.`;
  const cliche = ck && agent.get(ck.critic) ? { card: onWall.get(ck.concept)!, critic: agent.get(ck.critic)!, flag: ck.cliche_flags[0]!, note: note.length <= 90 ? note : "" } : null;

  // a claim checked against the photos: "<claim>": <what the photo shows>, preferring the Fact Checker
  let truth: Plan["truth"] = null;
  const truthCands = lab.critiques
    .filter((k) => onWall.has(k.concept) && k.concept !== ck?.concept)
    .sort((a, b) => Number(b.critic === "fact_checker") - Number(a.critic === "fact_checker") || onWall.get(a.concept)! - onWall.get(b.concept)!);
  for (const k of truthCands) {
    for (const f of k.truth_flags) {
      const m = CLAIM.exec(f.trim());
      const view = /\bv\d{2}\b/.exec(f)?.[0] ?? null;
      if (!m || !view || !agent.get(k.critic)) continue;
      let fix = m[2]!.replace(/\bv\d{2}\s+/g, "").trim();
      fix = fix.charAt(0).toUpperCase() + fix.slice(1);
      if (!/[.!?]$/.test(fix)) fix += ".";
      truth = { card: onWall.get(k.concept)!, critic: agent.get(k.critic)!, claim: m[1]!, fix, view };
      break;
    }
    if (truth) break;
  }

  // remixes: the greenlit ones first, then steals (a parent pitched by another creator)
  const finalistIds = new Set(lab.finalists);
  const remixCands = lab.concepts
    .filter((c) => isRoom(c) && c.parents.length && c.parents.some((p) => onWall.has(p)))
    .map((c) => {
      const stolen = c.parents.find((p) => onWall.has(p) && wall[onWall.get(p)!]!.c.agent !== c.agent);
      const from = onWall.get(stolen ?? c.parents.find((p) => onWall.has(p))!)!;
      return { c, from, steal: !!stolen, score: (finalistIds.has(c.id) ? 2 : 0) + (stolen ? 1 : 0) };
    })
    .sort((a, b) => b.score - a.score || a.c.id.localeCompare(b.c.id))
    .slice(0, 4);
  const remixes: Remix[] = remixCands.map((r, i) => ({
    c: r.c,
    who: agent.get(r.c.agent),
    from: r.from,
    fromWho: agent.get(wall[r.from]!.c.agent),
    steal: r.steal,
    y: WALL_Y + i * (REMIX_H + REMIX_GAP),
    at: REMIX_FROM + i * REMIX_STEP,
  }));

  const fins = lab.finalists.map((id) => lab.concepts.find((c) => c.id === id)).filter((c): c is ReelConcept => !!c).slice(0, 2);
  const startX = (1920 - fins.length * FIN_W - (fins.length - 1) * 40) / 2;
  const finalists = fins.map((c, i) => {
    const r = remixes.find((x) => x.c.id === c.id);
    const reel = lab.reels.find((x) => x.concept === c.id);
    return {
      c,
      who: agent.get(c.agent),
      reel: reel ? { id: reel.id, label: reel.label } : undefined,
      score: criticScore(lab, c.id),
      from: r ? { x: COL_X, y: r.y } : null,
      x: startX + i * (FIN_W + 40),
    };
  });

  return {
    wall,
    critics: critics.map((a) => ({ a, count: lab.critiques.filter((k) => k.critic === a.id).length })),
    cliche,
    truth,
    remixes,
    finalists,
  };
}

export function SceneCritique({ lab, shoot, story, t }: SceneProps) {
  const p = useMemo(() => plan(lab), [lab]);

  const headIn = phase(t, 0, 500);
  const headOut = phase(t, FINALE - 100, 400);
  const finHead = phase(t, FINALE + 100, 500);
  const dim = 1 - 0.93 * phase(t, FINALE, 700);
  const scanEdge = WALL_X + WALL_W * phase(t, SCAN_FROM, SCAN_SPAN, linear);
  const scanOn = phase(t, SCAN_FROM - 100, 200) * (1 - phase(t, SCAN_FROM + SCAN_SPAN, 300));
  const remixOn = t >= REMIX_FROM - 300;
  const parents = new Set(p.remixes.map((r) => r.from));

  const clicheAt = p.cliche ? Math.max(p.wall[p.cliche.card]!.stampAt + 250, 1700) : 0;
  const truthAt = p.truth ? Math.max(p.wall[p.truth.card]!.stampAt + 250, clicheAt + 900, 2700) : 0;
  const calloutsOut = phase(t, 4300, 300);
  const clicheIn = p.cliche ? phase(t, clicheAt, 280, easeBack) * (1 - calloutsOut) : 0;
  const truthIn = p.truth ? phase(t, truthAt, 280, easeBack) * (1 - calloutsOut) : 0;

  return (
    <div className="pz-critique">
      {/* headline: the critics, then the greenlight */}
      <header className="pz-critique-head" style={{ opacity: headIn * (1 - headOut), transform: `translateY(${(1 - headIn) * 20}px)` }}>
        <h2 className="pz-critique-title">Three critics review every idea.</h2>
        <p className="pz-critique-sub">{"They cut overused ideas and catch claims the photos don't support."}</p>
      </header>
      <header className="pz-critique-head" style={{ opacity: finHead, transform: `translateY(${(1 - finHead) * 20}px)` }} aria-hidden={finHead < 0.5}>
        <h2 className="pz-critique-title">
          <Crown size={52} strokeWidth={1.8} className="pz-critique-crown" aria-hidden /> Two ideas make the cut.
        </h2>
        <p className="pz-critique-sub">
          Picked from {story.ideas} ideas after {story.critiques} reviews.
        </p>
      </header>

      {/* the critics */}
      <div className="pz-critique-critics" style={{ opacity: phase(t, 150, 450) * dim }}>
        {p.critics.map(({ a, count }, i) => {
          const Icon = agentIcon(a.id);
          const on =
            (p.cliche?.critic.id === a.id && clicheIn > 0.5) || (p.truth?.critic.id === a.id && truthIn > 0.5) || (scanOn > 0.5 && !p.cliche && !p.truth);
          const enter = phase(t, 150 + i * 120, 450);
          return (
            <div key={a.id} className="pz-critique-critic" data-on={on || undefined} style={{ opacity: enter, transform: `translateX(${(1 - enter) * -16}px)` }}>
              <span className="pz-critique-critic-icon">
                <Icon size={30} strokeWidth={1.8} aria-hidden />
                {scanOn > 0.2 && <span className="pz-critique-live" aria-hidden />}
              </span>
              <span>
                <span className="pz-critique-critic-name">{a.name}</span>
                <span className="pz-critique-critic-job">{AGENT_JOB[a.id] ?? a.lens}</span>
                <span className="pz-critique-critic-count">
                  <span className="pz-num">{Math.round(countUp(t, SCAN_FROM, SCAN_SPAN, count))}</span> reviews
                </span>
              </span>
            </div>
          );
        })}
      </div>

      {/* the wall of first pitches, stamped as the scan passes */}
      {p.wall.map((w, i) => {
        const Icon = agentIcon(w.c.agent);
        const stamp = phase(t, w.stampAt, 240, easeBack);
        const killed = w.verdict === "kill" ? phase(t, w.stampAt + 200, 400) : 0;
        const fade = remixOn && !parents.has(i) ? 0.3 + 0.7 * (1 - phase(t, REMIX_FROM - 300, 400)) : 1;
        return (
          <article
            key={w.c.id}
            className="pz-critique-card"
            style={
              {
                left: w.x,
                top: w.y,
                width: CARD_W,
                height: CARD_H,
                // a kill fades the card's text to faint (in CSS) so the red stamp stays at full strength
                "--kill": killed,
                opacity: phase(t, 0, 350) * fade * dim,
                transform: `translateY(${killed * 8}px)`,
              } as CSSProperties
            }
          >
            <p className="pz-critique-card-who">
              <Icon size={15} strokeWidth={2} aria-hidden /> {w.who?.name ?? w.c.agent}
            </p>
            <p className="pz-critique-card-title">{w.c.title}</p>
            <p className="pz-critique-card-hook">“{w.c.hook.text}”</p>
            {stamp > 0 && (
              <span className="pz-critique-stamp" data-v={w.verdict} style={{ opacity: Math.min(1, stamp * 1.4), transform: `scale(${1.5 - 0.5 * stamp})` }}>
                {VERDICT_WORD[w.verdict]}
              </span>
            )}
          </article>
        );
      })}

      {/* the scan */}
      {scanOn > 0 && <span className="pz-critique-scan" style={{ left: scanEdge - 160, opacity: scanOn }} aria-hidden />}

      {/* two real notes from the critics */}
      {p.cliche && clicheIn > 0 && <Callout card={p.wall[p.cliche.card]!} critic={p.cliche.critic} tag="Overused" amount={clicheIn} top={CLICHE_TOP}>
        <p className="pz-critique-quote">
          <Strike t={t} at={clicheAt + 450}>“{p.cliche.flag}”</Strike>
        </p>
        {p.cliche.note && <p className="pz-critique-fix">{p.cliche.note}</p>}
      </Callout>}
      {p.truth && truthIn > 0 && <Callout card={p.wall[p.truth.card]!} critic={p.truth.critic} tag="Not true" amount={truthIn} top={p.cliche ? TRUTH_TOP : CLICHE_TOP}>
        <div className="pz-critique-proof">
          <div>
            <p className="pz-critique-quote">
              <Strike t={t} at={truthAt + 450}>“{p.truth.claim}”</Strike>
            </p>
            <p className="pz-critique-fix">{p.truth.fix}</p>
          </div>
          {p.truth.view && shoot?.photos[p.truth.view] && (
            <figure>
              <img src={hdPhoto(shoot.photos[p.truth.view])} alt="" />
              <figcaption>{shoot.labels[p.truth.view] ?? "The photo"}</figcaption>
            </figure>
          )}
        </div>
      </Callout>}

      {/* remixes: arcs from the pitch each one borrowed from */}
      <p className="pz-critique-col-head" style={{ opacity: phase(t, REMIX_FROM - 200, 400) * dim }}>
        Remixes
      </p>
      <svg className="pz-critique-arcs" width={1920} height={1080} aria-hidden style={{ opacity: dim }}>
        {p.remixes.map((r) => {
          const from = p.wall[r.from]!;
          const x1 = from.x + CARD_W - 18;
          const y1 = from.y + 18;
          const x2 = COL_X - 8;
          const y2 = r.y + REMIX_H / 2;
          const d = `M ${x1} ${y1} C ${x1 + (x2 - x1) * 0.4} ${Math.min(y1, y2) - 110}, ${x2 - 140} ${y2}, ${x2} ${y2}`;
          const draw = phase(t, r.at - 280, 640, easeInOut);
          if (draw <= 0) return null;
          return (
            <g key={r.c.id}>
              <path d={d} pathLength={1} className="pz-critique-arc" style={{ strokeDasharray: 1, strokeDashoffset: 1 - draw }} />
              <circle cx={x1} cy={y1} r={4} className="pz-critique-arc-dot" />
              {draw > 0.97 && <circle cx={x2} cy={y2} r={4.5} className="pz-critique-arc-dot" />}
            </g>
          );
        })}
      </svg>
      {p.remixes.map((r) => {
        const Icon = agentIcon(r.c.agent);
        const enter = phase(t, r.at, 420);
        const fin = p.finalists.some((f) => f.c.id === r.c.id);
        const handoff = fin ? 1 - phase(t, FINALE + 100, 150) : dim;
        if (enter <= 0) return null;
        return (
          <article
            key={r.c.id}
            className="pz-critique-remix"
            style={{ top: r.y, height: REMIX_H, opacity: enter * handoff, transform: `translateX(${(1 - enter) * 24}px)` }}
          >
            <p className="pz-critique-remix-steal">
              <Icon size={14} strokeWidth={2.2} aria-hidden />
              {r.steal ? `${r.who?.name ?? r.c.agent} borrowed from ${r.fromWho?.name ?? "another creator"}` : `${r.who?.name ?? r.c.agent} rewrote its idea`}
            </p>
            <p className="pz-critique-remix-title">{r.c.title}</p>
            <p className="pz-critique-remix-hook">“{r.c.hook.text}”</p>
          </article>
        );
      })}

      {/* the room's work, counted */}
      <div className="pz-critique-counters" style={{ opacity: phase(t, REMIX_FROM - 200, 400) * (1 - 0.6 * phase(t, FINALE, 700)) }}>
        {[
          [story.critiques, "reviews"],
          [story.truthFlags, "problems caught"],
          [story.remixes, "rewrites"],
          [story.steals, "ideas borrowed"],
        ].map(([n, label]) => (
          <p key={label as string} className="pz-critique-counter">
            <b className="pz-num">{Math.round(countUp(t, REMIX_FROM - 100, 2600, n as number))}</b>
            <span>{label}</span>
          </p>
        ))}
      </div>

      {/* the greenlight */}
      {p.finalists.map((f, i) => {
        const move = phase(t, FINALE + 100, 1100, easeInOut);
        if (t < FINALE + 100) return null;
        const s0 = COL_W / FIN_W;
        const s = s0 + (1 - s0) * move;
        const dx = f.from ? (f.from.x - f.x) * (1 - move) : 0;
        const dy = f.from ? (f.from.y - FIN_Y) * (1 - move) : 40 * (1 - move);
        const hook = phase(t, FINALE + 1000 + i * 150, 600);
        const badge = phase(t, FINALE + 1300 + i * 150, 320, easeBack);
        const Icon = agentIcon(f.c.agent);
        const beat = f.c.beats[0];
        const photo = beat ? hdPhoto(shoot?.photos[beat.view]) : undefined;
        return (
          <article
            key={f.c.id}
            className="pz-critique-fin"
            style={{
              left: f.x,
              top: FIN_Y,
              width: FIN_W,
              height: FIN_H,
              opacity: phase(t, FINALE + 100, 220),
              transform: `translate(${dx}px, ${dy}px) scale(${f.from ? s : 0.92 + 0.08 * move})`,
            }}
          >
            <div className="pz-critique-fin-photo">{photo && <img src={photo} alt="" style={focusStyle(beat?.focus)} />}</div>
            <div className="pz-critique-fin-body">
              <span className="pz-critique-fin-badge" style={{ opacity: badge, transform: `scale(${0.7 + 0.3 * badge})` }}>
                <Crown size={15} strokeWidth={2.2} aria-hidden /> Picked
              </span>
              {f.reel && (
                <p className="pz-critique-fin-reel">
                  Reel {f.reel.id} · {f.reel.label}
                </p>
              )}
              <p className="pz-critique-fin-hook" style={{ opacity: 0.25 + 0.75 * hook, transform: `translateY(${(1 - hook) * 10}px)` }}>
                “{f.c.hook.text}”
              </p>
              <div className="pz-critique-fin-meta">
                <p className="pz-critique-fin-by">
                  <Icon size={17} strokeWidth={2} aria-hidden />
                  <span>
                    {f.c.title} · {f.who?.name ?? f.c.agent}
                  </span>
                </p>
                {f.score > 0 && (
                  <p className="pz-critique-fin-score">
                    {"Critics' score "}
                    <span className="pz-num">{f.score.toFixed(1)}</span>/10
                  </p>
                )}
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}

function Callout({ card, critic, tag, amount, top, children }: { card: WallCard; critic: AgentCard; tag: string; amount: number; top: number; children: ReactNode }) {
  const Icon = agentIcon(critic.id);
  const x1 = card.x + CARD_W - 6;
  const y1 = card.y + CARD_H / 2;
  const x2 = NOTE_X;
  const y2 = top + 44;
  return (
    <>
      <svg className="pz-critique-link" width={1920} height={1080} aria-hidden style={{ opacity: Math.min(1, amount) }}>
        <path d={`M ${x1} ${y1} C ${x1 + 60} ${y1}, ${x2 - 70} ${y2}, ${x2} ${y2}`} pathLength={1} style={{ strokeDasharray: 1, strokeDashoffset: 1 - Math.min(1, amount) }} />
        <circle cx={x1} cy={y1} r={5} />
      </svg>
      <aside className="pz-critique-callout" style={{ left: NOTE_X, top, width: NOTE_W, opacity: Math.min(1, amount * 1.3), transform: `translateX(${(1 - amount) * 18}px)` }}>
        <p className="pz-critique-callout-head">
          <span className="pz-critique-callout-icon">
            <Icon size={17} strokeWidth={2} aria-hidden />
          </span>
          {critic.name}
          <span className="pz-critique-tag">{tag}</span>
        </p>
        {children}
      </aside>
    </>
  );
}

function Strike({ t, at, children }: { t: number; at: number; children: ReactNode }) {
  return (
    <span className="pz-critique-claim" style={{ "--strike": phase(t, at, 420) } as CSSProperties}>
      {children}
    </span>
  );
}
