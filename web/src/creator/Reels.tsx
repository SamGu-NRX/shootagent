import { Pause, Play, Volume2, VolumeX } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { CONTROL_AGENT } from "./phases";
import { useCreator } from "./store";
import type { LabState, Reaction, Reel, ReelConcept, ViewerPersona } from "./types";
import { fmtPct, focusStyle, ReelBadge, SectionHead } from "./ui";

const NONE: never[] = [];

interface Planned {
  id: string;
  label: string;
  kind: Reel["kind"];
  concept: ReelConcept | undefined;
  reel: Reel | undefined;
}

/** The reels this lab renders, rendered or not yet: A, the finalists (B, C, ...) and S. */
export function plannedReels(lab: LabState): Planned[] {
  const byId = new Map(lab.reels.map((r) => [r.id, r]));
  const concept = (id: string | undefined) => lab.concepts.find((c) => c.id === id);
  const out: Planned[] = [];
  const template = lab.concepts.find((c) => c.agent === "template");
  out.push({ id: "A", label: "Template", kind: "baseline", concept: template, reel: byId.get("A") });
  const n = lab.finalists.length || lab.options.finalists;
  for (let i = 0; i < n; i++) {
    const id = "BCDEFG"[i]!;
    const r = byId.get(id);
    out.push({ id, label: r?.label ?? (["Showrunner's pick", "Wildcard"][i] ?? `Finalist ${i + 1}`), kind: "creative", concept: concept(r?.concept ?? lab.finalists[i]), reel: r });
  }
  if (lab.options.single_agent && lab.options.render_single_agent) {
    const r = byId.get("S");
    const first = lab.single_agent[0] ?? lab.concepts.find((c) => c.agent === CONTROL_AGENT)?.id;
    out.push({ id: "S", label: "Single agent", kind: "single_agent", concept: concept(r?.concept ?? first), reel: r });
  }
  // anything the server rendered that the plan didn't expect
  for (const r of lab.reels) if (!out.some((p) => p.id === r.id)) out.push({ id: r.id, label: r.label, kind: r.kind, concept: concept(r.concept), reel: r });
  return out.map((p) => (p.reel ? { ...p, label: p.reel.label, kind: p.reel.kind, concept: concept(p.reel.concept) ?? p.concept } : p));
}

export function ReelsSection() {
  const lab = useCreator((s) => s.lab)!;
  const api = useCreator((s) => s.api);
  const shootId = useCreator((s) => s.shootId);
  const sims = useCreator((s) => s.sims) ?? lab.simulations;
  const planned = useMemo(() => plannedReels(lab), [lab]);
  const videos = useRef(new Map<string, HTMLVideoElement>());
  const [syncing, setSyncing] = useState(false);
  const [audible, setAudible] = useState<string | null>(null);
  const rendered = planned.filter((p) => p.reel);
  const render = lab.phases.find((p) => p.key === "render");

  // awards, once the evidence exists
  const summaries = lab.evaluation?.summaries ?? NONE;
  const mostCreative = summaries.length ? [...summaries].sort((a, b) => b.creativity - a.creativity)[0]!.reel : null;
  const furthest = sims.length ? [...sims].sort((a, b) => b.totals.views - a.totals.views)[0]!.reel : null;

  const register = useCallback((id: string, v: HTMLVideoElement | null) => {
    if (v) videos.current.set(id, v);
    else videos.current.delete(id);
  }, []);

  const playAll = async () => {
    const vs = [...videos.current.values()];
    if (syncing) {
      vs.forEach((v) => v.pause());
      setSyncing(false);
      return;
    }
    vs.forEach((v) => {
      v.pause();
      v.currentTime = 0;
    });
    await Promise.all(vs.map((v) => v.play().catch(() => undefined)));
    setSyncing(true);
  };

  return (
    <section id="cl-reels" className="cl-section" aria-labelledby="cl-reels-title">
      <SectionHead
        id="cl-reels-title"
        title="The reels"
        sub={
          <>
            A is the template reel most listings post. The room's two picks sit beside it
            {planned.some((p) => p.kind === "single_agent") ? ", with the single agent's best for comparison." : "."}
          </>
        }
      >
        <button className="btn" onClick={() => void playAll()} disabled={rendered.length < 2} aria-pressed={syncing}>
          {syncing ? <Pause size={13} aria-hidden /> : <Play size={13} aria-hidden />}
          {syncing ? "Pause all" : "Play all in sync"}
        </button>
      </SectionHead>
      <div className="cl-phones" style={{ "--n": planned.length } as CSSProperties}>
        {planned.map((p) => (
          <PhoneCard
            key={p.id}
            p={p}
            src={p.reel && api && shootId ? api.fileUrl(shootId, lab.id, p.reel.video) : null}
            poster={p.reel && api && shootId ? api.fileUrl(shootId, lab.id, p.reel.poster) : null}
            rendering={render?.status === "running"}
            register={register}
            audible={audible === p.id}
            onAudible={(on) => setAudible(on ? p.id : null)}
            onPause={() => setSyncing(false)}
            awards={[p.id === mostCreative ? "Most creative" : null, p.id === furthest ? "Travels furthest" : null].filter((x): x is string => !!x)}
          />
        ))}
      </div>
    </section>
  );
}

