// "Watch a real run": a 1:51 cut of the team's screen recording of one real run on the demo shoot
// (docs/media/demo-walkthrough.mp4, 6:54), for visitors who can't get past the live demo's passcode. It plays at real
// speed. Every cut carries a chip with the real time it skips, read off the run clock in the trace, and the captions
// repeat it. The markup is the finished state: first frame, controls and chapters. Nothing loads until the section
// is near the screen (then the poster); the video loads when it plays. Muted autoplay starts once, the first time
// half the picture is on screen, and pauses off screen or in a background tab; reduced motion never autoplays.
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ArrowUpRight, Pause, Play, Replay } from "./Icons";
import { useReducedMotion } from "../lib/motion";
import "./Tour.css";

const MEDIA = "/media/real-run";
const FULL_RECORDING = "https://github.com/SamGu-NRX/shootagent/blob/main/docs/media/demo-walkthrough.mp4";
const DURATION = 111.3;

// The cut's chapters, mapped from the timestamps in docs/media/demo-walkthrough.md (real-run-chapters.en.vtt holds
// the same).
const CHAPTERS: [start: number, title: string][] = [
  [0, "Run QC: a plan first"],
  [13, "Candidates to judge"],
  [22, "Fail, retry, pass"],
  [39.5, "Verdicts and the stop sign"],
  [54.7, "A request for the whole shoot"],
  [67, "Shift-click the TV"],
  [94.5, "The listing reel"],
];

type Status = "idle" | "playing" | "paused" | "ended";
type IOSVideo = HTMLVideoElement & { webkitEnterFullscreen?: () => void };

const chapterAt = (t: number) => CHAPTERS.reduce((at, [start], i) => (t >= start - 0.05 ? i : at), 0);
const mmss = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

// A cue that follows a cut opens with the time it skips, "[56 s later] …"; that part reads as a tag.
function Cue({ text }: { text: string }) {
  const skip = /^\[([^\]]+)\]\s*/.exec(text);
  return skip ? (
    <>
      <span className="tour-skip">{skip[1]}</span> {text.slice(skip[0].length)}
    </>
  ) : (
    <>{text}</>
  );
}

const Captions = () => (
  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" aria-hidden>
    <rect x="3" y="5.5" width="18" height="13" rx="2.5" />
    <path d="M10.6 10.2a2.3 2.3 0 1 0 0 3.6M17.1 10.2a2.3 2.3 0 1 0 0 3.6" />
  </svg>
);
const Corners = ({ inward }: { inward: boolean }) => (
  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d={inward ? "M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" : "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"} />
  </svg>
);

