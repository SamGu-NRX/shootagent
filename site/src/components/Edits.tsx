// Customer requests, before and after: the seven saved requests from AutoHDR's editor dataset that README.md's chat
// pass reports, as ShootAgent applied them. Pick a request (tabs; arrow keys move between them), then wipe between
// the photo as delivered and as edited: drag anywhere on the photo, or focus the divider (a slider) and use the arrow
// keys, Page Up / Page Down, Home and End. "Show what changed" dims everything the edit left alone.
//
// Photos, masks, outlines and numbers come from site/scripts/prepare_edits.py (src/edits.json; the images in public/img/edits/). The
// markup is the finished state: each photo opens with the divider across the middle of its edit. Below the screen,
// the first photo waits with the divider at the right edge (before only) and sweeps open once, the first time it is
// in view; reduced motion keeps it finished. On touch, a vertical swipe on the photo scrolls the page; only a
// sideways drag moves the divider (touch-action: pan-y), and a tap places it.
import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import EDITS from "../edits.json";
import { Check } from "./Icons";
import { useEntrance } from "../lib/motion";
import "./Edits.css";

type Pair = (typeof EDITS.pairs)[number];
const PAIRS: Pair[] = EDITS.pairs;
const src = (name: string) => `/img/edits/${name}`;
const clamp = (v: number) => Math.min(100, Math.max(0, v));
const valueText = (v: number) => (v <= 0 ? "After only" : v >= 100 ? "Before only" : `${v}% before, ${100 - v}% after`);

// Arrow keys move the divider 2%; Shift + arrows and Page Up / Page Down move it 10%.
const STEP = 2;
const BIG_STEP = 10;
const LABEL_ROOM = 84; // px: a corner label fades out once its side of the divider is narrower than this
const SWEEP_MS = 1700; // the entrance's transition in edits.css (200 ms delay + 1300 ms), plus a margin

const Chevrons = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="m9 6-6 6 6 6M15 6l6 6-6 6" />
  </svg>
);
const Highlight = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden>
    <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" />
    <path d="M9 12.5 11 14.5 15.5 9.5" />
  </svg>
);

interface Drag {
  id: number;
  x0: number;
  y0: number;
  from: number;
  onKnob: boolean;
  touch: boolean;
  moving: boolean;
}

