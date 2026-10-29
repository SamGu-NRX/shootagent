import { Check, Info, Scale } from "lucide-react";
import { useMemo, useState, type CSSProperties } from "react";
import { CreativityMap } from "./CreativityMap";
import { plannedReels } from "./Reels";
import { useCreator } from "./store";
import { agreementLabel, DIMENSION_LABEL, DIMENSIONS, dimensionMeans, headToHead, kendallFromRatings } from "./stats";
import type { Evaluation, LabState, Reel, ReelSummary } from "./types";
import { Avatar, fmtPct, numberWord, ReelBadge, reelColors, reelStyle, SectionHead, useAgents, viewerWho } from "./ui";

export function ScorecardSection() {
  const lab = useCreator((s) => s.lab)!;
  const ev = lab.evaluation;
  const colors = useMemo(() => reelColors(plannedReels(lab).map((p) => ({ id: p.id, kind: p.kind }))), [lab]);
  const nJudges = ev?.judges.length ?? 0;
  const nViewers = ev?.viewers.length ?? 0;
  return (
    <section id="cl-scorecard" className="cl-section" aria-labelledby="cl-scorecard-title">
      <SectionHead
        n="03"
        id="cl-scorecard-title"
        title="Creativity scorecard"
        sub={
          <>
            An independent panel that never saw the room's notes: {nViewers ? numberWord(nViewers) : "the"} viewer agents react as their audience segment,{" "}
            {nJudges ? numberWord(nJudges) : "the"} judges rate blind and vote head-to-head, and novelty is measured, not judged.
          </>
        }
      />
      {!ev || !lab.reels.length ? (
        <Waiting lab={lab} />
      ) : (
        <Scores lab={lab} ev={ev} colors={colors} />
      )}
    </section>
  );
}