export function Tour() {
  const figure = useRef<HTMLElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const seek = useRef<HTMLInputElement>(null);
  const captionTrack = useRef<HTMLTrackElement>(null);
  const reduced = useReducedMotion();
  const [enhanced, setEnhanced] = useState(false);
  const [near, setNear] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [time, setTime] = useState(0);
  const [captions, setCaptions] = useState(true);
  const [cue, setCue] = useState("");
  const [full, setFull] = useState(false);
  const userPaused = useRef(false); // the visitor paused: never resume on their behalf
  const autoplayed = useRef(false); // autoplay happens once
  const resume = useRef(false); // paused for leaving the screen or the tab, to resume on return
  const userStarted = useRef(false); // the visitor pressed play or picked a chapter
  const visible = useRef(0); // share of the picture on screen
  const reducedNow = useRef(reduced);
  reducedNow.current = reduced;
  const captionsOn = useRef(captions);
  captionsOn.current = captions;

  // Without script the browser's own controls (and its own captions) stand in.
  useLayoutEffect(() => setEnhanced(true), []);

  // The poster loads once the section is within a screen or so of view.
  useEffect(() => {
    const el = figure.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (!e?.isIntersecting) return;
        setNear(true);
        io.disconnect();
      },
      { rootMargin: "800px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // The playhead drives --t (chapter bars, the seek fill) every frame while playing; React state follows at the
  // media events' pace for the text.
  useEffect(() => {
    const v = video.current;
    const fig = figure.current;
    if (!v || !fig) return;
    v.muted = true;
    v.defaultMuted = true;
    let raf = 0;
    const paint = () => {
      fig.style.setProperty("--t", v.currentTime.toFixed(3));
      if (seek.current) seek.current.value = String(v.currentTime);
    };
    const loop = () => {
      paint();
      raf = requestAnimationFrame(loop);
    };
    const onPlay = () => {
      setStatus("playing");
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(loop);
    };
    const onPause = () => {
      cancelAnimationFrame(raf);
      paint();
      if (!v.ended) setStatus("paused");
    };
    const onEnded = () => {
      cancelAnimationFrame(raf);
      paint();
      setStatus("ended");
    };
    const onTime = () => setTime(v.currentTime);
    const onSeek = () => {
      paint();
      setTime(v.currentTime);
    };
    const events: [string, () => void][] = [
      ["play", onPlay],
      ["pause", onPause],
      ["ended", onEnded],
      ["timeupdate", onTime],
      ["seeking", onSeek],
      ["seeked", onSeek],
    ];
    events.forEach(([name, fn]) => v.addEventListener(name, fn));
    return () => {
      cancelAnimationFrame(raf);
      events.forEach(([name, fn]) => v.removeEventListener(name, fn));
    };
  }, []);

  // Captions are drawn below the picture, where they cover none of the recording and stay legible on a phone; the
  // track itself is hidden, except in iOS's own full-screen player, which can only show the browser's rendering.
  useLayoutEffect(() => {
    const v = video.current;
    const track = captionTrack.current?.track;
    if (!v || !track) return;
    track.mode = "hidden";
    const onCue = () => {
      const active = track.activeCues?.[0] as VTTCue | undefined;
      setCue(active?.text ?? "");
    };
    const onBegin = () => (track.mode = captionsOn.current ? "showing" : "disabled");
    const onEnd = () => (track.mode = "hidden");
    track.addEventListener("cuechange", onCue);
    v.addEventListener("webkitbeginfullscreen", onBegin);
    v.addEventListener("webkitendfullscreen", onEnd);
    return () => {
      track.removeEventListener("cuechange", onCue);
      v.removeEventListener("webkitbeginfullscreen", onBegin);
      v.removeEventListener("webkitendfullscreen", onEnd);
    };
  }, []);

  // Autoplay once, muted, when half the picture is on screen; pause when it leaves the screen or the tab hides,
  // and pick up again on return unless the visitor paused it.
  useEffect(() => {
    const el = stage.current;
    const v = video.current;
    if (!el || !v) return;
    const tryPlay = () => {
      if (userPaused.current || v.ended || document.hidden || visible.current < 0.5) return;
      if (resume.current) {
        // playback this section paused, the visitor's or the autoplay's, carries on
        resume.current = false;
        v.play().catch(() => {});
      } else if (!autoplayed.current && !reducedNow.current) {
        autoplayed.current = true;
        v.play().catch(() => {});
      }
    };
    const hold = () => {
      if (v.paused) return;
      resume.current = true;
      v.pause();
    };
    const io = new IntersectionObserver(
      ([e]) => {
        if (!e) return;
        visible.current = e.isIntersecting ? e.intersectionRatio : 0;
        if (visible.current >= 0.5) tryPlay();
        else if (!e.isIntersecting) hold();
      },
      { threshold: [0, 0.5] },
    );
    const onVisibility = () => (document.hidden ? hold() : tryPlay());
    io.observe(el);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      io.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  // Reduced motion switched on mid-way stops playback that the visitor didn't start.
  useEffect(() => {
    const v = video.current;
    if (reduced && v && !v.paused && autoplayed.current && !userPaused.current && !userStarted.current) v.pause();
  }, [reduced]);

  useEffect(() => {
    const on = () => setFull(document.fullscreenElement === figure.current);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);

  const play = () => {
    const v = video.current;
    if (!v) return;
    userPaused.current = false;
    userStarted.current = true;
    autoplayed.current = true;
    resume.current = false;
    v.play().catch(() => {});
  };
  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (!v.paused && !v.ended) {
      userPaused.current = true;
      resume.current = false;
      v.pause();
      return;
    }
    if (v.ended) v.currentTime = 0;
    play();
  };
  const goTo = (start: number) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = start + 0.02;
    figure.current?.style.setProperty("--t", String(start));
    setTime(start);
    play();
  };
  const scrub = (t: number) => {
    const v = video.current;
    if (!v || Number.isNaN(t)) return;
    v.currentTime = t;
    figure.current?.style.setProperty("--t", String(t));
    setTime(t);
  };
  const fullScreen = () => {
    const el = figure.current;
    const v = video.current as IOSVideo | null;
    if (!el || !v) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else if (typeof el.requestFullscreen === "function") void el.requestFullscreen().catch(() => {});
    else v.webkitEnterFullscreen?.();
  };

  const at = chapterAt(time);
  const label = status === "playing" ? "Pause" : status === "ended" ? "Replay" : "Play";
  const hint: ReactNode = status === "idle" && !cue ? "The recording is silent; its narration appears here." : null;

  return (
    <section className="section" id="run" aria-labelledby="run-title">
      <div className="wrap">
        <div className="section-head">
          <h2 id="run-title">Watch a real run.</h2>
          <p className="lede">
            One real run of ShootAgent on the demo shoot, from QC to the reel, recorded from the app. It plays at real
            speed, and every cut is marked with the real time it skips.
          </p>
        </div>
        <figure className="tour" ref={figure} data-status={status}>
          <div className="tour-player">
            <div className="tour-stage" ref={stage}>
              <video
                ref={video}
                width={1440}
                height={810}
                preload="none"
                muted
                playsInline
                controls={!enhanced}
                poster={near ? `${MEDIA}-poster.jpg` : undefined}
                onClick={enhanced ? toggle : undefined}
                aria-label="Screen recording of ShootAgent's QC, two customer requests and the listing reel on the demo shoot"
              >
                <source src={`${MEDIA}.webm`} type='video/webm; codecs="vp9"' />
                <source src={`${MEDIA}.mp4`} type="video/mp4" />
                <track ref={captionTrack} kind="captions" src={`${MEDIA}.en.vtt`} srcLang="en" label="English" default />
                <track kind="chapters" src={`${MEDIA}-chapters.en.vtt`} srcLang="en" label="Chapters" />
              </video>
              <span className="tour-glyph" aria-hidden>
                <Play size={28} />
              </span>
            </div>
            {captions && (
              <p className="tour-caption" data-hint={hint ? "" : undefined}>
                {hint ?? <Cue text={cue} />}
              </p>
            )}
          </div>

          <div className="tour-controls">
            <button className="control tour-play" onClick={toggle} aria-label={`${label} the recording`}>
              {status === "playing" ? <Pause /> : status === "ended" ? <Replay /> : <Play />}
              {label}
            </button>
            <input
              ref={seek}
              className="tour-seek"
              type="range"
              min={0}
              max={DURATION}
              step={0.1}
              defaultValue={0}
              aria-label="Position in the recording"
              aria-valuetext={`${mmss(time)} of ${mmss(DURATION)}, ${CHAPTERS[at]![1]}`}
              onChange={(e) => scrub(e.currentTarget.valueAsNumber)}
            />
            <span className="tour-time" aria-hidden>
              {mmss(time)} / {mmss(DURATION)}
            </span>
            <button className="control tour-cc" aria-pressed={captions} onClick={() => setCaptions((c) => !c)}>
              <Captions />
              <span className="tour-cc-label">Captions</span>
            </button>
            <button className="control tour-full" onClick={fullScreen} aria-label={full ? "Exit full screen" : "Full screen"}>
              <Corners inward={full} />
            </button>
          </div>

          <ol className="tour-chapters" aria-label="Chapters">
            {CHAPTERS.map(([start, title], i) => (
              <li key={start}>
                <button
                  className="tour-chapter"
                  aria-current={i === at ? "true" : undefined}
                  style={{ "--a": start, "--b": CHAPTERS[i + 1]?.[0] ?? DURATION } as CSSProperties}
                  onClick={() => goTo(start)}
                >
                  <span className="tour-chapter-time">{mmss(start)}</span>
                  <span className="tour-chapter-title">{title}</span>
                  <span className="tour-chapter-bar" aria-hidden>
                    <span />
                  </span>
                </button>
              </li>
            ))}
          </ol>

          <figcaption className="tour-note">
            <span>
              Recorded on 28 October 2026: 13 minutes of real time in 1:51. The agent’s four runs cost $0.91.
            </span>
            <a className="text-link" href={FULL_RECORDING}>
              The full recording, 6:54 <ArrowUpRight />
            </a>
          </figcaption>
        </figure>
      </div>
    </section>
  );
}
