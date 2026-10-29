// Scene 6, the payoff: what the creative reel is predicted to do for the realtor in its first two days,
// what it cost to make, then the end card. Launch figures come from the simulated launch.

import "../../styles/present-payoff.css";
import { Bot, Clock, Coins, Eye, House, MessageCircle, Sparkles, type LucideIcon } from "lucide-react";
import { useMemo } from "react";
import { interpolateSeries } from "../sim";
import type { SimPoint } from "../types";
import { fmtInt, niceTicks } from "../ui";
import type { ReelStats } from "./metrics";
import { easeInOut, easeOut, phase, type SceneProps } from "./types";

// the race: the chart's clock runs from 0 to the last hour over this window (ms)
const RACE_FROM = 900;
const RACE_SPAN = 5600;
const STRIP_AT = 7500;
const END_AT = 9500;

// chart geometry inside its 1000 x 440 box
const CW = 1000;
const CH = 420;
const L = 78;
const R = 210;
const T = 18;
const B = 42;

const kindName = (r: ReelStats) => (r.kind === "baseline" ? "Typical listing reel" : r.kind === "single_agent" ? "Single AI agent" : "AI creative team");
const ratio = (v: number) => `${v >= 9.95 ? Math.round(v) : v.toFixed(1)}×`;
const usd = (v: number) => `$${v.toFixed(2)}`;

/** Fades and lifts an element in over [from, from + span] ms. */
const rise = (t: number, from: number, span = 600, dy = 28) => {
  const p = phase(t, from, span, easeOut);
  return { opacity: p, transform: `translateY(${((1 - p) * dy).toFixed(1)}px)` };
};

/** The series up to hour h, ending exactly at h. */
function pointsTo(series: SimPoint[], h: number, x: (h: number) => number, y: (v: number) => number): string {
  const pts = series.filter((p) => p.hour < h);
  const end = interpolateSeries(series, h);
  return [...pts, end].map((p) => `${x(p.hour).toFixed(1)},${y(p.views).toFixed(1)}`).join(" ");
}

