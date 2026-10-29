import { Dices, FastForward, Pause, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { clockHour, formatRatio, interpolateSeries, launchOutcome, METRICS, segmentsAt, visibleComments, type Metric } from "./sim";
import { useCreator } from "./store";
import type { LabState, Reel, SimPoint, Simulation, ViewerPersona } from "./types";
import { Disclosure, fmtCompact, fmtInt, fmtPct, niceTicks, ReelBadge, reelInks, SectionHead, useDismiss, useReducedMotion, useWidth } from "./ui";

/** Real seconds the 48-hour playback takes. */
export const PLAY_MS = 20_000;
const NONE: never[] = [];

// ---- the accelerated clock --------------------------------------------------------------------------

function useLaunchClock(hours: number) {
  const [hour, setHour] = useState(0);
  const [playing, setPlaying] = useState(false);
  const from = useRef({ at: 0, hour: 0 });
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = (now: number) => {
      const h = from.current.hour + clockHour(now - from.current.at, PLAY_MS, hours);
      if (h >= hours) {
        setHour(hours);
        setPlaying(false);
        return;
      }
      setHour(h);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, hours]);
  const play = useCallback(
    (fromHour?: number) => {
      const start = fromHour ?? (hour >= hours ? 0 : hour);
      from.current = { at: performance.now(), hour: start };
      setHour(start);
      setPlaying(true);
    },
    [hour, hours],
  );
  const pause = useCallback(() => setPlaying(false), []);
  const seek = useCallback((h: number) => {
    setPlaying(false);
    setHour(h);
  }, []);
  return { hour, playing, play, pause, seek };
}

// ---- section ----------------------------------------------------------------------------------------

export function LaunchSection() {
  const lab = useCreator((s) => s.lab)!;
  const override = useCreator((s) => s.sims);
  const sims = override ?? lab.simulations;
  const viewers = lab.evaluation?.viewers ?? NONE;
  const ready = sims.length > 0 && lab.reels.length > 0;
  return (
    <section id="cl-launch" className="cl-section" aria-labelledby="cl-launch-title">
      <SectionHead
        id="cl-launch-title"
        title="Simulated launch"
        sub={`Which reel would travel further? ${sims[0]?.hours ?? lab.options.hours} hours on an account with ${fmtInt(sims[0]?.followers ?? lab.options.followers)} followers, played back in ${PLAY_MS / 1000} seconds.`}
      />
      <p className="cl-honest">
        <span>Simulated audience:</span> a model-based prediction from the evaluation panel, not platform data.
      </p>
      {ready ? (
        <Launch lab={lab} sims={sims} viewers={viewers} />
      ) : (
        <div className="cl-waiting">
          <p>The launch is simulated from the audience panel's reactions once the reels have been screened.</p>
        </div>
      )}
    </section>
  );
}

function niceFollowers(v: number) {
  const f = 1000 * Math.pow(100, v / 100);
  const mag = Math.pow(10, Math.floor(Math.log10(f)) - 1);
  return Math.round(f / mag) * mag;
}
const sliderOf = (followers: number) => Math.max(0, Math.min(100, (Math.log(followers / 1000) / Math.log(100)) * 100));

