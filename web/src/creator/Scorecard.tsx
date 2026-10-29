import { Check, Scale, TriangleAlert } from "lucide-react";
import { useMemo, useState, type CSSProperties } from "react";
import { CreativityMap } from "./CreativityMap";
import { plannedReels } from "./Reels";
import { useCreator } from "./store";
import { firstSentence } from "./story";
import { agreementLabel, DIMENSION_LABEL, DIMENSIONS, dimensionMeans, headToHead, kendallFromRatings } from "./stats";
import type { Evaluation, LabState, Reel, ReelSummary } from "./types";
import { Avatar, Disclosure, fmtPct, numberWord, ReelBadge, reelColors, reelStyle, SectionHead, useAgents, viewerWho } from "./ui";

export function ScorecardSection() {
  const lab = useCreator((s) => s.lab)!;
  const ev = lab.evaluation;
  const colors = useMemo(() => reelColors(plannedReels(lab).map((p) => ({ id: p.id, kind: p.kind }))), [lab]);
  const nJudges = ev?.judges.length ?? 0;
  const nViewers = ev?.viewers.length ?? 0;
  return (
    <section id="cl-scorecard" className="cl-section" aria-labelledby="cl-scorecard-title">
      <SectionHead
        id="cl-scorecard-title"
        title="Scorecard"
        sub={
          <>
            An independent panel that never saw the room's notes: {nJudges ? numberWord(nJudges) : "the"} judges rate the reels blind and vote head to head,{" "}
            {nViewers ? numberWord(nViewers) : "the"} viewer agents react as their audience segment, and novelty is measured, not judged.
          </>
        }
      />
      {!ev || !lab.reels.length ? <Waiting lab={lab} /> : <Scores lab={lab} ev={ev} colors={colors} />}
    </section>
  );
}

