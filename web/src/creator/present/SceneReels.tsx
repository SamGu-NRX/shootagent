// Scene 4, before / after: the template reel and the AI team's most-viewed reel playing side by side, the
// agents' hook typed out, and three blind-panel comparisons counting up.

import "../../styles/present-reels.css";
import { ArrowRight, Check, Sparkles } from "lucide-react";
import { useEffect, useRef, type CSSProperties } from "react";
import type { ReelStats } from "./metrics";
import { countUp, easeOut, linear, phase, type SceneProps } from "./types";

/** Plays a reel in step with the scene clock: from the top on mount, paused with the presenter, re-cued on a seek. */
function useSyncedVideo(t: number, playing: boolean) {
  const ref = useRef<HTMLVideoElement>(null);
  const last = useRef(t);
  useEffect(() => {
    if (ref.current) ref.current.currentTime = 0;
  }, []);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    if (playing) void v.play().catch(() => undefined);
    else v.pause();
  }, [playing]);
  useEffect(() => {
    const v = ref.current;
    const jumped = Math.abs(t - last.current) > 400;
    last.current = t;
    if (!v || !(jumped || !playing)) return;
    if (Number.isFinite(v.duration) && v.duration > 0) v.currentTime = (t / 1000) % v.duration;
  }, [t, playing]);
  return ref;
}

interface Row {
  key: string;
  label: string;
  a: number;
  b: number;
  max: number;
  fmt: (v: number) => string;
}

function rowsFor(tpl: ReelStats, hero: ReelStats): Row[] {
  const pct = (v: number) => `${Math.round(v)}%`;
  return [
    { key: "stop", label: "Stopped scrolling in 1.5 s", a: tpl.stopRate * 100, b: hero.stopRate * 100, max: 100, fmt: pct },
    { key: "end", label: "Watched to the end", a: tpl.completion * 100, b: hero.completion * 100, max: 100, fmt: pct },
    { key: "creative", label: "Judged creativity", a: tpl.creativity, b: hero.creativity, max: 10, fmt: (v) => `${v.toFixed(1)}/10` },
  ];
}

const ROW_AT = [3000, 4500, 6000];
const COUNT_MS = 1400;

