// Scene 5, "Measured": creativity is measured, not claimed. The cliché corpus settles into a cloud, the
// template drops into it, the room's ideas burst out past it, and a blind jury scores the reels.

import "../../styles/present-proof.css";
import { Check, Eye, Gavel } from "lucide-react";
import { useMemo, type CSSProperties } from "react";
import { cloudEllipse } from "../CreativityMap";
import type { MapPoint } from "../types";
import { easeBack, easeInOut, easeOut, phase, type SceneProps } from "./types";

// the map's drawing area; the right margin leaves room for the reel labels
const MW = 1040;
const MH = 700;
const PAD = { l: 40, r: 330, t: 50, b: 40 };

/** Deterministic 0-1 noise per index, so every take scatters the dots the same way. */
const noise = (i: number, salt: number) => {
  const s = Math.sin(i * 12.9898 + salt * 78.233) * 43758.5453;
  return s - Math.floor(s);
};

const fmtX = (v: number) => `${v >= 9.95 ? Math.round(v) : v.toFixed(1)}×`;

const VIEWER_ROLE: Record<string, string> = {
  v_scroller: "Casual scroller",
  v_genz: "Gen Z scroller",
  v_first_buyer: "First-time buyer",
  v_renter: "Renter",
  v_design: "Design fan",
  v_local_buyer: "Local buyer",
  v_investor: "Investor",
  v_relocator: "Relocator",
  v_peer: "Fellow agent",
  v_downsizer: "Downsizer",
  v_neighbor: "Neighbour",
};
const JUDGE_ROLE: Record<string, string> = {
  judge_viral: "Viral strategist",
  judge_listing: "Listing pro",
  judge_editor: "Film editor",
  judge_cd: "Ad creative",
  judge_critic: "Culture critic",
};

