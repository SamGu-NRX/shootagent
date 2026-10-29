import { useCallback, useEffect, useRef, useState } from "react";
import { ASSETS, img, srcSet } from "../content";
import { useEntrance, useReducedMotion } from "../lib/motion";
import { Replay } from "./Icons";

// One photo the editor fused from two camera positions, walked by the viewfinder, then split into the two photos
// ShootAgent rebuilds from the brackets. The markup's finished state is the split (the data attributes are in the
// JSX, so the prerendered page shows it before any script runs); each beat below adds one data attribute back and
// CSS transitions carry the change, so a replay mid-way restarts from the joined photo.
// 2800 ms is the walk's transition in styles.css; the split starts as it settles.
const BEATS: [string, number][] = [
  ["walked", 250],
  ["split", 3150],
  ["read", 3700],
];

const L = ASSETS.laundry;
// The film runs the content's width: the page's on a phone, at most 1360 px.
const FILM_SIZES = "(max-width: 1360px) 100vw, 1360px";
const groups = L.groups.map((g) => `${g[0]}–${g[g.length - 1]}`);
const matches = L.explanation.match(/median (\d+) matches within\); brackets [\d-]+ \(median (\d+) matches within\)\. Across positions at most (\d+)/);

export function Film() {
  const figure = useRef<HTMLElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const timers = useRef<number[]>([]);
  const reduced = useReducedMotion();
  // The stacked figure is taller than a phone's screen at large text sizes, so it starts once its top is well in
  // view rather than once a share of it is.
  const entrance = useEntrance(stage, { threshold: 0, rootMargin: "0px 0px -30% 0px" });
  // Only without motion: whether the reader has switched back to the editor's photo.
  const [joined, setJoined] = useState(false);

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

  useEffect(() => {
    if (entrance === "waiting") reset();
    else if (entrance === "playing") play();
  }, [entrance, play, reset]);
  useEffect(() => {
    if (reduced) finish();
    setJoined(false);
  }, [reduced, finish]);
  useEffect(() => clear, []);

  // With motion, Replay plays the walk and the split again. Without it, the same button switches between the
  // editor's photo and the split, so both stay reachable.
  const control = reduced
    ? joined
      ? { label: "Split it", name: "Split it into its two photos", act: () => (finish(), setJoined(false)) }
      : { label: "See the editor’s photo", name: "See the editor’s photo", act: () => (reset(), setJoined(true)) }
    : { label: "Replay", name: "Replay the split", act: play };

  return (
    <section className="section" id="how" aria-labelledby="film-title">
      <div className="wrap">
        <div className="section-head">
          <h2 id="film-title">When the editor merges two views.</h2>
          <p className="lede">
            AutoHDR blended ten brackets into this kitchen photo, but they came from two camera positions. ShootAgent
            matches features between every pair of brackets, finds the two groups, and fuses each into its own photo.
          </p>
        </div>
        <figure className="film" ref={figure} data-walked="" data-split="" data-read="">
          <div className="film-stage" ref={stage}>
            <button className="control film-replay" onClick={control.act} aria-label={control.name}>
              <Replay /> {control.label}
            </button>
            <div
              className="film-whole"
              role="img"
              aria-label="The editor’s photo: ten brackets from two camera positions blended into one kitchen."
            >
              <img className="layer" src={img("laundry-delivered.webp")} srcSet={srcSet("laundry-delivered.webp")} sizes={FILM_SIZES} alt="" loading="lazy" decoding="async" />
              <img className="layer lens-view" src={img("laundry-a-band.webp")} srcSet={srcSet("laundry-a-band.webp")} sizes={FILM_SIZES} alt="" loading="lazy" decoding="async" />
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
                <img loading="lazy" decoding="async" src={img("laundry-b.webp")} alt="The second photo, fused from brackets 5 to 9: the kitchen corner with the sink." />
                <span className="panel-chip">Brackets {groups[1]}</span>
              </div>
            </div>
          </div>
          <figcaption className="readings">
            <p className="reading" data-reading="1">
              <b>Found</b>
              Ten brackets, two camera positions.
              {matches
                ? ` Within each group a median of ${matches[1]} and ${matches[2]} features match; across them, at most ${matches[3]}.`
                : ""}
            </p>
            <p className="reading" data-reading="2">
              <b>Photo one</b>
              Brackets {groups[0]}: the wall with the entry door.
            </p>
            <p className="reading" data-reading="3">
              <b>Photo two</b>
              Brackets {groups[1]}: the kitchen corner with the sink. Both are toned to match the editor’s photo.
            </p>
          </figcaption>
        </figure>
        <p className="film-note">
          The viewfinder shows what one group of brackets saw: the cabinets from the other camera position vanish inside it.
          Ghosting and grouping is ShootAgent’s strongest result: 21 of 21 fixed in AutoHDR’s defect set.
        </p>
      </div>
    </section>
  );
}
