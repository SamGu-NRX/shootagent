import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ASSETS, img, zoomTo } from "../content";
import { useOnScreen, usePageVisible, useReducedMotion } from "../lib/motion";
import { Mark } from "./Brand";
import { Check, Pause, Play, Replay } from "./Icons";
import { DemoButton } from "./Nav";

type SceneId = "check" | "fix" | "change" | "deliver";

const STEPS: { id: SceneId; label: string; ms: number; alt: string }[] = [
  {
    id: "check",
    label: "Check every photo against its brackets",
    ms: 6400,
    alt: "The agent's viewfinder crosses the editor's photo of a street and stops where a stop sign should be: the camera's brackets show the sign, the edited photo has lost it.",
  },
  {
    id: "fix",
    label: "Fix what the editor got wrong",
    ms: 5000,
    alt: "The stop sign is restored from the brackets. The self-check confirms nothing outside the sign changed.",
  },
  {
    id: "change",
    label: "Make the customer's changes",
    ms: 6400,
    alt: "A customer asks for the grey bedroom walls in the colour of a reference photo. The agent finds the walls and recolours only them.",
  },
  {
    id: "deliver",
    label: "Disclose and deliver",
    ms: 5200,
    alt: "The recoloured photo is published with its disclosure, “Digitally altered photo”, and a code linking to the original.",
  },
];

const S = ASSETS.street;
const pct = (v: number) => `${(v * 100).toFixed(2)}%`;
// The corners frame what QC found (the candidate's box); the restore works through a grown mask around it.
const FOUND = S.candidate;
const SIGN_PAD = 0.012;
const signBox: CSSProperties = {
  left: pct(FOUND[0]! - SIGN_PAD),
  top: pct(FOUND[1]! - SIGN_PAD * 1.5),
  width: pct(FOUND[2]! - FOUND[0]! + 2 * SIGN_PAD),
  height: pct(FOUND[3]! - FOUND[1]! + 3 * SIGN_PAD),
};
const tagBeside: CSSProperties = { left: pct(FOUND[2]! + SIGN_PAD + 0.008), top: pct(FOUND[1]! - SIGN_PAD) };

// The loupe's 3:4 cells show 12% of the photo's width around the sign.
const LOUPE = zoomTo(FOUND, S.size, 0.12, 4 / 3);

const beat = (at: number, extra: Record<string, string> = {}): CSSProperties => ({ "--at": `${at}ms`, ...extra }) as CSSProperties;

function Row({ at, mark, tool, tone, children }: { at: number; mark: string; tool: string; tone?: string; children: ReactNode }) {
  return (
    <div className="trace-row" data-tone={tone} style={beat(at)}>
      <span className="mark">{mark}</span>
      <p>
        <code>{tool}</code> {children}
      </p>
    </div>
  );
}

function Loupe({ at, left, right }: { at: number; left: [string, ReactNode]; right: [string, ReactNode] }) {
  return (
    <div className="loupe beat" style={beat(at, { "--anim": "rise-in" })}>
      <div className="loupe-cell">
        {left[1]}
        <span>{left[0]}</span>
      </div>
      <div className="loupe-cell">
        {right[1]}
        <span>{right[0]}</span>
      </div>
    </div>
  );
}

const zoomed = (name: string, style?: CSSProperties) => <img src={img(name)} alt="" style={{ ...LOUPE, ...style }} />;