function Launch({ lab, sims, viewers }: { lab: LabState; sims: Simulation[]; viewers: ViewerPersona[] }) {
  const simulate = useCreator((s) => s.simulate);
  const simBusy = useCreator((s) => s.simBusy);
  const simRun = useCreator((s) => s.simRun);
  const reduced = useReducedMotion();
  const hours = sims[0]?.hours ?? lab.options.hours;
  const clock = useLaunchClock(hours);
  const [followers, setFollowers] = useState(sims[0]?.followers ?? lab.options.followers);
  const [seed, setSeed] = useState(sims[0]?.seed ?? lab.options.seed);
  const [played, setPlayed] = useState(false);

  const reels = useMemo(() => sims.map((s) => lab.reels.find((r) => r.id === s.reel)).filter((r): r is Reel => !!r), [sims, lab.reels]);
  const dirty = followers !== (sims[0]?.followers ?? followers) || seed !== (sims[0]?.seed ?? seed);

  // new simulations (a re-run, or a lab that just finished) play from hour 0
  const firstRun = useRef(simRun);
  useEffect(() => {
    setFollowers(sims[0]?.followers ?? lab.options.followers);
    setSeed(sims[0]?.seed ?? lab.options.seed);
    if (simRun === firstRun.current) return;
    setPlayed(true);
    if (reduced) clock.seek(hours);
    else clock.play(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simRun]);

  const rerun = () => void simulate({ followers, seed, hours });
  const go = () => {
    if (dirty) {
      rerun();
      return;
    }
    setPlayed(true);
    if (reduced) clock.seek(hours);
    else if (clock.playing) clock.pause();
    else clock.play();
  };

  const hour = clock.hour;
  const done = hour >= hours - 1e-6;
  const at = useMemo(() => new Map(sims.map((s) => [s.reel, interpolateSeries(s.series, hour)])), [sims, hour]);
  const leader = hour > 0 ? [...at.entries()].sort((a, b) => b[1].views - a[1].views)[0] : undefined;
  const winner = useMemo(() => launchOutcome(sims, reels, "views")?.winner ?? null, [sims, reels]);
  const lead = leader?.[0] ?? winner;
  const inks = useMemo(() => reelInks(reels, lead), [reels, lead]);
  const finalInks = useMemo(() => reelInks(reels, winner), [reels, winner]);

  return (
    <div className="cl-launch">
      <div className="cl-sim-bar">
        <button className="btn btn-primary cl-launch-go" onClick={go} disabled={simBusy}>
          {simBusy ? (
            <>
              <span className="spinner spinner-sm" aria-hidden /> Simulating…
            </>
          ) : dirty ? (
            <>
              <RotateCcw size={13} aria-hidden /> Simulate these settings
            </>
          ) : clock.playing ? (
            <>
              <Pause size={13} aria-hidden /> Pause
            </>
          ) : (
            <>
              <Play size={13} aria-hidden /> {done && played ? "Replay launch" : hour > 0 ? "Resume" : "Simulate launch"}
            </>
          )}
        </button>
        <button className="icon-btn" onClick={() => (setPlayed(true), clock.seek(hours))} aria-label="Skip to the end" title="Skip to the end" disabled={done}>
          <FastForward size={15} />
        </button>
        <p className="cl-clock-read" aria-live="off">
          Hour <span className="num">{hour < 10 ? hour.toFixed(1) : String(Math.floor(hour))}</span>
          <span className="cl-clock-of">
            {" "}
            / <span className="num">{hours}</span>
          </span>
        </p>
        <input
          type="range"
          className="cl-scrub"
          min={0}
          max={hours}
          step={0.1}
          value={hour}
          onChange={(e) => (setPlayed(true), clock.seek(Number(e.target.value)))}
          aria-label="Launch clock, hours"
          style={{ "--p": `${(hour / hours) * 100}%` } as CSSProperties}
        />
        <LaunchSettings followers={followers} seed={seed} onFollowers={setFollowers} onSeed={setSeed} onRun={rerun} busy={simBusy} />
      </div>

      <Banner sims={sims} reels={reels} hours={hours} done={done && played} leader={leader} hour={hour} />
      <Counters reels={reels} at={at} leader={leader?.[0]} final={done && played} />
      <div className="cl-sim-main">
        <Growth sims={sims} inks={inks} lead={lead} hour={hour} hours={hours} />
        <Comments sims={sims} hour={hour} />
      </div>
      <Disclosure className="cl-sim-more" label="Retention, audience segments, all metrics and how the simulation works" openLabel="Hide the details">
        <div className="cl-launch-more">
          <Retention sims={sims} inks={finalInks} />
          <Segments sims={sims} hour={hour} viewers={viewers} />
          <Metrics reels={reels} at={at} />
          <ModelNotes sims={sims} />
        </div>
      </Disclosure>
    </div>
  );
}

// ---- followers and seed, behind "Change" ----------------------------------------------------------------

function LaunchSettings({
  followers,
  seed,
  onFollowers,
  onSeed,
  onRun,
  busy,
}: {
  followers: number;
  seed: number;
  onFollowers: (n: number) => void;
  onSeed: (n: number) => void;
  onRun: () => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, close, wrap);
  const slider = sliderOf(followers);
  return (
    <div className="cl-sim-settings" ref={wrap}>
      <span className="cl-sim-settings-read">
        <span className="num">{fmtInt(followers)}</span> followers · seed <span className="num">{seed}</span>
      </span>
      <button className="btn btn-sm" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="dialog">
        Change
      </button>
      {open && (
        <div className="cl-pop cl-sim-pop" role="dialog" aria-label="Launch settings">
          <label className="cl-sim-field">
            <span className="cl-sim-field-head">
              <span>Followers</span>
              <span className="num">{fmtInt(followers)}</span>
            </span>
            <input
              type="range"
              className="cl-scrub"
              min={0}
              max={100}
              step={1}
              value={slider}
              onChange={(e) => onFollowers(niceFollowers(Number(e.target.value)))}
              aria-valuetext={`${fmtInt(followers)} followers`}
              style={{ "--p": `${slider}%` } as CSSProperties}
            />
          </label>
          <div className="cl-sim-field cl-sim-field-row">
            <span>Seed</span>
            <span className="cl-sim-seed">
              <span className="num">{seed}</span>
              <button className="icon-btn" onClick={() => onSeed(1 + Math.floor(Math.random() * 9999))} aria-label="Shuffle the seed" title="Shuffle the seed">
                <Dices size={14} />
              </button>
            </span>
          </div>
          <button
            className="btn btn-sm btn-primary cl-sim-rerun"
            onClick={() => {
              setOpen(false);
              onRun();
            }}
            disabled={busy}
          >
            <RotateCcw size={12} aria-hidden /> Re-run
          </button>
        </div>
      )}
    </div>
  );
}

// ---- the result in one sentence -----------------------------------------------------------------------

function Banner({
  sims,
  reels,
  hours,
  done,
  leader,
  hour,
}: {
  sims: Simulation[];
  reels: Reel[];
  hours: number;
  done: boolean;
  leader: [string, { views: number }] | undefined;
  hour: number;
}) {
  const views = launchOutcome(sims, reels, "views");
  const inquiries = launchOutcome(sims, reels, "inquiries");
  const reel = (id: string | null) => reels.find((r) => r.id === id);
  if (!done || !views) {
    return (
      <div className="cl-banner" data-state="race" aria-live="polite">
        {hour > 0 && leader ? (
          <p className="cl-banner-race">
            <ReelBadge id={leader[0]} size={20} />
            <span>
              <b>{reel(leader[0])?.label ?? leader[0]}</b> leads at hour {Math.floor(hour)} with <span className="num">{fmtInt(leader[1].views)}</span> views
            </span>
          </p>
        ) : (
          <p className="cl-banner-hint">
            Press Simulate launch to play {hours} hours in {PLAY_MS / 1000} seconds; the counters, the curve and the comments move with the clock.
          </p>
        )}
      </div>
    );
  }
  const w = reel(views.winner);
  const vs = reel(views.against);
  const name = w?.title ?? views.winner;
  const against = vs?.kind === "baseline" ? "the template" : vs ? `${vs.id} · ${vs.label}` : (views.against ?? "");
  const inq = inquiries && inquiries.winner === views.winner && inquiries.against === views.against ? inquiries : null;
  const sim = sims[0]!;
  return (
    <div className="cl-banner" data-state="won" role="status">
      <p className="cl-banner-head">
        <ReelBadge id={views.winner} size={24} />
        <span>
          {views.tie ? (
            <>
              {name} and {reel(views.ranking[1]!.reel)?.title ?? views.ranking[1]!.reel} tied on views
            </>
          ) : views.ratio != null ? (
            <>
              {name} reached <b className="cl-banner-x">{formatRatio(views.ratio)}</b> {vs?.kind === "baseline" ? "the template's views" : `the views of ${against}`}
            </>
          ) : (
            <>
              {name} reached <span className="num">{fmtInt(views.winnerValue)}</span> views; {against} reached none
            </>
          )}
        </span>
      </p>
      <p className="cl-banner-sub">
        <span className="num">{fmtInt(views.winnerValue)}</span> views vs <span className="num">{fmtInt(views.againstValue)}</span>
        {inq && (
          <>
            {" · "}
            {inq.ratio != null ? (
              <>
                <span className="num">{formatRatio(inq.ratio)}</span> the inquiries (<span className="num">{fmtInt(inq.winnerValue)}</span> vs{" "}
                <span className="num">{fmtInt(inq.againstValue)}</span>)
              </>
            ) : (
              <>
                <span className="num">{fmtInt(inq.winnerValue)}</span> {inq.winnerValue === 1 ? "inquiry" : "inquiries"} vs none
              </>
            )}
          </>
        )}
        {" · "}simulated: <span className="num">{fmtInt(sim.followers)}</span> followers, <span className="num">{sim.hours}</span> h, seed{" "}
        <span className="num">{sim.seed}</span>
      </p>
    </div>
  );
}

// ---- counters -----------------------------------------------------------------------------------------

const count = (n: number, one: string, many: string) => (
  <>
    <span className="num">{fmtCompact(n)}</span> {Math.round(n) === 1 ? one : many}
  </>
);

function Counters({ reels, at, leader, final }: { reels: Reel[]; at: Map<string, SimPoint>; leader?: string; final: boolean }) {
  return (
    <div className="cl-counters" style={{ "--n": reels.length } as CSSProperties}>
      {reels.map((r) => {
        const p = at.get(r.id);
        const lead = leader === r.id;
        return (
          <article key={r.id} className="cl-counter" data-leading={lead || undefined}>
            <header className="cl-counter-head">
              <ReelBadge id={r.id} size={18} />
              <span className="cl-counter-label">{r.label}</span>
              {lead && <span className="cl-counter-lead">{final ? "Winner" : "Leading"}</span>}
            </header>
            <p className="cl-counter-views">
              <span className="num">{fmtInt(p?.views ?? 0)}</span>
              <span className="cl-counter-unit">views</span>
            </p>
            <p className="cl-counter-sub">
              {count(p?.shares ?? 0, "share", "shares")} · {count(p?.inquiries ?? 0, "inquiry", "inquiries")}
            </p>
          </article>
        );
      })}
    </div>
  );
}

// ---- growth chart -------------------------------------------------------------------------------------

function Growth({ sims, inks, lead, hour, hours }: { sims: Simulation[]; inks: Record<string, string>; lead: string | null; hour: number; hours: number }) {
  const [box, W] = useWidth<HTMLDivElement>(760);
  const H = 270;
  const L = 44;
  const R = 78;
  const T = 10;
  const B = 26;
  const ticksY = niceTicks(Math.max(...sims.map((s) => s.totals.views), 1), 3);
  const top = ticksY[ticksY.length - 1]!;
  const x = (h: number) => L + (h / hours) * (W - L - R);
  const y = (v: number) => T + (1 - v / top) * (H - T - B);
  const [hoverH, setHoverH] = useState<number | null>(null);
  const ticksX = [0, 6, 12, 24, 36, 48].filter((h) => h < hours);
  ticksX.push(hours);

  // end labels sit in a right gutter, spaced so they never overlap; leader lines join them to their dots
  const ends = sims
    .map((s) => ({ id: s.reel, v: interpolateSeries(s.series, hour).views }))
    .sort((a, b) => b.v - a.v)
    .map((e) => ({ ...e, y: y(e.v), ly: y(e.v) }));
  for (let i = 1; i < ends.length; i++) ends[i]!.ly = Math.max(ends[i]!.ly, ends[i - 1]!.ly + 16);
  const overflow = (ends[ends.length - 1]?.ly ?? 0) - (H - B);
  if (overflow > 0) ends.forEach((e) => (e.ly -= overflow));
  // the leader draws last, on top
  const order = [...sims].sort((a, b) => Number(a.reel === lead) - Number(b.reel === lead));

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const h = Math.round(((e.clientX - r.left - L) / (W - L - R)) * hours);
    setHoverH(h >= 0 && h <= Math.floor(hour) ? h : null);
  };

  return (
    <div className="cl-growth">
      <h3 className="cl-h3">Views over time</h3>
      <div className="cl-growth-plot" ref={box}>
        <svg width={W} height={H} onPointerMove={onMove} onPointerLeave={() => setHoverH(null)} role="img" aria-label="Cumulative views per reel by hour">
          <defs>
            <clipPath id="cl-growth-clip">
              <rect x={0} y={0} width={Math.max(0, x(hour) + 1)} height={H} />
            </clipPath>
          </defs>
          {ticksY.map((v) => (
            <g key={v} className="cl-grid">
              <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} />
              <text x={L - 10} y={y(v) + 4} textAnchor="end">
                {fmtCompact(v)}
              </text>
            </g>
          ))}
          {ticksX.map((h) => (
            <text key={h} className="cl-axis" x={x(h)} y={H - 6} textAnchor="middle">
              {h}h
            </text>
          ))}
          <g clipPath="url(#cl-growth-clip)">
            {order.map((s) => (
              <polyline key={s.reel} className="cl-growth-line" points={s.series.map((p) => `${x(p.hour).toFixed(1)},${y(p.views).toFixed(1)}`).join(" ")} style={{ stroke: inks[s.reel] }} />
            ))}
          </g>
          {hour > 0 && <line className="cl-growth-now" x1={x(hour)} x2={x(hour)} y1={T} y2={H - B} />}
          {hour > 0 &&
            ends.map((e) => (
              <g key={e.id}>
                {Math.abs(e.ly - e.y) > 3 && <polyline className="cl-growth-leader" points={`${x(hour) + 6},${e.y} ${x(hour) + 11},${e.ly}`} />}
                <circle cx={x(hour)} cy={e.y} r={3.5} style={{ fill: inks[e.id] }} />
                <text x={x(hour) + 14} y={e.ly + 4} className="cl-growth-end" style={{ fill: inks[e.id] }}>
                  <tspan className="cl-growth-end-id">{e.id}</tspan> {fmtCompact(e.v)}
                </text>
              </g>
            ))}
          {hoverH != null && <line className="cl-growth-cross" x1={x(hoverH)} x2={x(hoverH)} y1={T} y2={H - B} />}
        </svg>
        {hoverH != null && (
          <div className="cl-tip cl-growth-tip" style={{ left: x(hoverH) }} data-flip={x(hoverH) > W * 0.6 || undefined}>
            <p className="cl-growth-tip-h num">Hour {hoverH}</p>
            {[...sims]
              .map((s) => ({ id: s.reel, v: interpolateSeries(s.series, hoverH).views }))
              .sort((a, b) => b.v - a.v)
              .map((e) => (
                <p key={e.id} className="cl-growth-tip-row">
                  <i style={{ background: inks[e.id] }} />
                  <span>{e.id}</span>
                  <b className="num">{fmtInt(e.v)}</b>
                </p>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- comments -------------------------------------------------------------------------------------------

function Comments({ sims, hour }: { sims: Simulation[]; hour: number }) {
  const list = useMemo(() => {
    const byReel = new Map(sims.map((s) => [s.reel, s]));
    return visibleComments(sims, hour).map((c) => {
      const s = byReel.get(c.reel);
      if (!s) return c;
      const at = interpolateSeries(s.series, c.hour).views;
      const later = s.totals.views - at;
      const f = later > 0 ? Math.min(1, Math.max(0, (interpolateSeries(s.series, hour).views - at) / later)) : 1;
      return { ...c, likes: Math.round(c.likes * f) };
    });
  }, [sims, hour]);
  const total = sims.reduce((n, s) => n + s.comments.length, 0);
  return (
    <aside className="cl-comments" aria-label="Comments as they are posted">
      <div className="cl-comments-head">
        <h3 className="cl-h3">Comments</h3>
        <span className="cl-comments-count num">
          {list.length} of {total}
        </span>
      </div>
      {list.length === 0 ? (
        <p className="cl-empty-line">Comments appear as the clock passes the hour they were posted. They are the viewer agents' own words.</p>
      ) : (
        <ol className="cl-comment-list" aria-live="polite">
          {list.map((c) => (
            <li key={c.key} className="cl-comment">
              <p className="cl-comment-head">
                <b>{c.name}</b>
                <ReelBadge id={c.reel} size={16} />
                <span className="cl-comment-h num">{c.hour.toFixed(1)} h</span>
              </p>
              <p className="cl-comment-text">{c.text}</p>
              {c.likes > 0 && (
                <p className="cl-comment-likes">
                  <span className="num">{fmtInt(c.likes)}</span> {c.likes === 1 ? "like" : "likes"}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}

// ---- retention ------------------------------------------------------------------------------------------

function Retention({ sims, inks }: { sims: Simulation[]; inks: Record<string, string> }) {
  const [box, W] = useWidth<HTMLDivElement>(420);
  const H = 200;
  const L = 40;
  const R = 58;
  const T = 10;
  const B = 24;
  const maxS = Math.max(...sims.map((s) => s.retention[s.retention.length - 1]?.second ?? 0), 1);
  const x = (s: number) => L + (s / maxS) * (W - L - R);
  const y = (f: number) => T + (1 - f) * (H - T - B);
  const ends = sims
    .map((s) => {
      const last = s.retention[s.retention.length - 1];
      return { id: s.reel, x: x(last?.second ?? 0), y: y(last?.fraction ?? 0), ly: y(last?.fraction ?? 0), f: last?.fraction ?? 0 };
    })
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) ends[i]!.ly = Math.max(ends[i]!.ly, ends[i - 1]!.ly + 15);
  return (
    <div className="cl-launch-block cl-retention">
      <h3 className="cl-h3">Still watching, second by second</h3>
      <div className="cl-retention-plot" ref={box}>
        <svg width={W} height={H} role="img" aria-label={`Share of impressions still watching: ${ends.map((e) => `${e.id} ${fmtPct(e.f)} at the end`).join(", ")}`}>
          {[0, 0.5, 1].map((f) => (
            <g key={f} className="cl-grid">
              <line x1={L} x2={W - R} y1={y(f)} y2={y(f)} />
              <text x={L - 8} y={y(f) + 4} textAnchor="end">
                {fmtPct(f)}
              </text>
            </g>
          ))}
          <line className="cl-ret-hook" x1={x(1.5)} x2={x(1.5)} y1={T} y2={H - B} />
          <text className="cl-axis" x={x(1.5) + 5} y={H - B - 7}>
            1.5 s hook
          </text>
          {[0, 5, 10, 15, 20, 25, 30]
            .filter((s) => s <= maxS)
            .map((s) => (
              <text key={s} className="cl-axis" x={x(s)} y={H - 6} textAnchor="middle">
                {s}s
              </text>
            ))}
          {sims.map((s) => (
            <polyline key={s.reel} className="cl-growth-line" points={s.retention.map((p) => `${x(p.second).toFixed(1)},${y(p.fraction).toFixed(1)}`).join(" ")} style={{ stroke: inks[s.reel] }} />
          ))}
          {ends.map((e) => (
            <g key={e.id}>
              <circle cx={e.x} cy={e.y} r={3} style={{ fill: inks[e.id] }} />
              <text x={W - R + 10} y={e.ly + 4} className="cl-growth-end" style={{ fill: inks[e.id] }}>
                <tspan className="cl-growth-end-id">{e.id}</tspan> {fmtPct(e.f)}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <p className="cl-foot">Share of everyone shown the reel. Most of the loss is in the first 1.5 seconds.</p>
    </div>
  );
}

// ---- segments -------------------------------------------------------------------------------------------

function Segments({ sims, hour, viewers }: { sims: Simulation[]; hour: number; viewers: ViewerPersona[] }) {
  const now = useMemo(() => sims.map((s) => ({ id: s.reel, seg: segmentsAt(s, hour) })), [sims, hour]);
  // the six segments that watch most across all reels, the rest folded into "Everyone else"
  const order = useMemo(() => {
    const total = new Map<string, number>();
    for (const s of sims) for (const [k, v] of Object.entries(s.by_segment)) total.set(k, (total.get(k) ?? 0) + v);
    return [...total.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  }, [sims]);
  const top = order.slice(0, 6);
  const rest = order.slice(6);
  const rows = [
    ...top.map((id) => ({ id, v: viewers.find((x) => x.id === id), vals: now.map((n) => n.seg[id] ?? 0) })),
    ...(rest.length ? [{ id: "rest", v: undefined, vals: now.map((n) => rest.reduce((a, k) => a + (n.seg[k] ?? 0), 0)) }] : []),
  ];
  return (
    <div className="cl-launch-block">
      <h3 className="cl-h3">Who watched · views by audience segment</h3>
      <table className="cl-table cl-launch-table">
        <thead>
          <tr>
            <th scope="col">Viewer</th>
            {now.map((n) => (
              <th key={n.id} scope="col">
                <ReelBadge id={n.id} size={18} />
                <span className="sr-only">Reel {n.id}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const best = Math.max(...row.vals);
            return (
              <tr key={row.id}>
                <th scope="row">
                  <span className="cl-seg-name">{row.v?.name ?? "Everyone else"}</span>
                  <span className="cl-seg-kind">{row.v?.segment ?? `${rest.length} more segments`}</span>
                </th>
                {row.vals.map((v, i) => (
                  <td key={now[i]!.id} className="num" data-best={(v === best && v > 0) || undefined}>
                    {fmtInt(v)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---- every metric -----------------------------------------------------------------------------------------

const METRIC_LABEL: Record<Metric, string> = {
  views: "Views",
  likes: "Likes",
  comments: "Comments",
  shares: "Shares",
  saves: "Saves",
  follows: "Follows",
  inquiries: "Inquiries",
};

function Metrics({ reels, at }: { reels: Reel[]; at: Map<string, SimPoint> }) {
  return (
    <div className="cl-launch-block">
      <h3 className="cl-h3">Every metric at this hour</h3>
      <table className="cl-table cl-launch-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Metric</span>
            </th>
            {reels.map((r) => (
              <th key={r.id} scope="col">
                <ReelBadge id={r.id} size={18} />
                <span className="sr-only">{r.label}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {METRICS.map((m) => {
            const vals = reels.map((r) => at.get(r.id)?.[m] ?? 0);
            const best = Math.max(...vals);
            return (
              <tr key={m}>
                <th scope="row">{METRIC_LABEL[m]}</th>
                {vals.map((v, i) => (
                  <td key={reels[i]!.id} className="num" data-best={(v === best && v > 0) || undefined}>
                    {fmtInt(v)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---- the model's assumptions --------------------------------------------------------------------------

function ModelNotes({ sims }: { sims: Simulation[] }) {
  const shared = (sims[0]?.notes ?? []).filter((n) => sims.every((s) => s.notes.includes(n)));
  const own = sims.map((s) => ({ id: s.reel, notes: s.notes.filter((n) => !shared.includes(n)) })).filter((x) => x.notes.length);
  if (!shared.length && !own.length) return null;
  return (
    <section className="cl-launch-block cl-notes" aria-labelledby="cl-notes-title">
      <h3 id="cl-notes-title" className="cl-h3">
        How the simulation works
      </h3>
      <ol className="cl-notes-list">
        {shared.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ol>
      {own.length > 0 && (
        <ul className="cl-notes-own">
          {own.map((x) => (
            <li key={x.id}>
              <ReelBadge id={x.id} size={16} /> <span>{x.notes.join(" ")}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