/** "local buyer actively searching, pre-approved" -> "Local buyer actively" (for labs with other personas). */
function shortRole(text: string): string {
  const head = text.split(/[:,(]/)[0]!.split(/\s+(?:who|for|and|of|on|in|with)\s+/)[0]!.trim();
  const words = head.split(/\s+/).slice(0, 3).join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const fade = (p: number, rise = 18): CSSProperties => ({ opacity: p, transform: `translateY(${(1 - p) * rise}px)` });

export function SceneProof({ lab, story, t }: SceneProps) {
  const ev = lab.evaluation!;

  const geo = useMemo(() => {
    const pts = ev.map?.points ?? [];
    if (!pts.length) return null;
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
    const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
    const sx = (x: number) => PAD.l + ((x - x0) / (x1 - x0 || 1)) * (MW - PAD.l - PAD.r);
    const sy = (y: number) => MH - PAD.b - ((y - y0) / (y1 - y0 || 1)) * (MH - PAD.t - PAD.b);
    const at = (p: MapPoint) => ({ x: sx(p.x), y: sy(p.y) });
    const cliches = pts.filter((p) => p.kind === "cliche").map(at);
    const zone = cliches.length > 2 ? cloudEllipse(cliches) : null;
    const tp = pts.find((p) => p.kind === "baseline");
    const ideas = pts
      .filter((p) => p.kind === "concept" || p.kind === "finalist" || p.kind === "single_agent")
      .sort((a, b) => (a.kind === "single_agent" ? 1 : 0) - (b.kind === "single_agent" ? 1 : 0) || a.id.localeCompare(b.id))
      .map((p) => ({ p, ...at(p) }));
    return { cliches, zone, template: tp ? at(tp) : null, ideas };
  }, [ev.map]);

  const reelOf = useMemo(() => new Map(lab.reels.filter((r) => r.kind !== "baseline").map((r) => [r.concept, r])), [lab.reels]);

  const jury = useMemo(
    () => [
      ...ev.judges.filter((j) => j.id !== "head_judge").map((j) => ({ id: j.id, name: j.name.split(/\s+/)[0]!, role: JUDGE_ROLE[j.id] ?? shortRole(j.lens), judge: true })),
      ...ev.viewers.map((v) => ({ id: v.id, name: v.name.split(/\s+/)[0]!, role: VIEWER_ROLE[v.id] ?? shortRole(v.segment), judge: false })),
    ],
    [ev.judges, ev.viewers],
  );

  const template = story.template;
  const hero = story.hero;
  const w = story.kendallW;

  // timeline (ms): headline 0-0.9 s · cliché cloud 0-2.4 s · template drops 2.5-3.4 s · ideas burst 4-6.5 s ·
  // reel labels 5.8-7 s · jury 0.5-4.6 s · scoreboard 6-7.6 s · agreement 7.8-8.6 s · hold from 9.2 s
  const zoneIn = phase(t, 1500, 900);
  const dropP = phase(t, 2500, 900, easeBack);
  const tplLabel = phase(t, 3150, 600);
  const labelsIn = phase(t, 5800, 700);
  const heroIn = phase(t, 6300, 700);
  const centre = geo?.zone ? { x: geo.zone.cx, y: geo.zone.cy } : { x: MW / 2, y: MH / 2 };

  return (
    <div className="pz-proof">
      <header className="pz-proof-head">
        <h2 className="pz-proof-title">
          <span style={fade(phase(t, 100, 700), 28)}>Creativity you can measure.</span>
          <span className="pz-proof-title-dim" style={fade(phase(t, 350, 700), 28)}>
            Scored by judges who never saw who made what.
          </span>
        </h2>
      </header>

      {geo && (
        <svg className="pz-proof-map" width={MW} height={MH} viewBox={`0 0 ${MW} ${MH}`} aria-hidden>
          {geo.zone && (
            <g style={{ opacity: zoneIn }}>
              <ellipse
                className="pz-proof-zone"
                cx={geo.zone.cx}
                cy={geo.zone.cy}
                rx={geo.zone.rx}
                ry={geo.zone.ry}
                transform={`rotate(${geo.zone.angle} ${geo.zone.cx} ${geo.zone.cy})`}
                strokeDasharray="6 7"
                style={{ strokeDashoffset: (1 - zoneIn) * 120 }}
              />
            </g>
          )}

          {geo.cliches.map((c, i) => {
            const delay = noise(i, 1) * 900;
            const p = phase(t, delay, 1300, easeInOut);
            const fx = noise(i, 2) * MW;
            const fy = noise(i, 3) * MH;
            return <circle key={i} className="pz-proof-cliche" cx={fx + (c.x - fx) * p} cy={fy + (c.y - fy) * p} r={3.2} style={{ opacity: Math.min(1, p * 1.6) * 0.8 }} />;
          })}

          {geo.zone && (
            <text className="pz-proof-zone-label" x={geo.zone.cx} y={geo.zone.cy + Math.min(geo.zone.ry, geo.zone.rx) * 0.62} textAnchor="middle" style={{ opacity: phase(t, 1900, 600) }}>
              <tspan className="pz-proof-zone-n">{story.cliches}</tspan> overused listing lines
            </text>
          )}

          {geo.ideas.map(({ p, x, y }, i) => {
            const k = phase(t, 4000 + i * 55, 800, easeOut);
            if (k <= 0) return null;
            const cx = centre.x + (x - centre.x) * k;
            const cy = centre.y + (y - centre.y) * k;
            const reel = reelOf.get(p.id);
            if (p.kind === "single_agent") {
              return <circle key={p.id} className="pz-proof-solo" cx={cx} cy={cy} r={6.5} style={{ opacity: k }} />;
            }
            return (
              <g key={p.id} style={{ opacity: k }}>
                <circle cx={cx} cy={cy} r={p.kind === "finalist" || reel ? 9 : 7} fill="var(--pz-ink)" />
              </g>
            );
          })}

          {geo.ideas
            .filter(({ p }) => reelOf.has(p.id))
            .map(({ p, x, y }) => {
              const reel = reelOf.get(p.id)!;
              const isHero = hero?.concept === p.id;
              return (
                <g key={`l-${p.id}`} style={{ opacity: labelsIn }}>
                  <circle className="pz-proof-ring" data-hero={isHero || undefined} cx={x} cy={y} r={15 + (1 - labelsIn) * 10} />
                  <text className="pz-proof-label" data-hero={isHero || undefined} x={x + 26} y={y + 7}>
                    <tspan className="pz-proof-label-id">{reel.id}</tspan> · {p.label}
                  </text>
                  {isHero && story.noveltyX != null && (
                    <g style={{ opacity: heroIn }}>
                      <text className="pz-proof-callout" x={x + 26} y={y + 50}>
                        {fmtX(story.noveltyX)} more original
                      </text>
                      <text className="pz-proof-callout-sub" x={x + 26} y={y + 80}>
                        than the typical reel
                      </text>
                    </g>
                  )}
                </g>
              );
            })}

          {geo.template && dropP > 0 && (
            <g>
              <rect
                className="pz-proof-template"
                x={geo.template.x - 9}
                y={geo.template.y - 9 - (1 - dropP) * 260}
                width={18}
                height={18}
                rx={3}
                style={{ opacity: Math.min(1, dropP * 2) }}
              />
              <text className="pz-proof-tlabel" x={geo.template.x + 22} y={geo.template.y + 7} style={{ opacity: tplLabel }}>
                Typical reel
              </text>
            </g>
          )}
        </svg>
      )}

      <section className="pz-proof-jury">
        <div style={fade(phase(t, 300, 700))}>
          <p className="pz-proof-jury-title">{story.judges + story.viewers} AI judges and viewers</p>
        </div>
        <ul className="pz-proof-grid">
          {jury.map((j, i) => {
            const pop = phase(t, 500 + i * 250, 380, easeBack);
            const tick = phase(t, 900 + i * 250, 300, easeBack);
            const Icon = j.judge ? Gavel : Eye;
            return (
              <li key={j.id} className="pz-proof-chip" data-judge={j.judge || undefined} style={{ opacity: Math.min(1, pop * 1.4), transform: `scale(${0.8 + 0.2 * pop})` }}>
                <Icon size={20} strokeWidth={1.8} className="pz-proof-chip-icon" aria-hidden />
                <span className="pz-proof-chip-text">
                  <span className="pz-proof-chip-name">{j.name}</span>
                  <span className="pz-proof-chip-role">{j.role}</span>
                </span>
                <span className="pz-proof-tick" style={{ opacity: tick, transform: `scale(${tick})` }} aria-hidden>
                  <Check size={13} strokeWidth={3} />
                </span>
              </li>
            );
          })}
        </ul>

        {template && hero && (
          <div className="pz-proof-score" style={fade(phase(t, 6000, 600))}>
            <p className="pz-proof-score-label">Creativity score, out of 10</p>
            {[
              { key: "t", label: "Typical reel", value: template.creativity, hero: false },
              { key: "h", label: `${hero.id} · ${hero.title}`, value: hero.creativity, hero: true },
            ].map((row) => {
              const v = row.value * phase(t, 6200, 1400, easeOut);
              return (
                <div key={row.key} className="pz-proof-row" data-hero={row.hero || undefined}>
                  <span className="pz-proof-row-label">{row.label}</span>
                  <span className="pz-proof-bar">
                    <span style={{ width: `${(v / 10) * 100}%` }} />
                  </span>
                  <span className="pz-proof-row-v num">{v.toFixed(1)}</span>
                </div>
              );
            })}
            {w != null && (
              <p className="pz-proof-agree" style={fade(phase(t, 7800, 700), 10)}>
                {w < 0.5 ? "The judges disagreed on the order." : `The ${story.judges} judges ranked the reels almost the same way.`}
              </p>
            )}
          </div>
        )}
      </section>

      <p className="pz-proof-foot" style={{ opacity: phase(t, 6500, 700) }}>
        Originality is how far a reel's lines are from {story.cliches} overused listing lines.
      </p>
    </div>
  );
}