function Scene({ id }: { id: SceneId }) {
  switch (id) {
    case "check":
      return (
        <div className="scene scene-check">
          <img className="layer" src={img("street-delivered.webp")} alt="" />
          <img className="layer lens-view lens-out" src={img("street-captured.webp")} alt="" />
          <div className="lens lens-out">
            <i />
            <span className="lens-tag">As captured</span>
          </div>
          <div className="finding beat" style={{ ...signBox, ...beat(3700, { "--anim": "pop-in", "--dur": "420ms" }) }}>
            <i />
          </div>
          <span className="tag beat" style={{ ...tagBeside, ...beat(3800) }}>
            <b>Missing</b> stop sign
          </span>
          <Loupe at={4100} left={["Delivered", zoomed("street-delivered.webp")]} right={["Captured", zoomed("street-captured.webp")]} />
        </div>
      );
    case "fix":
      return (
        <div className="scene scene-fix">
          <img className="layer" src={img("street-delivered.webp")} alt="" />
          <img className="layer restored beat" src={img("street-fixed.webp")} alt="" style={beat(500, { "--dur": "1000ms" })} />
          <div className="finding to-pass" style={signBox}>
            <i />
          </div>
          <span className="tag tag-out" style={tagBeside}>
            <b>Missing</b> stop sign
          </span>
          <span className="tag beat" style={{ ...tagBeside, ...beat(1800), "--tone": "var(--pass)" } as CSSProperties}>
            <b>Restored</b> from the brackets
          </span>
          <Loupe
            at={0}
            left={["Delivered", zoomed("street-delivered.webp")]}
            right={[
              "Fixed",
              <>
                {zoomed("street-delivered.webp")}
                <img className="beat" src={img("street-fixed.webp")} alt="" style={{ ...LOUPE, ...beat(500, { "--dur": "1000ms" }) }} />
              </>,
            ]}
          />
        </div>
      );
    case "change":
      return (
        <div className="scene scene-change">
          <img className="layer" src={img("walls-before.webp")} alt="" />
          <div className="layer walls-mask walls-tint beat" style={beat(1300, { "--anim": "tint-pulse", "--dur": "1700ms", "--ease": "ease-in-out" })} />
          <img className="layer walls-mask beat" src={img("walls-after.webp")} alt="" style={beat(2900, { "--dur": "1300ms" })} />
          <div className="bubble beat" style={beat(150, { "--anim": "rise-in" })}>
            <span>Change the grey walls to the colour in the reference</span>
            <span className="bubble-ref">
              <i />
              reference.jpg
            </span>
          </div>
          <span className="tag beat" style={{ left: "4%", top: "6%", ...beat(4500), "--tone": "var(--pass)" } as CSSProperties}>
            <b>Applied</b> walls only
          </span>
        </div>
      );
    case "deliver":
      return (
        <div className="scene scene-deliver">
          <img className="layer" src={img("walls-after.webp")} alt="" />
          <div className="label-strip beat" style={beat(350, { "--anim": "slide-up", "--dur": "700ms" })}>
            <div>
              <b>Digitally altered photo</b>
              <span>Wall colour changed to match a reference.</span>
              <span className="muted">Scan for the unaltered original.</span>
            </div>
            <img src={img("qr-demo.svg")} alt="" />
          </div>
          <span className="tag published beat" style={{ left: "4%", top: "6%", ...beat(1500) } as CSSProperties}>
            <b>
              <Check size={12} />
            </b>
            Published · record verified
          </span>
        </div>
      );
  }
}

function Trace({ id }: { id: SceneId }) {
  switch (id) {
    case "check":
      return (
        <>
          <Row at={300} mark="›" tool="run_qc_checks">
            compares the photo with its 3 brackets
          </Row>
          <Row at={3800} mark="!" tool="Removal error" tone="find">
            something every bracket shows is missing · confidence {S.confidence.toFixed(2)}
          </Row>
        </>
      );
    case "fix":
      return (
        <>
          <Row at={200} mark="›" tool="restore_from_brackets">
            {(S.maskShare * 100).toFixed(2)}% of the frame, from the brackets
          </Row>
          <Row at={1900} mark="✓" tool="Self-check" tone="pass">
            outside the mask {S.outside} / 255
          </Row>
        </>
      );
    case "change":
      return (
        <>
          <Row at={1200} mark="›" tool="find_regions">
            “grey walls” · {(ASSETS.walls.share * 100).toFixed(1)}% of the frame
          </Row>
          <Row at={2900} mark="›" tool="recolor_region">
            to the reference colour
          </Row>
          <Row at={4500} mark="✓" tool="Self-check" tone="pass">
            outside the walls 0 / 255
          </Row>
        </>
      );
    case "deliver":
      return (
        <>
          <Row at={300} mark="›" tool="publish">
            1 photo disclosed, its original paired
          </Row>
          <Row at={1500} mark="✓" tool="Edit record" tone="pass">
            every fingerprint matches
          </Row>
        </>
      );
  }
}

const LATER = ["street-fixed.webp", "street-sign-mask.png", "walls-before.webp", "walls-after.webp", "walls-mask.png", "qr-demo.svg"];

