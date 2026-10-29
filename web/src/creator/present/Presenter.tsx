// The presenter's shell: a full-screen 1920x1080 stage scaled to the window, a clock that drives the scenes,
// and quiet controls. Opens from "Present" in the header or with ?present in the URL.
//   ?present            open on load          &scene=<id|n>  start at a scene      &autoplay=0  start paused
//   &clean              no controls (for screen recording; H toggles them)          &captions    show captions
// Keys: Space play/pause · ←/→ previous/next scene · R restart · C captions · H hide controls · Esc exit.
// window.__present.seek(scene, ms) / play() / pause() / state() drive it from a recording script.

import "../../styles/present.css";
import { ChevronLeft, ChevronRight, Captions, Pause, Play, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { create } from "zustand";
import { useCreator } from "../store";
import { buildStory } from "./metrics";
import { SCENES } from "./scenes";

const W = 1920;
const H = 1080;

const params = () => new URLSearchParams(typeof location === "undefined" ? "" : location.search);

interface PresenterState {
  open: boolean;
  show: () => void;
  hide: () => void;
}

export const usePresenter = create<PresenterState>((set) => ({
  open: params().has("present"),
  show: () => set({ open: true }),
  hide: () => set({ open: false }),
}));

function startScene(): number {
  const s = params().get("scene");
  if (!s) return 0;
  const byId = SCENES.findIndex((x) => x.id === s);
  if (byId >= 0) return byId;
  const n = Number(s);
  return Number.isInteger(n) && n >= 1 && n <= SCENES.length ? n - 1 : 0;
}

function useStageScale(): number {
  const get = () => (typeof window === "undefined" ? 1 : Math.min(window.innerWidth / W, window.innerHeight / H));
  const [s, setS] = useState(get);
  useEffect(() => {
    const on = () => setS(get());
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return s;
}

declare global {
  interface Window {
    __present?: { seek: (scene: number | string, ms?: number) => void; play: () => void; pause: () => void; state: () => { scene: number; t: number; playing: boolean; total: number } };
  }
}

export function Presenter() {
  const lab = useCreator((s) => s.lab);
  const shoot = useCreator((s) => s.shoot);
  const api = useCreator((s) => s.api);
  const shootId = useCreator((s) => s.shootId);
  const hide = usePresenter((s) => s.hide);
  const scale = useStageScale();
  const [scene, setScene] = useState(startScene);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(() => params().get("autoplay") !== "0");
  const [take, setTake] = useState(0);
  const [captions, setCaptions] = useState(() => params().has("captions"));
  const [chrome, setChrome] = useState(() => !params().has("clean"));
  const clock = useRef({ at: 0, t: 0 });

  const story = useMemo(() => (lab ? buildStory(lab, shoot?.listing?.price) : null), [lab, shoot?.listing?.price]);
  const def = SCENES[scene]!;

  const go = useCallback((i: number, ms = 0) => {
    const next = Math.max(0, Math.min(SCENES.length - 1, i));
    setScene(next);
    setT(ms);
    clock.current = { at: performance.now(), t: ms };
    setTake((k) => k + 1);
  }, []);

  // the clock: t advances while playing; at the end of a scene the next one starts, the last one holds
  useEffect(() => {
    if (!playing) return;
    clock.current = { at: performance.now(), t };
    let raf = 0;
    const tick = (now: number) => {
      const next = clock.current.t + (now - clock.current.at);
      if (next >= def.duration) {
        if (scene < SCENES.length - 1) {
          go(scene + 1);
          return;
        }
        setT(def.duration);
        setPlaying(false);
        return;
      }
      setT(next);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // t is read once when play starts; the loop owns it after that
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, scene, def.duration, go]);

  const toggle = useCallback(() => {
    if (!playing && scene === SCENES.length - 1 && t >= def.duration) go(0);
    setPlaying((p) => !p);
  }, [playing, scene, t, def.duration, go]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === " " || e.key === "k") (e.preventDefault(), toggle());
      else if (e.key === "ArrowRight" || e.key === "PageDown") (e.preventDefault(), go(scene + 1));
      else if (e.key === "ArrowLeft" || e.key === "PageUp") (e.preventDefault(), go(scene - 1));
      else if (e.key === "r" || e.key === "Home") (go(0), setPlaying(true));
      else if (e.key === "c") setCaptions((c) => !c);
      else if (e.key === "h") setChrome((c) => !c);
      else if (e.key === "Escape") hide();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle, go, scene, hide]);

  useEffect(() => {
    window.__present = {
      seek: (s, ms = 0) => go(typeof s === "number" ? s : Math.max(0, SCENES.findIndex((x) => x.id === s)), ms),
      play: () => setPlaying(true),
      pause: () => setPlaying(false),
      state: () => ({ scene, t, playing, total: SCENES.length }),
    };
  });
  useEffect(() => () => void delete window.__present, []);

  useEffect(() => {
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => void (document.body.style.overflow = before);
  }, []);

  const fileUrl = useCallback((path: string) => (api && shootId && lab ? api.fileUrl(shootId, lab.id, path) : path), [api, shootId, lab]);

  if (!lab || !story || lab.status !== "done" || !lab.evaluation) {
    return (
      <div className="pz" role="dialog" aria-label="Presenter">
        <p className="pz-wait">{lab && lab.status === "running" ? "The presenter opens once the lab has finished." : "Opening the lab…"}</p>
        <button className="pz-exit" onClick={hide} aria-label="Close the presenter">
          <X size={18} />
        </button>
      </div>
    );
  }

  const Scene = def.Component;
  return (
    <div className="pz" role="dialog" aria-label="Presenter" data-paused={!playing || undefined} data-chrome={chrome || undefined}>
      <div className="pz-stage" style={{ transform: `translate(-50%, -50%) scale(${scale})` }}>
        <div className="pz-scene" key={`${def.id}-${take}`} data-scene={def.id}>
          <Scene lab={lab} shoot={shoot} story={story} fileUrl={fileUrl} t={t} duration={def.duration} playing={playing} />
        </div>
        {captions && <p className="pz-caption">{def.caption(story)}</p>}
      </div>

      {chrome && (
        <div className="pz-ui">
          <div className="pz-progress" role="tablist" aria-label="Scenes">
            {SCENES.map((s, i) => (
              <button key={s.id} role="tab" aria-selected={i === scene} className="pz-seg" onClick={() => go(i)} title={s.label}>
                <span className="pz-seg-fill" style={{ width: `${i < scene ? 100 : i === scene ? Math.min(100, (t / def.duration) * 100) : 0}%` }} />
                <span className="pz-seg-label">{s.label}</span>
              </button>
            ))}
          </div>
          <div className="pz-buttons">
            <button onClick={() => go(scene - 1)} aria-label="Previous scene" disabled={scene === 0}>
              <ChevronLeft size={18} />
            </button>
            <button onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
              {playing ? <Pause size={18} /> : <Play size={18} />}
            </button>
            <button onClick={() => go(scene + 1)} aria-label="Next scene" disabled={scene === SCENES.length - 1}>
              <ChevronRight size={18} />
            </button>
            <button onClick={() => (go(0), setPlaying(true))} aria-label="Restart">
              <RotateCcw size={16} />
            </button>
            <button onClick={() => setCaptions((c) => !c)} aria-label="Captions" aria-pressed={captions}>
              <Captions size={18} />
            </button>
            <button onClick={hide} aria-label="Close the presenter">
              <X size={18} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