function PhoneCard({
  p,
  src,
  poster,
  rendering,
  register,
  audible,
  onAudible,
  onPause,
  awards,
}: {
  p: Planned;
  src: string | null;
  poster: string | null;
  rendering: boolean;
  register: (id: string, v: HTMLVideoElement | null) => void;
  audible: boolean;
  onAudible: (on: boolean) => void;
  onPause: () => void;
  awards: string[];
}) {
  const video = useRef<HTMLVideoElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [failed, setFailed] = useState(false);
  const photos = useCreator((s) => s.shoot?.photos);
  const reel = p.reel;
  const seconds = reel?.seconds ?? 0;

  useEffect(() => {
    if (video.current) video.current.muted = !audible;
  }, [audible]);

  // a smooth playhead for the progress bar and the swipe-away strip while playing
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      if (video.current) setTime(video.current.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  const setRef = (v: HTMLVideoElement | null) => {
    video.current = v;
    register(p.id, v);
  };
  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => undefined);
    else {
      v.pause();
      onPause();
    }
  };
  const first = p.concept?.beats[0];

  return (
    <figure className="cl-phone-card" data-kind={p.kind}>
      <figcaption className="cl-phone-cap">
        <span className="cl-phone-row">
          <ReelBadge id={p.id} size={20} />
          <span className="cl-phone-label">{p.label}</span>
          {reel && <span className="cl-phone-secs num">{reel.seconds.toFixed(1)} s</span>}
        </span>
        <span className="cl-phone-title">{reel?.title ?? p.concept?.title ?? " "}</span>
        <span className="cl-phone-award">{awards.join(" · ")}</span>
      </figcaption>

      <div className="cl-phone" data-state={reel ? "ready" : rendering ? "rendering" : "waiting"}>
        {reel && src && !failed ? (
          <>
            <video
              ref={setRef}
              src={src}
              poster={poster ?? undefined}
              muted
              loop
              playsInline
              preload="metadata"
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onTimeUpdate={(e) => !playing && setTime(e.currentTarget.currentTime)}
              onError={() => setFailed(true)}
              aria-label={`Reel ${p.id}: ${reel.title}`}
            />
            <button className="cl-phone-hit" onClick={toggle} aria-label={playing ? `Pause reel ${p.id}` : `Play reel ${p.id}`}>
              {!playing && (
                <span className="cl-phone-play" aria-hidden>
                  <Play size={18} fill="currentColor" />
                </span>
              )}
            </button>
            <button className="cl-phone-sound" onClick={() => onAudible(!audible)} aria-label={audible ? `Mute reel ${p.id}` : `Unmute reel ${p.id}`} aria-pressed={audible}>
              {audible ? <Volume2 size={14} aria-hidden /> : <VolumeX size={14} aria-hidden />}
            </button>
            <span className="cl-phone-progress" aria-hidden>
              <span style={{ width: `${seconds ? Math.min(100, (time / seconds) * 100) : 0}%` }} />
            </span>
          </>
        ) : (
          <div className="cl-phone-wait">
            {first && photos?.[first.view] && <img src={photos[first.view]} alt="" style={focusStyle(first.focus)} />}
            <div className="cl-phone-wait-text">
              {reel && failed ? (
                <p className="cl-phone-wait-state">The video didn't load. It may still be writing to disk; reload in a moment.</p>
              ) : (
                <>
                  <p className="cl-phone-wait-state">
                    {rendering ? (
                      <>
                        <span className="spinner spinner-sm" aria-hidden /> Rendering
                      </>
                    ) : p.concept ? (
                      "Renders next"
                    ) : (
                      "Renders after the greenlight"
                    )}
                  </p>
                  {p.concept && <p className="cl-phone-wait-hook">“{p.concept.hook.text}”</p>}
                </>
              )}
            </div>
          </div>
        )}
      </div>

      {reel && <ExitStrip reel={reel} time={time} />}
    </figure>
  );
}