function Waiting({ lab }: { lab: LabState }) {
  const steps = ["watch", "judge", "novelty", "verdict"].map((k) => lab.phases.find((p) => p.key === k)).filter(Boolean);
  return (
    <div className="cl-waiting">
      <Scale size={18} strokeWidth={1.6} aria-hidden />
      <p>The panel watches once the reels are rendered. The ranking, the verdict and the creativity map land here.</p>
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
  const summaries = useMemo(() => new Map(ev.summaries.map((s) => [s.reel, s])), [ev.summaries]);
  return (
    <>
      <div className="cl-score-top">
        <div className="cl-score-left">
          <Ranking reels={reels} ev={ev} summaries={summaries} colors={colors} />
          <Verdict ev={ev} />
          <Diversity ev={ev} />
        </div>
        <CreativityMap />
      </div>
      <Disclosure className="cl-full" label="Full scorecard: seven dimensions, head-to-head votes, judge agreement and the audience panel" openLabel="Hide the full scorecard">
        <div className="cl-full-grid">
          <Dimensions ev={ev} reels={reels} colors={colors} />
          <HeadToHead ev={ev} ids={ids} colors={colors} />
          <Agreement ev={ev} ids={ids} />
          <AudienceRates reels={reels} colors={colors} summaries={summaries} />
        </div>
        <AudiencePanel ev={ev} reels={reels} colors={colors} />
      </Disclosure>
    </>
  );
}

// ---- the ranking ------------------------------------------------------------------------------------

function Ranking({ reels, ev, summaries, colors }: { reels: Reel[]; ev: Evaluation; summaries: Map<string, ReelSummary>; colors: Record<string, string> }) {
  const rows = reels
    .map((r) => {
      const ratings = ev.ratings.filter((x) => x.reel === r.id);
      const s = summaries.get(r.id);
      const creativity = s?.creativity ?? (ratings.length ? ratings.reduce((a, x) => a + x.creativity, 0) / ratings.length : null);
      const wins = ev.pairwise.filter((v) => v.winner === r.id).length;
      const games = ev.pairwise.filter((v) => v.first === r.id || v.second === r.id).length;
      const novelty = ev.novelty.find((n) => n.kind === "reel" && n.id === r.id)?.novelty ?? s?.novelty ?? null;
      return { r, creativity, sd: s?.creativity_sd ?? 0, wins, games, novelty };
    })
    .sort((a, b) => (b.creativity ?? -1) - (a.creativity ?? -1));
  return (
    <ol className="cl-rank" aria-label="Reels ranked by the judges' creativity score">
      {rows.map((row, i) => (
        <li key={row.r.id} className="cl-rank-row" style={reelStyle(colors[row.r.id]!)} data-kind={row.r.kind}>
          <span className="cl-rank-n num">{i + 1}</span>
          <ReelBadge id={row.r.id} color={colors[row.r.id]!} size={26} />
          <span className="cl-rank-name">
            <b>{row.r.label}</b>
            <span>{row.r.title}</span>
          </span>
          <span className="cl-rank-score" title={`Judges' mean creativity, ± ${row.sd.toFixed(1)} sd`}>
            <span className="cl-rank-big num">{row.creativity != null ? row.creativity.toFixed(1) : "–"}</span>
            <span className="cl-rank-of">/ 10</span>
            <span className="cl-rank-bar" aria-hidden>
              <span style={{ width: `${((row.creativity ?? 0) / 10) * 100}%` }} />
            </span>
          </span>
          <span className="cl-rank-fact">
            <span>
              won <span className="num">{row.wins}</span> of <span className="num">{row.games}</span>
            </span>
            <small>head-to-heads</small>
          </span>
          <span className="cl-rank-fact">
            <span className="num">{row.novelty != null ? row.novelty.toFixed(2) : "–"}</span>
            <small>novelty</small>
          </span>
        </li>
      ))}
    </ol>
  );
}

// ---- verdict, caveats, diversity -----------------------------------------------------------------------

/** The caveat that most needs reading: the one that says the panel is simulated, else the first. */
export function leadCaveat(notes: string[]): string | undefined {
  return notes.find((n) => /not a person|simulat|language model|claude agent/i.test(n)) ?? notes[0];
}

function Verdict({ ev }: { ev: Evaluation }) {
  const head = useAgents()("head_judge");
  const [full, setFull] = useState(false);
  if (!ev.verdict && !ev.notes.length) {
    return (
      <p className="cl-quiet">
        <span className="spinner spinner-sm" aria-hidden /> The head judge writes the verdict once the panel, the judges and the novelty scores are in.
      </p>
    );
  }
  const short = ev.verdict ? (() => {
    const one = firstSentence(ev.verdict);
    const rest = ev.verdict.slice(ev.verdict.indexOf(one) + one.length).trim();
    return rest ? `${one} ${firstSentence(rest)}` : one;
  })() : "";
  const lead = leadCaveat(ev.notes);
  return (
    <div className="cl-verdict-block">
      {ev.verdict && (
        <figure className="cl-quote">
          <blockquote>{full ? ev.verdict : short}</blockquote>
          <figcaption>
            <Avatar who={head} size={20} />
            <span>{head.name !== "head judge" ? head.name : "Head judge"}</span>
            {short !== ev.verdict && (
              <button className="cl-link-btn" onClick={() => setFull((f) => !f)} aria-expanded={full}>
                {full ? "Shorter" : "Full verdict"}
              </button>
            )}
          </figcaption>
        </figure>
      )}
      {ev.notes.length > 0 && (
        <Disclosure
          className="cl-caveats"
          label={
            <>
              <TriangleAlert size={12} aria-hidden className="cl-caveat-icon" />
              <b>
                {ev.notes.length} caveat{ev.notes.length === 1 ? "" : "s"}
              </b>
              <span className="cl-caveat-lead">{lead}</span>
            </>
          }
          openLabel={
            <>
              <TriangleAlert size={12} aria-hidden className="cl-caveat-icon" />
              <b>
                {ev.notes.length} caveat{ev.notes.length === 1 ? "" : "s"}
              </b>
            </>
          }
        >
          <ul className="cl-caveat-list">
            {ev.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </Disclosure>
      )}
    </div>
  );
}

function Diversity({ ev }: { ev: Evaluation }) {
  const room = ev.diversity.room;
  const single = ev.diversity.single_agent;
  if (room == null || single == null) return null;
  const close = Math.abs(room - single) < 0.02;
  return (
    <p className="cl-diversity">
      <span className="cl-diversity-k">Idea diversity</span> room <b className="num">{room.toFixed(2)}</b> · single agent <b className="num">{single.toFixed(2)}</b>
      <span className="cl-diversity-v">
        {close ? "about the same spread of first ideas." : room > single ? "the room's first ideas were more varied." : "the single agent's first ideas were more varied."}
      </span>
    </p>
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
