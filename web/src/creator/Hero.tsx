// The results band at the top of the dashboard: what a realtor wants to know in three seconds. Which reel to
// post, how far it is predicted to travel against a typical listing reel, and what it cost to make.

import "../styles/creator-hero.css";
import { Bot, CircleDollarSign, Clock, Eye, Hand, Mail, MonitorPlay, Play, Users, type LucideIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { buildStory, type ReelStats } from "./present/metrics";
import { usePresenter } from "./present/Presenter";
import { useCreator } from "./store";
import { fmtInt, fmtUsd, goToSection, Rich, useReducedMotion } from "./ui";

/** 0 -> 1 over `ms` once `start` turns true; 1 straight away under reduced motion. */
function useCountUp(start: boolean, ms = 1300): number {
  const reduced = useReducedMotion();
  const [p, setP] = useState(reduced ? 1 : 0);
  useEffect(() => {
    if (reduced) return setP(1);
    if (!start) return;
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const x = Math.min(1, (now - t0) / ms);
      setP(1 - Math.pow(1 - x, 3));
      if (x < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [start, ms, reduced]);
  return p;
}

/** True once the element has been on screen. A callback ref, so it works when the element mounts late. */
function useSeen<T extends HTMLElement>(): [(el: T | null) => void, boolean] {
  const [el, setEl] = useState<T | null>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (!el || seen) return;
    if (typeof IntersectionObserver === "undefined") return setSeen(true);
    const io = new IntersectionObserver(([e]) => e?.isIntersecting && setSeen(true), { threshold: 0.3 });
    io.observe(el);
    return () => io.disconnect();
  }, [el, seen]);
  return [setEl, seen];
}

const ratio = (a: number, b: number) => (b > 0 ? a / b : null);
const fmtX = (x: number) => `${x >= 9.95 ? Math.round(x) : x.toFixed(1)}×`;

interface Kpi {
  icon: LucideIcon;
  label: string;
  value: number;
  base: number;
  format: (v: number) => string;
  /** "3.1×", "+3" or "+36 pts" */
  gain: string | null;
}

function kpis(hero: ReelStats, base: ReelStats): Kpi[] {
  const x = (a: number, b: number) => {
    const r = ratio(a, b);
    return r != null && r > 1.05 ? fmtX(r) : a > b ? `+${fmtInt(a - b)}` : null;
  };
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const pts = Math.round((hero.stopRate - base.stopRate) * 100);
  return [
    { icon: Eye, label: "Views", value: hero.views, base: base.views, format: fmtInt, gain: x(hero.views, base.views) },
    { icon: Mail, label: "Buyer inquiries", value: hero.inquiries, base: base.inquiries, format: fmtInt, gain: x(hero.inquiries, base.inquiries) },
    { icon: Users, label: "Buyers reached", value: hero.buyerViews, base: base.buyerViews, format: fmtInt, gain: x(hero.buyerViews, base.buyerViews) },
    { icon: Hand, label: "Stopped scrolling", value: hero.stopRate, base: base.stopRate, format: pct, gain: pts > 0 ? `+${pts} pts` : null },
  ];
}

export function Hero() {
  const lab = useCreator((s) => s.lab);
  const shoot = useCreator((s) => s.shoot);
  const api = useCreator((s) => s.api);
  const shootId = useCreator((s) => s.shootId);
  const reduced = useReducedMotion();
  const [ref, seen] = useSeen<HTMLElement>();
  const p = useCountUp(seen);
  const story = useMemo(() => (lab && lab.status === "done" && lab.simulations.length ? buildStory(lab, shoot?.listing?.price) : null), [lab, shoot?.listing?.price]);
  if (!lab || !story || !story.hero || !story.template || story.hero.id === story.template.id) return null;

  const hero = story.hero;
  const base = story.template;
  const reel = lab.reels.find((r) => r.id === hero.id);
  const hook = lab.concepts.find((c) => c.id === reel?.concept)?.hook.text;
  const poster = reel && api && shootId ? api.fileUrl(shootId, lab.id, reel.poster) : null;
  const minutes = Math.max(1, Math.round(story.seconds / 60));
  const tiles = kpis(hero, base);

  return (
    <section className="cl-hero" ref={ref} aria-labelledby="cl-hero-title">
      <button className="cl-hero-reel" onClick={() => goToSection("cl-reels", reduced)} aria-label={`Watch reel ${hero.id}, ${hero.title}`}>
        <span className="cl-hero-poster">
          {poster && <img src={poster} alt="" />}
          <span className="cl-hero-play" aria-hidden>
            <Play size={13} fill="currentColor" /> Watch
          </span>
          <span className="cl-hero-tag">
            <Bot size={12} aria-hidden /> AI team's pick
          </span>
        </span>
      </button>

      <div className="cl-hero-body">
        <p className="cl-hero-kicker">
          Post this one: <b>{hero.title}</b>
          <span className="cl-hero-reelid"> · reel {hero.id}</span>
        </p>
        {hook && (
          <p className="cl-hero-hook">
            “<Rich text={hook} />”
          </p>
        )}
        <h2 id="cl-hero-title" className="cl-hero-title">
          Your best reel is predicted to get <span className="cl-hero-x">{story.viewsX != null ? fmtX(story.viewsX) : "more than"}</span> the views of a typical listing reel.
        </h2>

        <ul className="cl-hero-kpis">
          {tiles.map((k, i) => (
            <li key={k.label} className="cl-kpi" style={{ animationDelay: `${120 + i * 90}ms` }}>
              <span className="cl-kpi-icon" aria-hidden>
                <k.icon size={16} />
              </span>
              <span className="cl-kpi-label">{k.label}</span>
              <span className="cl-kpi-value num">{k.format(k.value * p)}</span>
              <span className="cl-kpi-foot">
                {k.gain && <span className="cl-kpi-gain num">{k.gain}</span>}
                <span className="cl-kpi-base">
                  typical reel: <span className="num">{k.format(k.base)}</span>
                </span>
              </span>
            </li>
          ))}
        </ul>

        <div className="cl-hero-foot">
          <p className="cl-hero-made">
            <span>
              <Bot size={14} aria-hidden /> Made by {story.roomAgents} AI agents
            </span>
            <span>
              <Clock size={14} aria-hidden /> in {minutes} min
            </span>
            <span>
              <CircleDollarSign size={14} aria-hidden /> for <span className="num">{fmtUsd(story.usd)}</span>
            </span>
          </p>
          <button className="btn btn-primary cl-hero-go" onClick={() => usePresenter.getState().show()}>
            <MonitorPlay size={14} aria-hidden /> Watch the 1-minute walkthrough
          </button>
        </div>
        <p className="cl-hero-honest">
          Predicted: a simulated launch to {fmtInt(story.followers)} followers over {story.hours} h, scored by an AI viewer panel. Not platform data.
        </p>
      </div>
    </section>
  );
}