export function Hero() {
  const reduced = useReducedMotion();
  useEffect(() => {
    const fetchLater = () => LATER.forEach((name) => (new Image().src = img(name)));
    if (document.readyState === "complete") fetchLater();
    else window.addEventListener("load", fetchLater, { once: true });
    return () => window.removeEventListener("load", fetchLater);
  }, []);
  const [index, setIndex] = useState(0);
  const [run, setRun] = useState(0);
  // Reduced motion pauses it straight after the first render (below), so the server and the browser agree.
  const [mode, setMode] = useState<"playing" | "paused" | "ended">("playing");
  const [published, setPublished] = useState(false);
  const device = useRef<HTMLElement>(null);
  const onScreen = useOnScreen(device);
  const visible = usePageVisible();
  const active = mode === "playing" && onScreen && visible;
  const still = reduced;
  const remaining = useRef(STEPS[0]!.ms);
  const spent = useRef(0);

  // A new step (or a replay) starts its own clock.
  useEffect(() => {
    remaining.current = STEPS[index]!.ms;
    spent.current = 0;
    setPublished(false);
  }, [index, run]);

  // The step's timer runs only while playing and seen; pausing keeps the time left.
  useEffect(() => {
    if (!active) return;
    const started = performance.now();
    const base = spent.current;
    const timers = [
      window.setTimeout(() => {
        if (index < STEPS.length - 1) setIndex(index + 1);
        else setMode("ended");
      }, remaining.current),
    ];
    // The bar reads "Published" once the record checks out, 1.5 s into the last step.
    if (STEPS[index]!.id === "deliver" && base < 1500) timers.push(window.setTimeout(() => setPublished(true), 1500 - base));
    return () => {
      const used = performance.now() - started;
      remaining.current -= used;
      spent.current = base + used;
      timers.forEach((t) => window.clearTimeout(t));
    };
  }, [active, index, run]);

  useEffect(() => {
    if (reduced && mode === "playing") setMode("paused");
  }, [reduced, mode]);

  const go = (i: number) => {
    setIndex(i);
    setRun((r) => r + 1);
    setMode(reduced ? "paused" : "playing");
  };
  const control = () => {
    if (mode === "playing") setMode("paused");
    else if (mode === "ended") go(0);
    else setMode("playing");
  };

  const step = STEPS[index]!;
  const done = step.id === "deliver" && (mode === "ended" || published || still);
  return (
    <section className="wrap hero" aria-labelledby="hero-title">
      <div className="hero-copy">
        <h1 id="hero-title">Checked against what the camera saw.</h1>
        <p className="lede">
          ShootAgent is an AI agent for real-estate photo shoots. It compares every edited photo with the camera’s own
          exposures, fixes what the editor got wrong, makes the customer’s changes, and delivers the listing with the
          disclosures California now requires.
        </p>
        <div className="hero-actions">
          <DemoButton />
          <a className="text-link" href="#how">
            How it works
          </a>
        </div>
      </div>

      <figure
        className="device"
        ref={device}
        data-scene={step.id}
        data-paused={!active || undefined}
        role="img"
        aria-label={`ShootAgent at work, step ${index + 1} of 4: ${step.alt}`}
      >
        <div className="device-inner" aria-hidden>
          <div className="device-bar">
            <Mark size={16} />
            <div className="device-bar-title">
              <b>Demo shoot</b>
              <span>QC and customer requests</span>
            </div>
            <span className="live-pill" data-tone={done ? "done" : undefined}>
              <span className="live-dot" />
              {done ? "Published" : "Agent working"}
            </span>
          </div>
          <div className="device-stage">
            <Scene key={`${step.id}-${run}`} id={step.id} />
          </div>
          <div className="device-trace" key={`trace-${step.id}-${run}`}>
            <Trace id={step.id} />
          </div>
        </div>
      </figure>

      <div className="hero-steps" data-paused={!active || undefined} data-still={still || undefined}>
        <ol className="steps" aria-label="What the agent does, shown in the picture">
          {STEPS.map((s, i) => (
            <li key={s.id}>
              <button
                className="step"
                aria-current={i === index ? "step" : undefined}
                data-done={i < index || undefined}
                onClick={() => go(i)}
              >
                <span className="step-n">{i + 1}</span>
                <span>{s.label}</span>
                {i === index && (
                  <span className="step-progress" style={{ "--dur": `${s.ms}ms` } as CSSProperties}>
                    <span key={`${run}-${i}`} />
                  </span>
                )}
              </button>
            </li>
          ))}
        </ol>
        <button className="control" onClick={control} aria-label={mode === "playing" ? "Pause the walkthrough" : mode === "ended" ? "Play the walkthrough again" : "Play the walkthrough"}>
          {mode === "playing" ? <Pause /> : mode === "ended" ? <Replay /> : <Play />}
          {mode === "playing" ? "Pause" : mode === "ended" ? "Replay" : "Play"}
        </button>
      </div>
    </section>
  );
}
