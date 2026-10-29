import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { ASSETS, img } from "../content";
import { useEntrance, useReducedMotion } from "../lib/motion";
import { Replay } from "./Icons";

// One photo the editor fused from two camera positions, walked by the viewfinder, then split into the two photos
// ShootAgent rebuilds from the brackets. The markup's finished state is the split; each beat below adds one data
// attribute and CSS transitions carry the change, so a replay mid-way restarts from the joined photo.
// 2800 ms is the walk's transition in styles.css; the split starts as it settles.
const BEATS: [string, number][] = [
  ["walked", 250],
  ["split", 3150],
  ["read", 3700],
];

const L = ASSETS.laundry;
const groups = L.groups.map((g) => `${g[0]}–${g[g.length - 1]}`);
const matches = L.explanation.match(/median (\d+) matches within\); brackets [\d-]+ \(median (\d+) matches within\)\. Across positions at most (\d+)/);

export function Film() {
  const figure = useRef<HTMLElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const timers = useRef<number[]>([]);
  const reduced = useReducedMotion();
  const entrance = useEntrance(stage, { threshold: 0.45 });

  const clear = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };
  const finish = useCallback(() => {
    clear();
    const el = figure.current;
    if (el) BEATS.forEach(([name]) => (el.dataset[name] = ""));
  }, []);
  const reset = useCallback(() => {
    clear();
    const el = figure.current;
    if (!el) return;
    el.dataset.instant = "";
    BEATS.forEach(([name]) => delete el.dataset[name]);
    el.getBoundingClientRect(); // commit the joined photo before transitions come back
    delete el.dataset.instant;
  }, []);
  const play = useCallback(() => {
    reset();
    for (const [name, at] of BEATS) timers.current.push(window.setTimeout(() => figure.current && (figure.current.dataset[name] = ""), at));
  }, [reset]);

  useLayoutEffect(finish, [finish]);
  useEffect(() => {
    if (entrance === "waiting") reset();
    else if (entrance === "playing") play();
  }, [entrance, play, reset]);
  useEffect(() => {
    if (reduced) finish();
  }, [reduced, finish]);
  useEffect(() => clear, []);

  return (
    <section className="section" id="how" aria-labelledby="film-title">
      <div className="wrap">
        <div className="section-head">
          <h2 id="film-title">When the editor merges two rooms.</h2>
          <p className="lede">
            AutoHDR blended ten exposures into this laundry photo, but they came from two camera positions. ShootAgent
            matches features between every pair of brackets, finds the two groups, and fuses each into its own photo.
          </p>
        </div>
        <figure className="film" ref={figure}>
          <div className="film-stage" ref={stage}>
            <button className="control film-replay" onClick={() => (reduced ? finish() : play())}>
              <Replay /> Replay
            </button>
            <div className="film-whole" aria-hidden>
              <img className="layer" src={img("laundry-delivered.webp")} alt="" loading="lazy" decoding="async" />
              <img className="layer lens-view" src={img("laundry-a-band.webp")} alt="" loading="lazy" decoding="async" />
              <div className="lens">
                <i />
              </div>
              <span className="film-lens-tag">Brackets {groups[0]} only</span>
            </div>
            <div className="film-panels">
              <div className="panel" data-panel="a">
                <img loading="lazy" decoding="async" src={img("laundry-a.webp")} alt="The first photo, fused from brackets 0 to 4: the wall with the entry door." />
                <span className="panel-chip">Brackets {groups[0]}</span>
              </div>
              <div className="panel" data-panel="b">
                <img loading="lazy" decoding="async" src={img("laundry-b.webp")} alt="The second photo, fused from brackets 5 to 9: the corner with the sink." />
                <span className="panel-chip">Brackets {groups[1]}</span>
              </div>
            </div>
          </div>
          <figcaption className="readings">
            <p className="reading" data-reading="1">
              <b>Found</b>
              Ten brackets, two camera positions.
              {matches ? ` Within each group ${matches[1]} and ${matches[2]} features match; across them, at most ${matches[3]}.` : ""}
            </p>
            <p className="reading" data-reading="2">
              <b>Photo one</b>
              Brackets {groups[0]}, fused on their own: the wall with the entry door.
            </p>
            <p className="reading" data-reading="3">
              <b>Photo two</b>
              Brackets {groups[1]}: the corner with the sink, styled to match the editor’s look.
            </p>
          </figcaption>
        </figure>
        <p className="film-note">
          The viewfinder shows what one group of brackets saw: the cabinets from the other camera position vanish inside it.
          Splitting merged views is ShootAgent’s strongest result, 21 of 21 in AutoHDR’s defect set.
        </p>
      </div>
    </section>
  );
}
