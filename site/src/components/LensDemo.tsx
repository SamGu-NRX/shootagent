import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type AnimationEvent,
  type ChangeEvent,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
} from "react";
import { Replay } from "./Icons";
import { ASSETS, img } from "../content";
import { useReducedMotion } from "../lib/motion";
import "./LensDemo.css";

// Try the viewfinder: the visitor works the agent's viewfinder. The editor's photo fills the stage; the viewfinder
// is a window onto what the camera captured (the fused brackets), aligned pixel for pixel. Over the stop sign that
// the editor's camera-removal step erased, the finding is flagged in place, and the fix, a restore from the
// brackets, lands through its mask.
//
// The position is two shares of the photo, --vf-x and --vf-y on the stage, and they feed only transforms: the
// window moves one way and the photo inside it the other, so a drag is composited, with no layout or paint. React
// renders the discrete states (idle, found, restored); the position is written straight to the stage.

type Phase = "idle" | "found" | "restored";
type Pos = { x: number; y: number };

const S = ASSETS.street;
// What QC found (the candidate's box); the restore works through a grown mask around it.
const [SX0, SY0, SX1, SY1] = S.candidate as [number, number, number, number];
const CONFIDENCE = S.confidence.toFixed(2);

// A 3:4 viewfinder, 28% of the photo's width. It starts over the houses on the right, away from the sign.
const LW = 0.28;
const LH = ((4 / 3) * LW * S.size.w) / S.size.h;
const MAX: Pos = { x: 1 - LW, y: 1 - LH };
const START: Pos = { x: 0.56, y: 0.2 };
const STEP = 0.02;
const BIG_STEP = 0.1;
const GLIDE_MS = 480; // .vf-stage[data-glide] in lens.css
const TAG_FLIP = 0.1; // above this share from the top, "As captured" fits over the viewfinder

const STAGE_VARS = {
  "--vf-w": String(LW),
  "--vf-h": LH.toFixed(4),
  "--vf-x": String(START.x),
  "--vf-y": String(START.y),
} as CSSProperties;

const clamp = (p: Pos): Pos => ({ x: Math.min(Math.max(p.x, 0), MAX.x), y: Math.min(Math.max(p.y, 0), MAX.y) });

/** True once most of the sign's box is inside the viewfinder. */
function covers(p: Pos): boolean {
  const w = Math.max(0, Math.min(p.x + LW, SX1) - Math.max(p.x, SX0));
  const h = Math.max(0, Math.min(p.y + LH, SY1) - Math.max(p.y, SY0));
  return (w * h) / ((SX1 - SX0) * (SY1 - SY0)) >= 0.6;
}

// The hero's placement: the sign's box with a little room, and the tag beside it.
const pct = (v: number) => `${(v * 100).toFixed(2)}%`;
const PAD = 0.012;
const SIGN_BOX: CSSProperties = {
  left: pct(SX0 - PAD),
  top: pct(SY0 - PAD * 1.5),
  width: pct(SX1 - SX0 + 2 * PAD),
  height: pct(SY1 - SY0 + 3 * PAD),
  "--anim": "pop-in",
  "--dur": "420ms",
} as CSSProperties;
const TAG_SPOT: CSSProperties = { left: pct(SX1 + PAD + 0.008), top: pct(SY0 - PAD) };

/** Where the viewfinder is and what it shows, for the control's value text. */
function describe(p: Pos, phase: Phase): string {
  const cx = p.x + LW / 2;
  const cy = p.y + LH / 2;
  const h = cx < 0.4 ? "left" : cx > 0.6 ? "right" : "centre";
  const v = cy < 0.42 ? "Upper" : cy > 0.58 ? "Lower" : "";
  const where = v
    ? `${v} ${h} of the photo`
    : h === "centre"
      ? "Centre of the photo"
      : `${h === "left" ? "Left" : "Right"} side of the photo`;
  if (!covers(p)) return `${where}. Inside: what the camera captured here; nothing is missing.`;
  return phase === "restored"
    ? `${where}. Inside: the stop sign the camera captured, which the restored photo now shows too.`
    : `${where}. Inside: a stop sign the camera captured, which the editor’s photo has lost.`;
}

const MoveIcon = () => (
  <svg width="1.15em" height="1.15em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3" />
  </svg>
);