export function Edits() {
  const uid = useId();
  const tabs = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const photo = useRef<HTMLDivElement>(null);
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const wanted = useRef(0);
  const ready = useRef(new Map<number, Promise<void>>());

  const [index, setIndex] = useState(0); // the selected tab
  const [shown, setShown] = useState(0); // the pair on screen: follows `index` once its photos are decoded
  const [split, setSplit] = useState(PAIRS[0]!.split * 100);
  const [changed, setChanged] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [sweep, setSweep] = useState<"waiting" | "playing" | null>(null);
  const [width, setWidth] = useState(0); // the photo's width on screen, for the corner labels
  const entrance = useEntrance(frame, { threshold: 0.5 });

  useEffect(() => {
    const el = photo.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? 0));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (entrance === "waiting") setSweep("waiting");
    else if (entrance === "playing") {
      setSweep("playing");
      const t = window.setTimeout(() => setSweep(null), SWEEP_MS);
      return () => window.clearTimeout(t);
    } else setSweep(null);
  }, [entrance]);

  const preload = (i: number) => {
    let p = ready.current.get(i);
    if (!p) {
      const pair = PAIRS[i]!;
      p = Promise.all(
        [pair.before, pair.after, pair.mask].map((name) => {
          const im = new Image();
          im.src = src(name);
          return im.decode().catch(() => undefined);
        }),
      ).then(() => undefined);
      ready.current.set(i, p);
    }
    return p;
  };

  // The tab changes at once; the photos swap together once both are decoded, so before and after never mismatch.
  const select = (i: number) => {
    setIndex(i);
    wanted.current = i;
    void preload(i).then(() => {
      if (wanted.current !== i) return;
      setShown(i);
      setSplit(PAIRS[i]!.split * 100);
    });
  };

  // Tabs: one stop in the tab order; arrow keys, Home and End move the selection (it follows focus).
  const onTabsKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const last = PAIRS.length - 1;
    const to = ({ ArrowRight: index + 1, ArrowDown: index + 1, ArrowLeft: index - 1, ArrowUp: index - 1, Home: 0, End: last } as Record<string, number>)[e.key];
    if (to === undefined) return;
    e.preventDefault();
    const i = (to + PAIRS.length) % PAIRS.length;
    select(i);
    tabs.current?.querySelectorAll<HTMLButtonElement>("[role=tab]")[i]?.focus();
  };

  const at = (clientX: number) => {
    const r = photo.current!.getBoundingClientRect();
    return clamp(((clientX - r.left) / r.width) * 100);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const onKnob = !!(e.target as Element).closest(".cmp-handle");
    const touch = e.pointerType !== "mouse";
    drag.current = { id: e.pointerId, x0: e.clientX, y0: e.clientY, from: split, onKnob, touch, moving: !touch };
    if (touch) return; // wait: a vertical swipe belongs to the page
    e.currentTarget.setPointerCapture(e.pointerId);
    setSweep(null);
    setDragging(true);
    if (!onKnob) setSplit(at(e.clientX));
    handle.current?.focus({ preventScroll: true });
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    if (!d.moving) {
      const dx = Math.abs(e.clientX - d.x0);
      if (dx < 6 || dx < Math.abs(e.clientY - d.y0)) return;
      d.moving = true;
      e.currentTarget.setPointerCapture(e.pointerId);
      setSweep(null);
      setDragging(true);
    }
    const span = photo.current!.getBoundingClientRect().width;
    // From the knob it moves by the drag, so grabbing it off-centre does not make it jump.
    setSplit(d.onKnob ? clamp(d.from + ((e.clientX - d.x0) / span) * 100) : at(e.clientX));
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    if (d.touch && !d.moving && !d.onKnob) {
      setSweep(null);
      setSplit(at(e.clientX)); // a tap places the divider
    }
    drag.current = null;
    setDragging(false);
  };
  // The browser took the gesture (a vertical scroll), or the photo lost its capture: leave the divider where it was.
  // (Taking capture for the photo makes the element under a finger lose its implicit capture; that bubbles here too,
  // so only the photo's own loss counts.)
  const onPointerCancel = () => {
    drag.current = null;
    setDragging(false);
  };

  const onSliderKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const v = Math.round(split);
    const step = e.shiftKey ? BIG_STEP : STEP;
    const to = (
      { ArrowLeft: v - step, ArrowDown: v - step, ArrowRight: v + step, ArrowUp: v + step, PageDown: v - BIG_STEP, PageUp: v + BIG_STEP, Home: 0, End: 100 } as Record<
        string,
        number
      >
    )[e.key];
    if (to === undefined) return;
    e.preventDefault();
    setSweep(null);
    setSplit(clamp(to));
  };

  const pair = PAIRS[shown]!;
  const { w, h } = pair.size;
  const value = Math.round(split);
  const style = { "--ar": w / h, "--split": `${split.toFixed(2)}%`, "--knob": `${Math.round(pair.knob * 100)}%`, "--mask": `url(${src(pair.mask)})` } as CSSProperties;
  const narrow = !width ? undefined : (split / 100) * width < LABEL_ROOM ? "before" : (1 - split / 100) * width < LABEL_ROOM ? "after" : undefined;

  return (
    <section className="section" id="requests" aria-labelledby="requests-title">
      <div className="wrap">
        <div className="section-head">
          <h2 id="requests-title">What the customer asked for, and nothing else.</h2>
          <p className="lede">
            Seven saved requests from AutoHDR’s editor dataset, sent to ShootAgent’s chat as the customers wrote them.
            All seven were applied, and outside each edit the photo is byte-identical to the one delivered.
          </p>
        </div>

        <div className="edits">
          <div className="edits-tabs" role="tablist" aria-label="Customer requests" ref={tabs} onKeyDown={onTabsKey}>
            {PAIRS.map((p, i) => (
              <button
                key={p.id}
                id={`${uid}-tab-${p.id}`}
                className="edits-tab"
                role="tab"
                aria-selected={i === index}
                aria-controls={`${uid}-panel`}
                tabIndex={i === index ? 0 : -1}
                onClick={() => select(i)}
                onPointerEnter={() => void preload(i)}
                onFocus={() => void preload(i)}
              >
                <span className="edits-tab-meta">
                  #{p.request} · {p.kind}
                </span>
                <span className="edits-tab-text">“{p.text}”</span>
              </button>
            ))}
          </div>

          <div className="edits-panel" role="tabpanel" id={`${uid}-panel`} aria-labelledby={`${uid}-tab-${PAIRS[index]!.id}`}>
            <div className="cmp-frame" ref={frame}>
              <div
                className="cmp-photo"
                ref={photo}
                style={style}
                data-changed={changed || undefined}
                data-dragging={dragging || undefined}
                data-entrance={sweep ?? undefined}
                data-narrow={narrow}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerCancel}
                onLostPointerCapture={(e) => e.target === e.currentTarget && onPointerCancel()}
                onMouseDown={(e) => e.preventDefault()}
              >
                <div className="cmp-layer" data-side="before">
                  <img src={src(pair.before)} alt={`Before: ${pair.alt.before}`} width={w} height={h} loading="lazy" decoding="async" draggable={false} />
                  <span className="cmp-label" aria-hidden>
                    Before
                  </span>
                </div>
                <div className="cmp-layer" data-side="after">
                  <img src={src(pair.after)} alt={`After: ${pair.alt.after}`} width={w} height={h} loading="lazy" decoding="async" draggable={false} />
                  <span className="cmp-label" aria-hidden>
                    After
                  </span>
                </div>
                <div className="cmp-dim" aria-hidden />
                <svg className="cmp-outline" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
                  <path d={pair.outline} />
                </svg>
                <div className="cmp-line" aria-hidden />
                <div
                  className="cmp-handle"
                  ref={handle}
                  role="slider"
                  tabIndex={0}
                  aria-label="Before and after divider"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={value}
                  aria-valuetext={valueText(value)}
                  onKeyDown={onSliderKey}
                >
                  <span className="cmp-knob">
                    <Chevrons />
                  </span>
                </div>
              </div>
            </div>
            <div className="cmp-foot">
              <dl className="cmp-result">
                <div>
                  <dt>Result</dt>
                  <dd data-tone="pass">
                    <Check size={15} /> Applied
                  </dd>
                </div>
                <div>
                  <dt>Attempts</dt>
                  <dd data-tone={pair.attempts > 1 ? "retry" : undefined}>{pair.attempts}</dd>
                </div>
                <div>
                  <dt>Time</dt>
                  <dd>{pair.seconds} s</dd>
                </div>
                <div>
                  <dt>Cost</dt>
                  <dd>${pair.cost.toFixed(2)}</dd>
                </div>
                <div>
                  <dt>Outside the edit</dt>
                  <dd>Byte-identical</dd>
                </div>
              </dl>
              <button className="control cmp-toggle" aria-pressed={changed} onClick={() => setChanged((c) => !c)}>
                <Highlight /> Show what changed
              </button>
            </div>
            {pair.why && <p className="cmp-why">{pair.why}</p>}
          </div>
        </div>
        <p className="edits-note">
          One run per request, timed and costed as in the README. The byte-for-byte check ran on the full-size photos;
          these previews are cut from the README’s composites and recompressed.
        </p>
      </div>
    </section>
  );
}