export function ScenePayoff({ lab, story, t }: SceneProps) {
  const hero = story.hero;
  const tpl = story.template;
  const hours = story.hours || 48;
  const race = phase(t, RACE_FROM, RACE_SPAN, easeInOut);
  const h = hours * race;
  const sims = useMemo(() => new Map(lab.simulations.map((s) => [s.reel, s])), [lab.simulations]);
  const heroSim = hero ? sims.get(hero.id) : undefined;
  const tplSim = tpl ? sims.get(tpl.id) : undefined;

  if (!hero || !tpl || !heroSim || !tplSim) return <EndCard t={t} story={story} always />;

  const at = (s: typeof heroSim) => interpolateSeries(s.series, h);
  const heroNow = at(heroSim);
  const tplNow = at(tplSim);
  // buyers reached grows with views, in the proportion the finished launch shows
  const buyersNow = (r: ReelStats, viewsNow: number) => (r.views > 0 ? (r.buyerViews * viewsNow) / r.views : 0);
  const only = hero.inquiries > 0 && story.reels.every((r) => r.id === hero.id || r.inquiries === 0);
  const x = story.viewsX ?? (tpl.views > 0 ? hero.views / tpl.views : 1);
  const shownX = 1 + (x - 1) * race;
  const done = race >= 1;
  const minutes = Math.max(1, Math.round(story.seconds / 60));

  // chart scales
  const yTicks = niceTicks(Math.max(...lab.simulations.map((s) => s.totals.views), 1), 3);
  const top = yTicks[yTicks.length - 1]!;
  const sx = (hr: number) => L + (hr / hours) * (CW - L - R);
  const sy = (v: number) => T + (1 - v / top) * (CH - T - B);
  const others = lab.simulations.filter((s) => s.reel !== hero.id && s.reel !== tpl.id);
  const heroY = sy(heroNow.views);
  const tplY = Math.max(sy(tplNow.views), heroY + 58);

  const tiles: { icon: LucideIcon; label: string; value: number; vs: number; note?: string; badge?: string }[] = [
    { icon: Eye, label: "Views", value: heroNow.views, vs: tplNow.views, badge: done ? ratio(x) : undefined },
    { icon: MessageCircle, label: "Buyer inquiries", value: Math.round(heroNow.inquiries), vs: Math.round(tplNow.inquiries), badge: done && only ? "Only reel" : undefined },
    {
      icon: House,
      label: "Buyers reached",
      value: buyersNow(hero, heroNow.views),
      vs: buyersNow(tpl, tplNow.views),
      note: "First-time, local, relocating and downsizing buyers",
    },
  ];

  return (
    <div className="pz-payoff">
      <header className="pz-payoff-head">
        <p className="pz-kicker" style={rise(t, 0, 500, 16)}>
          The payoff · predicted first {hours} hours
        </p>
        <h2 className="pz-payoff-title">
          <span className="pz-payoff-l1" style={rise(t, 120)}>
            <span className="pz-payoff-x">{ratio(shownX)}</span> the views.
          </span>
          <span className="pz-payoff-l2" style={rise(t, 420)}>
            {only ? "And the only buyer inquiries." : `And ${hero.inquiries} buyer inquiries to ${tpl.inquiries}.`}
          </span>
        </h2>
      </header>

      <figure className="pz-payoff-chart" style={rise(t, 500, 700, 20)}>
        <svg width={CW} height={CH} role="img" aria-label={`Views over the first ${hours} hours: ${kindName(hero)} ${fmtInt(hero.views)}, ${kindName(tpl)} ${fmtInt(tpl.views)}`}>
          {niceTicks(top, 3).map((v) => (
            <g key={v} className="pz-payoff-grid">
              <line x1={L} x2={CW - R} y1={sy(v)} y2={sy(v)} />
              <text x={L - 14} y={sy(v) + 6} textAnchor="end">
                {v >= 1000 ? `${v / 1000}K` : v}
              </text>
            </g>
          ))}
          {[0, 12, 24, 36, hours].map((hr) => (
            <text key={hr} className="pz-payoff-axis" x={sx(hr)} y={CH - 8} textAnchor="middle">
              {hr}h
            </text>
          ))}
          {others.map((s) => (
            <polyline key={s.reel} className="pz-payoff-line" data-tone="other" points={pointsTo(s.series, h, sx, sy)} />
          ))}
          <polyline className="pz-payoff-line" data-tone="template" points={pointsTo(tplSim.series, h, sx, sy)} />
          <polyline className="pz-payoff-line" data-tone="hero" points={pointsTo(heroSim.series, h, sx, sy)} />
          {h > 0.05 && (
            <>
              <line className="pz-payoff-now" x1={sx(h)} x2={sx(h)} y1={T} y2={CH - B} />
              <circle className="pz-payoff-dot" data-tone="template" cx={sx(h)} cy={sy(tplNow.views)} r={7} />
              <circle className="pz-payoff-dot" data-tone="hero" cx={sx(h)} cy={heroY} r={9} />
              <g className="pz-payoff-end" data-tone="hero" transform={`translate(${sx(h) + 22} ${heroY})`}>
                <text y={8} className="pz-payoff-end-v">
                  {fmtInt(heroNow.views)}
                </text>
                <text y={34} className="pz-payoff-end-k">
                  {kindName(hero)}
                </text>
              </g>
              <g className="pz-payoff-end" data-tone="template" transform={`translate(${sx(h) + 22} ${tplY})`}>
                <text y={8} className="pz-payoff-end-v">
                  {fmtInt(tplNow.views)}
                </text>
                <text y={34} className="pz-payoff-end-k">
                  {kindName(tpl)}
                </text>
              </g>
            </>
          )}
        </svg>
        <p className="pz-payoff-hour num" style={{ opacity: phase(t, RACE_FROM - 200, 300) }}>
          Hour <b>{Math.floor(h)}</b> <span>of {hours}</span>
        </p>
        <figcaption className="pz-payoff-legend">
          <span data-tone="hero">{kindName(hero)}</span>
          <span data-tone="template">{kindName(tpl)}</span>
          {others.length > 0 && <span data-tone="other">The lab's other reels</span>}
        </figcaption>
        <p className="pz-payoff-sim">
          Predicted · simulated {fmtInt(story.followers)}-follower account, first {hours} h
        </p>
      </figure>

      <div className="pz-payoff-tiles">
        {tiles.map((k, i) => (
          <article key={k.label} className="pz-payoff-tile" style={rise(t, 700 + i * 220, 600, 30)}>
            <span className="pz-payoff-icon" aria-hidden>
              <k.icon size={34} strokeWidth={1.8} />
            </span>
            <div className="pz-payoff-tile-text">
              <p className="pz-payoff-tile-label">
                {k.label}
                {k.badge && (
                  <span className="pz-payoff-badge" style={rise(t, RACE_FROM + RACE_SPAN, 400, 8)}>
                    {k.badge}
                  </span>
                )}
              </p>
              <p className="pz-payoff-tile-value">{fmtInt(k.value)}</p>
              <p className="pz-payoff-tile-vs">
                vs <b>{fmtInt(k.vs)}</b> for a typical listing reel
              </p>
              {k.note && <p className="pz-payoff-tile-note">{k.note}</p>}
            </div>
          </article>
        ))}
      </div>

      <Economics t={t} story={story} minutes={minutes} />
      <EndCard t={t} story={story} />
    </div>
  );
}

function Economics({ t, story, minutes }: { t: number; story: SceneProps["story"]; minutes: number }) {
  const p = phase(t, STRIP_AT, 800, easeOut);
  return (
    <section className="pz-payoff-econ" style={{ opacity: p, transform: `translateY(${((1 - p) * 120).toFixed(1)}px)` }} aria-label="What it cost">
      <p className="pz-payoff-econ-main">
        <span>
          <Bot size={34} strokeWidth={1.8} aria-hidden /> Made by <b>{story.roomAgents} AI agents</b>
        </span>
        <span>
          <Clock size={32} strokeWidth={1.8} aria-hidden /> in <b>{minutes} min</b>
        </span>
        <span>
          <Coins size={32} strokeWidth={1.8} aria-hidden /> for <b className="pz-payoff-gold">{usd(story.usd)}</b>
        </span>
      </p>
      <p className="pz-payoff-econ-sub">
        <span>
          <b>{usd(story.usdPerReel)}</b> per finished reel
        </span>
        {story.commission != null && story.price != null && (
          <span>
            <b>${fmtInt(story.commission)}</b> commission on this listing<sup>*</sup>
          </span>
        )}
      </p>
      <p className="pz-payoff-foot">
        {story.commission != null && story.price != null && (
          <>
            {" "}
            <sup>*</sup> Assumes a {(story.commissionRate * 100).toFixed(1).replace(/\.0$/, "")}% listing commission on ${fmtInt(story.price)}.
          </>
        )}
      </p>
    </section>
  );
}

function EndCard({ t, story, always = false }: { t: number; story: SceneProps["story"]; always?: boolean }) {
  const p = always ? 1 : phase(t, END_AT, 700, easeOut);
  if (p <= 0) return null;
  const from = always ? 0 : END_AT;
  return (
    <div className="pz-payoff-endcard" style={{ opacity: p }}>
      <div className="pz-payoff-endcard-inner">
        <p className="pz-payoff-mark" style={rise(t, from + 150, 700, 30)}>
          <Sparkles size={64} strokeWidth={1.6} aria-hidden /> ShootAgent
        </p>
        <p className="pz-payoff-tagline" style={rise(t, from + 400, 700, 24)}>
          A creative team for every listing.
        </p>
        <p className="pz-payoff-credits" style={rise(t, from + 650, 700, 18)}>
          {story.roomAgents} agents create · {story.judges + story.viewers} judge blind · built on Claude
        </p>
      </div>
    </div>
  );
}