// ---- where the audience panel swiped away ------------------------------------------------------------

interface Exit {
  r: Reaction;
  v: ViewerPersona;
  x: number;
}

/** Weighted share of the panel still watching at each of `n + 1` points across the reel. */
export function watchCurve(rs: Reaction[], weights: Map<string, number>, seconds: number, n = 60): number[] {
  const total = rs.reduce((a, r) => a + (weights.get(r.viewer) ?? 0), 0) || 1;
  return Array.from({ length: n + 1 }, (_, i) => {
    const t = (i / n) * seconds;
    return rs.reduce((a, r) => a + (r.completed || r.watch_seconds >= t - 1e-6 ? (weights.get(r.viewer) ?? 0) : 0), 0) / total;
  });
}

/** One line under a reel: a tick where each viewer swiped away (their reaction on hover), and how many finished. */
function ExitStrip({ reel, time }: { reel: Reel; time: number }) {
  const reactions = useCreator((s) => s.lab?.evaluation?.reactions ?? NONE);
  const viewers = useCreator((s) => s.lab?.evaluation?.viewers ?? NONE);
  const [hover, setHover] = useState<Exit | null>(null);
  const seconds = Math.max(reel.seconds, 0.1);
  const mine = useMemo(() => reactions.filter((r) => r.reel === reel.id), [reactions, reel.id]);
  const weights = useMemo(() => new Map(viewers.map((v) => [v.id, v.weight])), [viewers]);
  const finished = useMemo(() => watchCurve(mine, weights, seconds, 1)[1] ?? 0, [mine, weights, seconds]);
  const exits = useMemo(
    () =>
      mine
        .filter((r) => !r.completed)
        .map((r) => ({ r, v: viewers.find((v) => v.id === r.viewer), x: Math.min(1, r.watch_seconds / seconds) }))
        .filter((e): e is Exit => !!e.v)
        .sort((a, b) => a.x - b.x),
    [mine, viewers, seconds],
  );
  if (!mine.length) return <p className="cl-exit-empty">Swipe-aways show here once the audience panel has watched.</p>;
  return (
    <div className="cl-exit">
      <div className="cl-exit-track" role="group" aria-label={`Where viewers swiped away from reel ${reel.id}`}>
        <span className="cl-exit-played" style={{ width: `${Math.min(100, (time / seconds) * 100)}%` }} aria-hidden />
        {exits.map((e) => (
          <button
            key={e.v.id}
            className="cl-exit-tick"
            style={{ left: `${e.x * 100}%` }}
            data-active={hover === e || undefined}
            onPointerEnter={() => setHover(e)}
            onPointerLeave={() => setHover((h) => (h === e ? null : h))}
            onFocus={() => setHover(e)}
            onBlur={() => setHover((h) => (h === e ? null : h))}
            aria-label={`${e.v.name} swiped away at ${e.r.watch_seconds.toFixed(1)} s: ${e.r.first_impression}`}
          />
        ))}
        {hover && (
          <div className="cl-tip cl-exit-tip" style={{ left: `${Math.min(80, Math.max(20, hover.x * 100))}%` }} role="status">
            <p className="cl-exit-tip-head">
              <b>{hover.v.name}</b> left at <span className="num">{hover.r.watch_seconds.toFixed(1)} s</span>
            </p>
            <p className="cl-exit-tip-q">“{hover.r.first_impression}”</p>
          </div>
        )}
      </div>
      <span className="cl-exit-end">
        <span className="num">{fmtPct(finished)}</span> finished
      </span>
    </div>
  );
}