export function SceneReels({ lab, story, fileUrl, t, playing }: SceneProps) {
  const tpl = story.template;
  const hero = story.hero && story.hero.id !== tpl?.id ? story.hero : (story.reels.find((r) => r.kind !== "baseline") ?? null);
  const tplReel = lab.reels.find((r) => r.id === tpl?.id);
  const heroReel = lab.reels.find((r) => r.id === hero?.id);
  const tplVideo = useSyncedVideo(t, playing);
  const heroVideo = useSyncedVideo(t, playing);
  if (!tpl || !hero || !tplReel || !heroReel) return null;

  const hook = (lab.concepts.find((c) => c.id === hero.concept)?.hook.text ?? hero.title).replace(/\*/g, "");
  const typedTo = Math.round(hook.length * phase(t, 1300, 1700, linear));
  const typing = t >= 1300 && t < 3600;
  const rows = rowsFor(tpl, hero);
  const sweep = rows.every((r) => r.b > r.a);
  const verdict = phase(t, 8200, 700);

  const head = phase(t, 0, 700);
  const left = phase(t, 200, 800);
  const right = phase(t, 450, 900);
  const arrow = phase(t, 1000, 500);
  const hookIn = phase(t, 1100, 500);
  const note = phase(t, 3000, 600);

  return (
    <div className="pz-reels">
      <figure className="pz-reels-phone" data-kind="template" style={{ opacity: left, transform: `translateX(${(1 - left) * -40}px)` }}>
        <figcaption className="pz-reels-label">
          <span className="pz-reels-tag">Template</span>
          <span className="pz-reels-label-main">Typical listing reel</span>
        </figcaption>
        <div className="pz-reels-screen">
          <video ref={tplVideo} src={fileUrl(tplReel.video)} poster={fileUrl(tplReel.poster)} muted playsInline loop preload="auto" />
        </div>
      </figure>

      <span className="pz-reels-arrow" style={{ opacity: arrow, transform: `translateX(${(1 - arrow) * -10}px)` }} aria-hidden>
        <ArrowRight size={22} />
      </span>

      <figure
        className="pz-reels-phone"
        data-kind="hero"
        style={{ opacity: right, transform: `translateY(${(1 - right) * 40}px) scale(${0.96 + 0.04 * right})`, "--ring": verdict } as CSSProperties}
      >
        <figcaption className="pz-reels-label">
          <span className="pz-reels-label-kicker">
            <Sparkles size={18} aria-hidden /> Your AI creative team's reel
          </span>
          <span className="pz-reels-label-title">{hero.title}</span>
        </figcaption>
        <div className="pz-reels-screen">
          <video ref={heroVideo} src={fileUrl(heroReel.video)} poster={fileUrl(heroReel.poster)} muted playsInline loop preload="auto" />
          <span className="pz-reels-ring" aria-hidden />
        </div>
      </figure>

      <div className="pz-reels-side">
        <header style={{ opacity: head, transform: `translateY(${(1 - head) * 18}px)` }}>
          <p className="pz-kicker">Before / after</p>
          <h2 className="pz-reels-title">
            <span>Same listing. Same photos.</span>
            <span className="pz-reels-title-dim">A completely different reel.</span>
          </h2>
        </header>

        <figure className="pz-reels-hook" style={{ opacity: hookIn }}>
          <figcaption>The hook the agents wrote</figcaption>
          <blockquote>
            <span className="pz-reels-q">“</span>
            {hook.slice(0, typedTo)}
            {typedTo < hook.length && <span className="pz-reels-caret" data-on={typing || undefined} />}
            <span className="pz-reels-untyped">{hook.slice(typedTo)}</span>
            {typedTo >= hook.length && <span className="pz-reels-q">”</span>}
          </blockquote>
        </figure>

        <ul className="pz-reels-rows">
          {rows.map((r, i) => {
            const at = ROW_AT[i]!;
            const show = phase(t, at, 500);
            const a = countUp(t, at + 150, COUNT_MS, r.a);
            const b = countUp(t, at + 150, COUNT_MS, r.b);
            const x = r.a > 0 ? r.b / r.a : null;
            return (
              <li key={r.key} style={{ opacity: show, transform: `translateY(${(1 - show) * 16}px)` }}>
                <p className="pz-reels-row-label">{r.label}</p>
                <p className="pz-reels-row-values">
                  <span className="pz-reels-a num">{r.fmt(a)}</span>
                  <ArrowRight size={22} className="pz-reels-row-arrow" aria-hidden />
                  <span className="pz-reels-b num">{r.fmt(b)}</span>
                  {x != null && x >= 1.05 && (
                    <span className="pz-reels-x num" style={{ opacity: phase(t, at + COUNT_MS, 400, easeOut) }}>
                      {x >= 9.95 ? Math.round(x) : x.toFixed(1)}×
                    </span>
                  )}
                </p>
                <span className="pz-reels-bars" aria-hidden>
                  <span className="pz-reels-bar-a" style={{ width: `${(a / r.max) * 100}%` }} />
                  <span className="pz-reels-bar-b" style={{ width: `${(b / r.max) * 100}%` }} />
                </span>
              </li>
            );
          })}
        </ul>

        <footer className="pz-reels-foot">
          {sweep && (
            <p className="pz-reels-verdict" style={{ opacity: verdict, transform: `translateY(${(1 - verdict) * 10}px)` }}>
              <Check size={22} aria-hidden /> Wins on all three, judged blind.
            </p>
          )}
          <p className="pz-note" style={{ opacity: note }}>
            Panel of {story.viewers} AI viewer agents and {story.judges} AI judges, shown both reels blind and unlabelled.
          </p>
        </footer>
      </div>
    </div>
  );
}