export function LensDemo() {
  const reduced = useReducedMotion();
  const hintId = useId();
  const stage = useRef<HTMLDivElement>(null);
  const lens = useRef<HTMLDivElement>(null);
  const control = useRef<HTMLInputElement>(null);
  const resetButton = useRef<HTMLButtonElement>(null);
  const [phase, setPhaseState] = useState<Phase>("idle");
  const [moved, setMoved] = useState(false);
  // The position as last settled (drag end, key, glide end): the control's value and value text.
  const [at, setAt] = useState<Pos>(START);
  const phaseNow = useRef<Phase>("idle");
  const pos = useRef<Pos>(START);
  const drag = useRef<{ id: number; rect: DOMRect; dx: number; dy: number } | null>(null);
  const tap = useRef<{ id: number; x: number; y: number } | null>(null);
  const pending = useRef<Pos | null>(null);
  const frame = useRef(0);
  const glide = useRef(0);
  const touched = useRef(false);
  const nudged = useRef(false);
  const focusNext = useRef<"reset" | "control" | null>(null);

  const setPhase = useCallback((p: Phase) => {
    phaseNow.current = p;
    setPhaseState(p);
  }, []);

  const place = useCallback((p: Pos) => {
    pos.current = p;
    const el = stage.current;
    if (!el) return;
    el.style.setProperty("--vf-x", p.x.toFixed(4));
    el.style.setProperty("--vf-y", p.y.toFixed(4));
    lens.current?.toggleAttribute("data-top", p.y < TAG_FLIP);
  }, []);

  // The finding is flagged the first time the viewfinder covers the sign, and stays until a reset.
  const look = useCallback(
    (p: Pos) => {
      if (phaseNow.current === "idle" && covers(p)) setPhase("found");
    },
    [setPhase],
  );

  const touch = useCallback(() => {
    if (touched.current) return;
    touched.current = true;
    setMoved(true);
  }, []);

  // Stops the nudge or a glide where it is, so taking hold of the viewfinder never makes it jump.
  const halt = useCallback(() => {
    const el = stage.current;
    if (!el || !("nudge" in el.dataset || "glide" in el.dataset)) return;
    const style = getComputedStyle(el);
    const x = parseFloat(style.getPropertyValue("--vf-x")) + (parseFloat(style.getPropertyValue("--vf-dx")) || 0);
    const y = parseFloat(style.getPropertyValue("--vf-y"));
    delete el.dataset.nudge;
    delete el.dataset.glide;
    window.clearTimeout(glide.current);
    if (Number.isFinite(x) && Number.isFinite(y)) place(clamp({ x, y }));
  }, [place]);

  const glideTo = useCallback(
    (p: Pos, then: () => void) => {
      const el = stage.current;
      if (!el) return;
      halt();
      if (!reduced) el.dataset.glide = "";
      place(p);
      const done = () => {
        delete el.dataset.glide;
        then();
      };
      if (reduced) done();
      else glide.current = window.setTimeout(done, GLIDE_MS);
    },
    [halt, place, reduced],
  );

  // Pointer: any pointer drags the viewfinder itself. A mouse can also press anywhere on the photo and drag from
  // there; a finger taps the photo to send the viewfinder there, and a finger dragging the photo scrolls the page.
  const flush = () => {
    frame.current = 0;
    const p = pending.current;
    pending.current = null;
    if (!p) return;
    place(p);
    look(p);
    touch();
  };
  const centreOn = (e: { clientX: number; clientY: number }, rect: DOMRect) =>
    clamp({ x: (e.clientX - rect.left) / rect.width - LW / 2, y: (e.clientY - rect.top) / rect.height - LH / 2 });
  const grab = (e: PointerEvent, from: Pos, rect: DOMRect) => {
    drag.current = {
      id: e.pointerId,
      rect,
      dx: (e.clientX - rect.left) / rect.width - from.x,
      dy: (e.clientY - rect.top) / rect.height - from.y,
    };
    lens.current?.setPointerCapture(e.pointerId);
    stage.current?.setAttribute("data-dragging", "");
    // A mouse leaves the keys ready for fine moves. The focus ring waits for the first key: focus given during a
    // pointerdown that cancels its default counts as keyboard focus in Chromium.
    const input = control.current;
    if (e.pointerType === "mouse" && input) {
      input.dataset.quiet = "";
      input.focus({ preventScroll: true });
    }
  };

  const onLensDown = (e: PointerEvent<HTMLDivElement>) => {
    const el = stage.current;
    if (!el || (e.pointerType === "mouse" && e.button !== 0)) return;
    e.preventDefault(); // no text selection or image drag
    e.stopPropagation();
    halt();
    grab(e, pos.current, el.getBoundingClientRect());
  };
  const onStageDown = (e: PointerEvent<HTMLDivElement>) => {
    const el = stage.current;
    if (!el) return;
    if (e.pointerType !== "mouse") {
      tap.current = { id: e.pointerId, x: e.clientX, y: e.clientY };
      return;
    }
    if (e.button !== 0) return;
    e.preventDefault();
    halt();
    const rect = el.getBoundingClientRect();
    const p = centreOn(e, rect);
    place(p);
    look(p);
    touch();
    grab(e, p, rect);
  };
  const onStageUp = (e: PointerEvent<HTMLDivElement>) => {
    const t = tap.current;
    tap.current = null;
    const el = stage.current;
    // A tap, not the end of a scroll (that sends pointercancel) or a drag along the photo.
    if (!el || !t || t.id !== e.pointerId || Math.hypot(e.clientX - t.x, e.clientY - t.y) > 12) return;
    const p = centreOn(e, el.getBoundingClientRect());
    touch();
    glideTo(p, () => {
      look(p);
      setAt(p);
    });
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    pending.current = clamp({
      x: (e.clientX - d.rect.left) / d.rect.width - d.dx,
      y: (e.clientY - d.rect.top) / d.rect.height - d.dy,
    });
    if (!frame.current) frame.current = requestAnimationFrame(flush);
  };
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    cancelAnimationFrame(frame.current);
    flush();
    stage.current?.removeAttribute("data-dragging");
    setAt(pos.current);
  };

  // Keys: arrows move it, Shift moves further; Home and End go to the sides, Page Up and Page Down to the top and
  // bottom. Assistive technology that sets the slider's value moves it across.
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    delete e.currentTarget.dataset.quiet;
    const keys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"];
    if (!keys.includes(e.key) || e.altKey || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    halt();
    const { x, y } = pos.current;
    const step = e.shiftKey ? BIG_STEP : STEP;
    const next = clamp(
      e.key === "ArrowLeft" ? { x: x - step, y }
      : e.key === "ArrowRight" ? { x: x + step, y }
      : e.key === "ArrowUp" ? { x, y: y - step }
      : e.key === "ArrowDown" ? { x, y: y + step }
      : e.key === "Home" ? { x: 0, y }
      : e.key === "End" ? { x: MAX.x, y }
      : e.key === "PageUp" ? { x, y: 0 }
      : { x, y: MAX.y },
    );
    place(next);
    look(next);
    touch();
    setAt(next);
  };
  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    halt();
    const next = clamp({ x: (Number(e.target.value) / 100) * MAX.x, y: pos.current.y });
    place(next);
    look(next);
    touch();
    setAt(next);
  };

  const restore = (e: MouseEvent<HTMLButtonElement>) => {
    if (phaseNow.current !== "found") return;
    if (document.activeElement === e.currentTarget) focusNext.current = "reset";
    // The viewfinder steps aside so the restore lands in view.
    const p = pos.current;
    if (p.x < SX1 + PAD && p.x + LW > SX0 - PAD) {
      const q = clamp({ x: SX1 + 0.05, y: p.y });
      glideTo(q, () => setAt(q));
    }
    setPhase("restored");
  };
  const reset = (e: MouseEvent<HTMLButtonElement>) => {
    if (e.detail > 1) return; // the rest of a double click on Restore, which this button replaced
    if (document.activeElement === e.currentTarget) focusNext.current = "control";
    setPhase("idle");
    touched.current = false;
    setMoved(false);
    glideTo(START, () => setAt(START));
  };
  useEffect(() => {
    const next = focusNext.current;
    focusNext.current = null;
    if (next === "reset") resetButton.current?.focus();
    else if (next === "control") control.current?.focus();
  }, [phase]);

  // A one-time nudge the first time most of the photo is in view, unless the visitor got there first.
  useEffect(() => {
    const el = stage.current;
    if (!el || reduced || nudged.current) return;
    let timer = 0;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting && entry.intersectionRatio >= 0.6) {
          timer ||= window.setTimeout(() => {
            observer.disconnect();
            if (touched.current || nudged.current) return;
            nudged.current = true;
            el.dataset.nudge = "";
          }, 600);
        } else {
          window.clearTimeout(timer);
          timer = 0;
        }
      },
      { threshold: [0, 0.6, 1] },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      window.clearTimeout(timer);
    };
  }, [reduced]);
  const onAnimationEnd = (e: AnimationEvent<HTMLDivElement>) => {
    if (e.animationName === "vf-nudge" && e.target === e.currentTarget) delete e.currentTarget.dataset.nudge;
  };

  useEffect(
    () => () => {
      cancelAnimationFrame(frame.current);
      window.clearTimeout(glide.current);
    },
    [],
  );

  return (
    <section className="section vf-section" aria-labelledby="vf-title">
      <div className="wrap vf-layout">
        <div className="section-head vf-head">
          <h2 id="vf-title">Try the viewfinder.</h2>
          <p className="lede">
            This is the editor’s photo. Inside the viewfinder is what the camera’s brackets captured at the same spot,
            the comparison ShootAgent makes on every photo. One thing the camera saw is missing. Find it.
          </p>
          <p className="vf-note">
            Real output from ShootAgent’s tools on AutoHDR’s <code>defect_train_0016</code>.
          </p>
        </div>

        <figure className="vf" data-phase={phase} data-moved={moved || undefined}>
          <div
            className="vf-stage"
            ref={stage}
            style={STAGE_VARS}
            onPointerDown={onStageDown}
            onPointerUp={onStageUp}
            onPointerCancel={() => (tap.current = null)}
            onAnimationEnd={onAnimationEnd}
          >
            <img
              className="layer"
              src={img("street-delivered.webp")}
              alt="The editor’s photo: a street of new houses behind a young tree, with a lawn in front."
              draggable={false}
              loading="lazy"
              decoding="async"
            />
            <img className="layer restored vf-restored" src={img("street-fixed.webp")} alt="" draggable={false} loading="lazy" decoding="async" />
            <div className="layer vf-flash" aria-hidden />

            <div
              className="vf-lens"
              ref={lens}
              onPointerDown={onLensDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onPointerCancel={onUp}
              onLostPointerCapture={onUp}
            >
              <input
                ref={control}
                className="vf-input"
                type="range"
                min={0}
                max={100}
                step={5}
                value={Math.round((at.x / MAX.x) * 20) * 5}
                onChange={onChange}
                onKeyDown={onKey}
                onBlur={(e) => delete e.currentTarget.dataset.quiet}
                aria-label="Viewfinder"
                aria-valuetext={describe(at, phase)}
                aria-describedby={hintId}
              />
              <div className="vf-window" aria-hidden>
                <img src={img("street-captured.webp")} alt="" draggable={false} loading="lazy" decoding="async" />
              </div>
              <div className="lens" aria-hidden>
                <i />
                <span className="lens-tag">As captured</span>
              </div>
              <span className="vf-hint" aria-hidden>
                <MoveIcon />
                <span className="vf-hint-drag">Drag</span>
                <span className="vf-hint-keys">Arrow keys</span>
              </span>
            </div>

            {phase !== "idle" && (
              <>
                <div className="finding beat vf-finding" style={SIGN_BOX}>
                  <i />
                </div>
                {phase === "found" ? (
                  <span key="found" className="tag beat vf-tag" style={TAG_SPOT}>
                    <b>Missing:</b> stop sign{" "}
                    <span className="vf-conf">
                      · <span className="vf-conf-word">confidence </span>
                      {CONFIDENCE}
                    </span>
                  </span>
                ) : (
                  <span key="restored" className="tag beat vf-tag" style={{ ...TAG_SPOT, "--tone": "var(--pass)", "--at": "700ms" } as CSSProperties}>
                    <b>Restored</b> from the brackets
                  </span>
                )}
              </>
            )}
          </div>

          <div className="vf-bar">
            <p className="vf-status" aria-live="polite" data-tone={phase}>
              <span className="vf-mark" aria-hidden>
                {phase === "idle" ? "›" : phase === "found" ? "!" : "✓"}
              </span>
              {phase === "idle" && (
                <span>
                  <b>Find what the editor erased.</b> Drag the viewfinder or <span className="vf-coarse">tap</span>
                  <span className="vf-fine">click</span> the photo<span className="vf-fine">; arrow keys move it too</span>.
                </span>
              )}
              {phase === "found" && (
                <span>
                  <b>Missing: stop sign.</b> Every bracket shows it; the editor’s photo doesn’t. Confidence {CONFIDENCE}.
                </span>
              )}
              {phase === "restored" && (
                <span>
                  <b>Restored from the brackets.</b> <span className="vf-check">Outside the mask: {S.outside} / 255</span>,
                  so nothing else in the photo changed.
                </span>
              )}
            </p>
            {phase === "found" && (
              <button className="vf-restore beat" style={{ "--anim": "rise-in", "--dur": "360ms" } as CSSProperties} onClick={restore}>
                Restore from the brackets
              </button>
            )}
            {phase === "restored" && (
              <button ref={resetButton} className="control vf-reset" onClick={reset}>
                <Replay /> Reset
              </button>
            )}
          </div>
          <span id={hintId} hidden>
            Shows what the camera captured under it. Arrow keys move it; with Shift, further.
          </span>
        </figure>
      </div>
    </section>
  );
}