function Waiting({ lab }: { lab: LabState }) {
  const steps = ["watch", "judge", "novelty", "verdict"].map((k) => lab.phases.find((p) => p.key === k)).filter(Boolean);
  return (
    <div className="cl-waiting">
      <Scale size={18} strokeWidth={1.6} aria-hidden />
      <p>The panel watches once the reels are rendered. Scores, agreement, novelty and the verdict land here.</p>
      <ul>
        {steps.map((p) => (
          <li key={p!.key} data-status={p!.status}>
            {p!.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Scores({ lab, ev, colors }: { lab: LabState; ev: Evaluation; colors: Record<string, string> }) {
  const reels = lab.reels;
  const ids = reels.map((r) => r.id);
  const summaries = new Map(ev.summaries.map((s) => [s.reel, s]));
  return (
    <div className="cl-score">
      <Verdict ev={ev} />
      <div className="cl-score-cards" style={{ "--n": reels.length } as CSSProperties}>
        {reels.map((r) => (
          <ReelScore key={r.id} reel={r} color={colors[r.id]!} ev={ev} summary={summaries.get(r.id)} />
        ))}
      </div>
      <div className="cl-score-row">
        <Dimensions ev={ev} reels={reels} colors={colors} />
        <CreativityMap />
      </div>
      <div className="cl-score-row cl-score-row-3">
        <HeadToHead ev={ev} ids={ids} colors={colors} />
        <Agreement ev={ev} ids={ids} />
        <AudienceRates reels={reels} colors={colors} summaries={summaries} />
      </div>
      <AudiencePanel ev={ev} reels={reels} colors={colors} />
    </div>
  );
}

// ---- verdict and caveats ----------------------------------------------------------------------------

function Verdict({ ev }: { ev: Evaluation }) {
  const head = useAgents()("head_judge");
  if (!ev.verdict && !ev.notes.length) {
    return (
      <div className="cl-verdict-row">
        <p className="cl-empty-line">
          <span className="spinner spinner-sm" aria-hidden /> The head judge writes the verdict once the panel, the judges and the novelty scores are in.
        </p>
      </div>
    );
  }
  return (
    <div className="cl-verdict-row">
      <figure className="cl-verdict-quote">
        <figcaption>
          <Avatar who={head} size={24} /> <span>{head.name !== "head judge" ? head.name : "Head judge"}'s verdict</span>
        </figcaption>
        <blockquote>{ev.verdict}</blockquote>
      </figure>
      {ev.notes.length > 0 && (
        <aside className="cl-caveats" aria-label="Caveats">
          <h3 className="cl-label">
            <Info size={12} aria-hidden /> Read this before quoting a number
          </h3>
          <ul>
            {ev.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </aside>
      )}
    </div>
  );
}

// ---- per reel ---------------------------------------------------------------------------------------

function ReelScore({ reel, color, ev, summary }: { reel: Reel; color: string; ev: Evaluation; summary: ReelSummary | undefined }) {
  const agents = useAgents();
  const ratings = ev.ratings.filter((r) => r.reel === reel.id);
  const novelty = ev.novelty.find((n) => n.kind === "reel" && n.id === reel.id);
  const wins = ev.pairwise.filter((v) => v.winner === reel.id).length;
  const games = ev.pairwise.filter((v) => v.first === reel.id || v.second === reel.id).length;
  const mean = summary?.creativity ?? (ratings.length ? ratings.reduce((a, r) => a + r.creativity, 0) / ratings.length : null);
  const sdv = summary?.creativity_sd ?? 0;
  const x = (v: number) => `${((v - 1) / 9) * 100}%`;
  // judges who gave the same score stack upward
  const seen = new Map<number, number>();
  return (
    <article className="cl-rs" style={reelStyle(color)} data-kind={reel.kind}>
      <header className="cl-rs-head">
        <ReelBadge id={reel.id} color={color} size={26} />
        <div>
          <p className="cl-rs-label">{reel.label}</p>
          <p className="cl-rs-title">{reel.title}</p>
        </div>
      </header>

      <div className="cl-rs-block">
        <p className="cl-rs-k">Creativity · judges</p>
        <p className="cl-rs-big">
          <span className="cl-rs-num">{mean != null ? mean.toFixed(1) : "–"}</span>
          {mean != null && <span className="cl-rs-sd num">± {sdv.toFixed(1)} sd</span>}
          <span className="cl-rs-of">/ 10</span>
        </p>
        <div className="cl-dots" role="img" aria-label={`Judge scores: ${ratings.map((r) => `${agents(r.judge).name} ${r.creativity}`).join(", ")}`}>
          <span className="cl-dots-axis" />
          {mean != null && sdv > 0 && <span className="cl-dots-sd" style={{ left: x(Math.max(1, mean - sdv)), right: `calc(100% - ${x(Math.min(10, mean + sdv))})` }} />}
          {ratings.map((r) => {
            const k = seen.get(r.creativity) ?? 0;
            seen.set(r.creativity, k + 1);
            return <span key={r.judge} className="cl-dot" style={{ left: x(r.creativity), bottom: `${6 + k * 9}px` }} title={`${agents(r.judge).name}: ${r.creativity}`} />;
          })}
          {mean != null && <span className="cl-dots-mean" style={{ left: x(mean) }} />}
          <span className="cl-dots-tick num" style={{ left: 0 }}>
            1
          </span>
          <span className="cl-dots-tick num" style={{ left: "100%" }}>
            10
          </span>
        </div>
      </div>

      <dl className="cl-rs-facts">
        <div>
          <dt>Head-to-head</dt>
          <dd>
            <b className="num">{games ? fmtPct(wins / games) : "–"}</b>
            <span className="num">
              won {wins} of {games}
            </span>
          </dd>
        </div>
        <div>
          <dt>Novelty</dt>
          <dd>
            <b className="num">{novelty ? novelty.novelty.toFixed(2) : summary ? summary.novelty.toFixed(2) : "–"}</b>
            <span className="cl-meter cl-meter-wide" aria-hidden>
              <span style={{ width: `${(novelty?.novelty ?? summary?.novelty ?? 0) * 100}%` }} />
            </span>
          </dd>
        </div>
      </dl>
      {novelty && (
        <p className="cl-rs-cliche">
          Closest cliche <q>{novelty.nearest_cliche}</q> <span className="num">({novelty.cliche_similarity.toFixed(2)})</span>
        </p>
      )}
    </article>
  );
}

// ---- seven dimensions ---------------------------------------------------------------------------------

function Dimensions({ ev, reels, colors }: { ev: Evaluation; reels: Reel[]; colors: Record<string, string> }) {
  const means = useMemo(() => Object.fromEntries(reels.map((r) => [r.id, dimensionMeans(ev.ratings, r.id)])), [ev.ratings, reels]);
  const [hover, setHover] = useState<string | null>(null);
  return (
    <div className="cl-panel cl-dims">
      <div className="cl-panel-head">
        <h3 className="cl-label">Seven dimensions · judges' mean</h3>
        <span className="cl-panel-count">1-10</span>
      </div>
      <table className="cl-dims-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Dimension</span>
            </th>
            {reels.map((r) => (
              <th key={r.id} scope="col">
                <ReelBadge id={r.id} color={colors[r.id]!} size={20} />
                <span className="sr-only">{r.label}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {DIMENSIONS.map((d) => {
            const best = Math.max(...reels.map((r) => means[r.id]![d]));
            return (
              <tr key={d} data-hover={hover === d || undefined} onPointerEnter={() => setHover(d)} onPointerLeave={() => setHover(null)}>
                <th scope="row">{DIMENSION_LABEL[d]}</th>
                {reels.map((r) => {
                  const v = means[r.id]![d];
                  return (
                    <td key={r.id} style={reelStyle(colors[r.id]!)} data-best={v === best || undefined}>
                      <span className="cl-dim-v num">{v.toFixed(1)}</span>
                      <span className="cl-dim-bar" aria-hidden>
                        <span style={{ width: `${v * 10}%` }} />
                      </span>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="cl-foot">Bold marks the best reel on each dimension. Truthfulness: every claim checks out against the photos and listing.</p>
    </div>
  );
}

// ---- head to head and agreement ---------------------------------------------------------------------

function HeadToHead({ ev, ids, colors }: { ev: Evaluation; ids: string[]; colors: Record<string, string> }) {
  const t = useMemo(() => headToHead(ev.pairwise, ids), [ev.pairwise, ids]);
  return (
    <div className="cl-panel cl-h2h">
      <div className="cl-panel-head">
        <h3 className="cl-label">Head-to-head · which is more creative?</h3>
        <span className="cl-panel-count num">{ev.pairwise.length} votes</span>
      </div>
      <table className="cl-h2h-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Reel</span>
            </th>
            {ids.map((id) => (
              <th key={id} scope="col">
                <span className="cl-h2h-vs">vs</span> <ReelBadge id={id} color={colors[id]!} size={18} />
              </th>
            ))}
            <th scope="col">Won</th>
          </tr>
        </thead>
        <tbody>
          {ids.map((a) => {
            const row = t[a]!;
            const wins = ids.reduce((n, b) => n + (a === b ? 0 : row[b]!.wins), 0);
            const total = ids.reduce((n, b) => n + (a === b ? 0 : row[b]!.total), 0);
            return (
              <tr key={a}>
                <th scope="row">
                  <ReelBadge id={a} color={colors[a]!} size={20} />
                </th>
                {ids.map((b) => {
                  if (a === b) return <td key={b} className="cl-h2h-self" aria-label="same reel" />;
                  const c = row[b]!;
                  const share = c.total ? c.wins / c.total : 0;
                  return (
                    <td key={b} style={{ "--share": share } as CSSProperties} data-win={share > 0.5 || undefined} title={`${a} beat ${b} in ${c.wins} of ${c.total} votes`}>
                      <span className="num">
                        {c.wins}/{c.total}
                      </span>
                    </td>
                  );
                })}
                <td className="cl-h2h-total num">{total ? fmtPct(wins / total) : "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="cl-foot">Each judge saw each pair once, in a random order. Read across: how often the row's reel won.</p>
    </div>
  );
}

function Agreement({ ev, ids }: { ev: Evaluation; ids: string[] }) {
  const w = ev.agreement.kendall_w ?? kendallFromRatings(ev.ratings, ids);
  const label = agreementLabel(w);
  const consistency = ev.agreement.pairwise_consistency;
  const a = ev.agreement;
  const panel = [a.judges && `${a.judges} judges`, a.reels && `${a.reels} reels`, a.pairs_judged && `${a.pairs_judged} pairs judged both ways`]
    .filter(Boolean)
    .join(" · ");
  const room = ev.diversity.room;
  const single = ev.diversity.single_agent;
  return (
    <div className="cl-panel cl-agree">
      <div className="cl-panel-head">
        <h3 className="cl-label">Do the judges agree?</h3>
        <span className="cl-panel-count">Kendall's W</span>
      </div>
      <div className="cl-agree-w">
        <span className="cl-agree-num num">{w.toFixed(2)}</span>
        <div>
          <p className="cl-agree-label">{label.short}</p>
          <p className="cl-agree-long">{label.long}</p>
        </div>
      </div>
      <div className="cl-agree-scale" aria-hidden>
        <span style={{ left: `${Math.min(100, Math.max(0, w * 100))}%` }} />
      </div>
      <p className="cl-agree-ends" aria-hidden>
        <span>0 · no shared order</span>
        <span>1 · identical rankings</span>
      </p>
      {consistency != null && (
        <p className="cl-agree-note">
          Judges' head-to-head votes matched their own scores <b className="num">{fmtPct(consistency)}</b> of the time.
        </p>
      )}
      {a.order_consistency != null && (
        <p className="cl-agree-note">
          Shown a pair in both orders, judges picked the same winner <b className="num">{fmtPct(a.order_consistency)}</b> of
          the time.
        </p>
      )}
      {a.first_shown_wins != null && (
        <p className="cl-agree-note">
          The reel shown first won <b className="num">{fmtPct(a.first_shown_wins)}</b> of head-to-head votes (50% means no
          position bias).
        </p>
      )}
      {(a.kendall_w_p != null || panel) && (
        <p className="cl-agree-note cl-agree-meta num">
          {a.kendall_w_p != null && <>p = {a.kendall_w_p < 0.001 ? "<0.001" : a.kendall_w_p.toFixed(3)}</>}
          {a.kendall_w_p != null && panel && " · "}
          {panel}
        </p>
      )}
      {room != null && single != null && (
        <div className="cl-agree-div">
          <p className="cl-label">Idea diversity · room vs single agent</p>
          <div className="cl-divpair">
            <span>Room</span>
            <span className="cl-divpair-track" data-tone="room">
              <span style={{ width: `${(room / Math.max(room, single)) * 100}%` }} />
            </span>
            <b className="num">{room.toFixed(2)}</b>
            <span>Single agent</span>
            <span className="cl-divpair-track" data-tone="single">
              <span style={{ width: `${(single / Math.max(room, single)) * 100}%` }} />
            </span>
            <b className="num">{single.toFixed(2)}</b>
          </div>
          <p className="cl-foot">Mean distance between concepts at equal numbers. The room's ideas are {room > single ? `${(room / Math.max(single, 1e-9)).toFixed(1)}× as spread out` : "no more varied"}.</p>
        </div>
      )}
    </div>
  );
}

// ---- audience -------------------------------------------------------------------------------------

const RATE_ROWS: [keyof ReelSummary, string][] = [
  ["stop_rate", "Stopped in 1.5 s"],
  ["avg_watch_fraction", "Average watched"],
  ["completion_rate", "Watched to the end"],
  ["like_rate", "Like intent"],
  ["share_rate", "Share intent"],
  ["save_rate", "Save intent"],
  ["comment_rate", "Comment intent"],
  ["inquire_rate", "Would message the agent"],
];

function AudienceRates({ reels, colors, summaries }: { reels: Reel[]; colors: Record<string, string>; summaries: Map<string, ReelSummary> }) {
  return (
    <div className="cl-panel cl-rates">
      <div className="cl-panel-head">
        <h3 className="cl-label">Audience panel · weighted by segment</h3>
      </div>
      <table className="cl-rates-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Measure</span>
            </th>
            {reels.map((r) => (
              <th key={r.id} scope="col">
                <ReelBadge id={r.id} color={colors[r.id]!} size={18} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {RATE_ROWS.map(([k, label]) => {
            const vals = reels.map((r) => (summaries.get(r.id)?.[k] as number | undefined) ?? 0);
            const best = Math.max(...vals);
            return (
              <tr key={k}>
                <th scope="row">{label}</th>
                {vals.map((v, i) => (
                  <td key={reels[i]!.id} className="num" data-best={(v === best && v > 0) || undefined}>
                    {fmtPct(v)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="cl-foot">Intents are what the viewer agents said they'd do (0-100), not platform rates.</p>
    </div>
  );
}

function AudiencePanel({ ev, reels, colors }: { ev: Evaluation; reels: Reel[]; colors: Record<string, string> }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!ev.viewers.length) return null;
  const byKey = new Map(ev.reactions.map((r) => [`${r.viewer}:${r.reel}`, r]));
  const sel = open ? byKey.get(open) : undefined;
  const selViewer = sel ? ev.viewers.find((v) => v.id === sel.viewer) : undefined;
  return (
    <div className="cl-panel cl-panelgrid">
      <div className="cl-panel-head">
        <h3 className="cl-label">The viewers · what each one did</h3>
        <span className="cl-panel-count">
          <span className="cl-key cl-key-stop" /> stopped <span className="cl-key cl-key-skip" /> scrolled past <Check size={11} aria-hidden /> watched to the end
        </span>
      </div>
      <div className="cl-viewers">
        <table className="cl-viewers-table">
          <thead>
            <tr>
              <th scope="col">Viewer</th>
              <th scope="col" className="cl-vw">
                Share
              </th>
              {reels.map((r) => (
                <th key={r.id} scope="col">
                  <ReelBadge id={r.id} color={colors[r.id]!} size={18} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ev.viewers.map((v) => (
              <tr key={v.id}>
                <th scope="row">
                  <span className="cl-viewer">
                    <Avatar who={viewerWho(v)} size={24} />
                    <span>
                      <b>{v.name}</b>
                      <small>{v.segment}</small>
                    </span>
                  </span>
                </th>
                <td className="cl-vw num">{fmtPct(v.weight)}</td>
                {reels.map((r) => {
                  const x = byKey.get(`${v.id}:${r.id}`);
                  if (!x) return <td key={r.id} className="muted">–</td>;
                  const key = `${v.id}:${r.id}`;
                  const frac = Math.min(1, x.watch_seconds / Math.max(0.1, r.seconds));
                  return (
                    <td key={r.id} style={reelStyle(colors[r.id]!)}>
                      <button
                        className="cl-vcell"
                        data-stopped={x.stopped || undefined}
                        aria-pressed={open === key}
                        onClick={() => setOpen(open === key ? null : key)}
                        aria-label={`${v.name} on reel ${r.id}: ${x.stopped ? "stopped" : "scrolled past"}, watched ${x.watch_seconds.toFixed(1)} s${x.completed ? " to the end" : ""}. ${x.first_impression}`}
                      >
                        <span className="cl-vcell-dot" aria-hidden />
                        <span className="cl-vcell-bar" aria-hidden>
                          <span style={{ width: `${frac * 100}%` }} />
                        </span>
                        <span className="cl-vcell-s num">{x.completed ? <Check size={11} aria-label="to the end" /> : `${x.watch_seconds.toFixed(1)}s`}</span>
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <aside className="cl-reaction" aria-live="polite">
          {sel && selViewer ? (
            <>
              <p className="cl-reaction-who">
                <Avatar who={viewerWho(selViewer)} size={28} />
                <span>
                  <b>{selViewer.name}</b> on <ReelBadge id={sel.reel} color={colors[sel.reel]!} size={16} />
                </span>
              </p>
              <blockquote>“{sel.first_impression}”</blockquote>
              {sel.why && <p className="cl-reaction-why">{sel.why}</p>}
              {sel.comment_text && (
                <p className="cl-reaction-line">
                  <span className="cl-label">Would comment</span> {sel.comment_text}
                </p>
              )}
              {sel.share_note && (
                <p className="cl-reaction-line">
                  <span className="cl-label">Would send it to</span> {sel.share_note}
                </p>
              )}
              <p className="cl-reaction-ints num">
                <span>like {sel.like}</span>
                <span>share {sel.share}</span>
                <span>save {sel.save}</span>
                <span>message the agent {sel.inquire}</span>
              </p>
            </>
          ) : (
            <p className="cl-reaction-hint">Pick a cell to read that viewer's first impression, what they'd comment, and who they'd send it to.</p>
          )}
        </aside>
      </div>
    </div>
  );
}
