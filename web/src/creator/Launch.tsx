import { Bookmark, Dices, FastForward, Heart, Info, Mail, MessageCircle, Pause, Play, RotateCcw, Send, Trophy, UserPlus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { plannedReels } from "./Reels";
import { clockHour, formatRatio, interpolateSeries, launchOutcome, segmentsAt, visibleComments, type Metric } from "./sim";
import { useCreator } from "./store";
import type { LabState, Reel, Simulation, ViewerPersona } from "./types";
import { Avatar, fmtCompact, fmtInt, fmtPct, niceTicks, ReelBadge, reelColors, reelStyle, SectionHead, useReducedMotion, useWidth, viewerWho } from "./ui";

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
        n="04"
        id="cl-launch-title"
        title="Simulated launch"
        sub={`Which reel would travel further? ${sims[0]?.hours ?? lab.options.hours} hours on an account with ${fmtInt(sims[0]?.followers ?? lab.options.followers)} followers, played back in ${PLAY_MS / 1000} seconds.`}
      />
      <p className="cl-honest">
        <Info size={14} aria-hidden />
        <span>
          <b>Simulated audience:</b> a model-based prediction from the evaluation panel, not platform data.
        </span>
      </p>
      {ready ? (
        <Launch lab={lab} sims={sims} viewers={viewers} />
      ) : (
        <div className="cl-waiting">
          <Trophy size={18} strokeWidth={1.6} aria-hidden />
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

  const planned = useMemo(() => plannedReels(lab), [lab]);
  const colors = useMemo(() => reelColors(planned.map((p) => ({ id: p.id, kind: p.kind }))), [planned]);
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

  const go = () => {
    if (dirty) {
      void simulate({ followers, seed, hours });
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
  const leader = [...at.entries()].sort((a, b) => b[1].views - a[1].views)[0];

  return (
    <div className="cl-launch">
      <div className="cl-launch-bar">
        <div className="cl-launch-play">
          <button className="btn btn-agent cl-launch-go" onClick={go} disabled={simBusy}>
            {simBusy ? (
              <>
                <span className="spinner spinner-sm" aria-hidden /> Simulating…
              </>
            ) : dirty ? (
              <>
                <RotateCcw size={13} aria-hidden /> Simulate with these settings
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
        </div>
        <div className="cl-clock">
          <p className="cl-clock-read num" aria-live="off">
            <span className="cl-clock-k">Hour</span> {hour < 10 ? hour.toFixed(1) : String(Math.floor(hour))}
            <span className="cl-clock-of"> / {hours}</span>
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
        </div>
        <div className="cl-launch-settings">
          <label className="cl-follow">
            <span>
              Followers <b className="num">{fmtInt(followers)}</b>
            </span>
            <input type="range" min={0} max={100} step={1} value={sliderOf(followers)} onChange={(e) => setFollowers(niceFollowers(Number(e.target.value)))} aria-valuetext={`${fmtInt(followers)} followers`} />
          </label>
          <div className="cl-seed">
            <span>
              Seed <b className="num">{seed}</b>
            </span>
            <button className="btn btn-sm" onClick={() => setSeed(1 + Math.floor(Math.random() * 9999))} aria-label="Shuffle the seed">
              <Dices size={13} aria-hidden /> Shuffle
            </button>
          </div>
          <button className="btn btn-sm" onClick={() => void simulate({ followers, seed, hours })} disabled={simBusy}>
            <RotateCcw size={12} aria-hidden /> Re-run
          </button>
        </div>
      </div>

      <Banner sims={sims} reels={reels} colors={colors} done={done && played} leader={leader} hour={hour} />

      <div className="cl-launch-grid">
        <div className="cl-launch-main">
          <Counters reels={reels} colors={colors} at={at} leader={hour > 0 ? leader?.[0] : undefined} final={done && played} />
          <Growth sims={sims} colors={colors} hour={hour} hours={hours} />
          <div className="cl-launch-pair">
            <Retention sims={sims} colors={colors} />
            <Segments sims={sims} colors={colors} hour={hour} viewers={viewers} />
          </div>
        </div>
        <Comments sims={sims} colors={colors} hour={hour} viewers={viewers} />
      </div>
      <ModelNotes sims={sims} colors={colors} />
    </div>
  );
}

// ---- winner banner ------------------------------------------------------------------------------------

function Banner({
  sims,
  reels,
  colors,
  done,
  leader,
  hour,
}: {
  sims: Simulation[];
  reels: Reel[];
  colors: Record<string, string>;
  done: boolean;
  leader: [string, { views: number }] | undefined;
  hour: number;
}) {
  const views = launchOutcome(sims, reels, "views");
  const inquiries = launchOutcome(sims, reels, "inquiries");
  const label = (id: string | null) => reels.find((r) => r.id === id);
  if (!done || !views) {
    return (
      <div className="cl-banner" data-state="race" aria-live="polite">
        {hour > 0 && leader ? (
          <p>
            <ReelBadge id={leader[0]} color={colors[leader[0]]!} size={20} /> <b>{label(leader[0])?.label}</b> leads at hour {Math.floor(hour)} with{" "}
            <span className="num">{fmtInt(leader[1].views)}</span> views
          </p>
        ) : (
          <p className="muted">
            <Play size={13} aria-hidden /> Press Simulate launch to play {sims[0]?.hours ?? 48} hours in {PLAY_MS / 1000} seconds. Counters, curves and comments move with the clock.
          </p>
        )}
      </div>
    );
  }
  const w = label(views.winner);
  const vs = label(views.against);
  const inq = inquiries && inquiries.winner === views.winner && inquiries.against === views.against ? inquiries : null;
  return (
    <div className="cl-banner" data-state="won" style={reelStyle(colors[views.winner]!)} role="status">
      <Trophy size={26} aria-hidden className="cl-banner-icon" />
      <div className="cl-banner-text">
        <p className="cl-banner-head">
          <ReelBadge id={views.winner} color={colors[views.winner]!} size={28} />
          <span>
            {w?.title ?? views.winner} reached{" "}
            <b className="cl-banner-x">{views.ratio != null ? formatRatio(views.ratio) : "more"}</b> the views of {vs ? `${vs.id} · ${vs.label}` : views.against}
          </span>
        </p>
        <p className="cl-banner-sub num">
          {fmtInt(views.winnerValue)} views vs {fmtInt(views.againstValue)}
          {inq && (
            <>
              {" · "}
              {inq.ratio != null ? `${formatRatio(inq.ratio)} the inquiries (${fmtInt(inq.winnerValue)} vs ${fmtInt(inq.againstValue)})` : `${fmtInt(inq.winnerValue)} inquiries vs none`}
            </>
          )}
          {" · "}simulated, {fmtInt(sims[0]!.followers)} followers, {sims[0]!.hours} h, seed {sims[0]!.seed}
        </p>
      </div>
      <ol className="cl-banner-rank" aria-label="Views after the launch">
        {views.ranking.map((r, i) => (
          <li key={r.reel}>
            <span className="cl-banner-place num">{i + 1}</span>
            <ReelBadge id={r.reel} color={colors[r.reel]!} size={20} />
            <b className="num">{fmtCompact(r.value)}</b>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ---- counters -----------------------------------------------------------------------------------------

const COUNTS: [Exclude<Metric, "views">, string, typeof Heart][] = [
  ["likes", "Likes", Heart],
  ["comments", "Comments", MessageCircle],
  ["shares", "Shares", Send],
  ["saves", "Saves", Bookmark],
  ["follows", "Follows", UserPlus],
  ["inquiries", "Inquiries", Mail],
];

function Counters({
  reels,
  colors,
  at,
  leader,
  final,
}: {
  reels: Reel[];
  colors: Record<string, string>;
  at: Map<string, ReturnType<typeof interpolateSeries>>;
  leader?: string;
  final: boolean;
}) {
  return (
    <div className="cl-counters" style={{ "--n": reels.length } as CSSProperties}>
      {reels.map((r) => {
        const p = at.get(r.id);
        return (
          <article key={r.id} className="cl-counter" style={reelStyle(colors[r.id]!)} data-leading={leader === r.id || undefined}>
            <header>
              <ReelBadge id={r.id} color={colors[r.id]!} size={22} />
              <span className="cl-counter-label">{r.label}</span>
              {leader === r.id && <span className="cl-counter-lead">{final ? "Winner" : "Leading"}</span>}
            </header>
            <p className="cl-counter-views">
              <span className="num">{fmtInt(p?.views ?? 0)}</span>
              <span className="cl-counter-unit">views</span>
            </p>
            <dl className="cl-counter-grid">
              {COUNTS.map(([k, label, Icon]) => (
                <div key={k} title={label}>
                  <dt>
                    <Icon size={12} aria-hidden />
                    <span className="sr-only">{label}</span>
                  </dt>
                  <dd className="num">{fmtCompact(p?.[k] ?? 0)}</dd>
                </div>
              ))}
            </dl>
          </article>
        );
      })}
    </div>
  );
}

// ---- growth chart -------------------------------------------------------------------------------------

function Growth({ sims, colors, hour, hours }: { sims: Simulation[]; colors: Record<string, string>; hour: number; hours: number }) {
  const [box, W] = useWidth<HTMLDivElement>(760);
  const H = 250;
  const L = 46;
  const R = 84;
  const T = 12;
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
  for (let i = 1; i < ends.length; i++) ends[i]!.ly = Math.max(ends[i]!.ly, ends[i - 1]!.ly + 17);
  const overflow = (ends[ends.length - 1]?.ly ?? 0) - (H - B);
  if (overflow > 0) ends.forEach((e) => (e.ly -= overflow));

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const h = Math.round(((e.clientX - r.left - L) / (W - L - R)) * hours);
    setHoverH(h >= 0 && h <= Math.floor(hour) ? h : null);
  };

  return (
    <div className="cl-panel cl-growth">
      <div className="cl-panel-head">
        <h3 className="cl-label">Views over time</h3>
        <span className="cl-legend">
          {sims.map((s) => (
            <span key={s.reel}>
              <i style={{ background: colors[s.reel] }} /> {s.reel}
            </span>
          ))}
        </span>
      </div>
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
            {sims.map((s) => (
              <polyline key={s.reel} className="cl-growth-line" points={s.series.map((p) => `${x(p.hour).toFixed(1)},${y(p.views).toFixed(1)}`).join(" ")} style={{ stroke: colors[s.reel] }} />
            ))}
          </g>
          {hour > 0 && <line className="cl-growth-now" x1={x(hour)} x2={x(hour)} y1={T} y2={H - B} />}
          {hour > 0 &&
            ends.map((e) => (
              <g key={e.id}>
                {Math.abs(e.ly - e.y) > 3 && <polyline className="cl-growth-leader" points={`${x(hour) + 6},${e.y} ${x(hour) + 11},${e.ly}`} />}
                <circle cx={x(hour)} cy={e.y} r={4.5} className="cl-growth-dot" style={{ fill: colors[e.id] }} />
                <text x={x(hour) + 14} y={e.ly + 4} className="cl-growth-end">
                  <tspan className="cl-growth-end-id">{e.id}</tspan> {fmtCompact(e.v)}
                </text>
              </g>
            ))}
          {hoverH != null && <line className="cl-growth-cross" x1={x(hoverH)} x2={x(hoverH)} y1={T} y2={H - B} />}
        </svg>
        {hoverH != null && (
          <div className="cl-growth-tip" style={{ left: x(hoverH) }} data-flip={x(hoverH) > W * 0.6 || undefined}>
            <p className="cl-growth-tip-h num">Hour {hoverH}</p>
            {[...sims]
              .map((s) => ({ id: s.reel, v: interpolateSeries(s.series, hoverH).views }))
              .sort((a, b) => b.v - a.v)
              .map((e) => (
                <p key={e.id} className="cl-growth-tip-row">
                  <i style={{ background: colors[e.id] }} />
                  <b className="num">{fmtInt(e.v)}</b> <span>{e.id}</span>
                </p>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- retention ------------------------------------------------------------------------------------------

function Retention({ sims, colors }: { sims: Simulation[]; colors: Record<string, string> }) {
  const [box, W] = useWidth<HTMLDivElement>(360);
  const H = 196;
  const L = 40;
  const R = 58;
  const T = 12;
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
    <div className="cl-panel cl-retention">
      <div className="cl-panel-head">
        <h3 className="cl-label">Still watching, second by second</h3>
      </div>
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
            <polyline key={s.reel} className="cl-growth-line" points={s.retention.map((p) => `${x(p.second).toFixed(1)},${y(p.fraction).toFixed(1)}`).join(" ")} style={{ stroke: colors[s.reel] }} />
          ))}
          {ends.map((e) => (
            <g key={e.id}>
              <circle cx={e.x} cy={e.y} r={3.5} style={{ fill: colors[e.id] }} className="cl-growth-dot" />
              <text x={W - R + 10} y={e.ly + 4} className="cl-growth-end">
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

function Segments({ sims, colors, hour, viewers }: { sims: Simulation[]; colors: Record<string, string>; hour: number; viewers: ViewerPersona[] }) {
  const now = useMemo(() => sims.map((s) => ({ id: s.reel, seg: segmentsAt(s, hour) })), [sims, hour]);
  const finals = useMemo(() => sims.map((s) => s.by_segment), [sims]);
  // the six segments that watch most across all reels, the rest folded into "Everyone else"
  const order = useMemo(() => {
    const total = new Map<string, number>();
    for (const f of finals) for (const [k, v] of Object.entries(f)) total.set(k, (total.get(k) ?? 0) + v);
    return [...total.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  }, [finals]);
  const top = order.slice(0, 6);
  const rest = order.slice(6);
  const rows = [
    ...top.map((id) => ({ id, v: viewers.find((x) => x.id === id), vals: now.map((n) => n.seg[id] ?? 0) })),
    ...(rest.length ? [{ id: "rest", v: undefined, vals: now.map((n) => rest.reduce((a, k) => a + (n.seg[k] ?? 0), 0)) }] : []),
  ];
  const max = Math.max(1, ...finals.flatMap((f) => [...top.map((k) => f[k] ?? 0), rest.reduce((a, k) => a + (f[k] ?? 0), 0)]));
  return (
    <div className="cl-panel cl-segments">
      <div className="cl-panel-head">
        <h3 className="cl-label">Who watched · views by audience segment</h3>
      </div>
      <ul className="cl-seg-rows">
        {rows.map((row) => {
          const best = Math.max(...row.vals);
          return (
            <li key={row.id}>
              <span className="cl-seg-who" title={row.v?.segment ?? `${rest.length} more segments`}>
                {row.v ? <Avatar who={viewerWho(row.v)} size={20} /> : <span className="cl-seg-rest" aria-hidden>+{rest.length}</span>}
                <span>{row.v?.name ?? "Everyone else"}</span>
              </span>
              <span className="cl-seg-bars">
                {row.vals.map((v, i) => (
                  <span key={now[i]!.id} className="cl-seg-bar" style={{ width: `${(v / max) * 100}%`, background: colors[now[i]!.id] }} title={`${now[i]!.id}: ${fmtInt(v)} views`} />
                ))}
              </span>
              <span className="cl-seg-best num">{best > 0 ? `${now[row.vals.indexOf(best)]!.id} ${fmtCompact(best)}` : ""}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---- live comments ------------------------------------------------------------------------------------

function Comments({ sims, colors, hour, viewers }: { sims: Simulation[]; colors: Record<string, string>; hour: number; viewers: ViewerPersona[] }) {
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
    <aside className="cl-panel cl-comments" aria-label="Comments as they are posted">
      <div className="cl-panel-head">
        <h3 className="cl-label">Comments</h3>
        <span className="cl-panel-count num">
          {list.length} of {total}
        </span>
      </div>
      {list.length === 0 ? (
        <p className="cl-empty-line">Comments appear as the clock passes the hour they were posted. They are the viewer agents' own words.</p>
      ) : (
        <ol className="cl-comment-list" aria-live="polite">
          {list.map((c) => {
            const v = viewers.find((x) => x.id === c.viewer);
            const who = v ? viewerWho(v) : { id: c.viewer, name: c.name, color: "#777", initials: c.name.slice(0, 2).toUpperCase(), role: "viewer" as const };
            return (
              <li key={c.key} className="cl-comment">
                <Avatar who={who} size={28} />
                <div>
                  <p className="cl-comment-head">
                    <b>{c.name}</b>
                    <ReelBadge id={c.reel} color={colors[c.reel]!} size={16} />
                    <span className="num">h {c.hour.toFixed(1)}</span>
                  </p>
                  <p className="cl-comment-text">{c.text}</p>
                  {c.likes > 0 && (
                    <p className="cl-comment-likes num">
                      <Heart size={10} aria-hidden /> {fmtInt(c.likes)}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </aside>
  );
}

// ---- the model's assumptions --------------------------------------------------------------------------

function ModelNotes({ sims, colors }: { sims: Simulation[]; colors: Record<string, string> }) {
  const shared = (sims[0]?.notes ?? []).filter((n) => sims.every((s) => s.notes.includes(n)));
  const own = sims.map((s) => ({ id: s.reel, notes: s.notes.filter((n) => !shared.includes(n)) })).filter((x) => x.notes.length);
  if (!shared.length && !own.length) return null;
  return (
    <section className="cl-notes" aria-labelledby="cl-notes-title">
      <h3 id="cl-notes-title" className="cl-label">
        <Info size={12} aria-hidden /> How the simulation works
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
              <ReelBadge id={x.id} color={colors[x.id]!} size={16} /> {x.notes.join(" ")}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
