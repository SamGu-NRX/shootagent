import { Check } from "lucide-react";
import { useMemo, useState } from "react";
import { CreativityMap } from "./CreativityMap";
import { useCreator } from "./store";
import { firstSentence } from "./story";
import { agreementLabel, DIMENSION_LABEL, DIMENSIONS, dimensionMeans, headToHead, kendallFromRatings } from "./stats";
import type { Evaluation, LabState, Reel, ReelSummary } from "./types";
import { Disclosure, fmtPct, numberWord, ReelBadge, SectionHead, useAgents } from "./ui";

export function ScorecardSection() {
  const lab = useCreator((s) => s.lab)!;
  const ev = lab.evaluation;
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
      {!ev || !lab.reels.length ? <Waiting lab={lab} /> : <Scores lab={lab} ev={ev} />}
    </section>
  );
}

function Waiting({ lab }: { lab: LabState }) {
  const steps = ["watch", "judge", "novelty", "verdict"].map((k) => lab.phases.find((p) => p.key === k)).filter(Boolean);
  return (
    <div className="cl-waiting">
      <p>The panel watches once the reels are rendered. The ranking, the verdict and the creativity map land here.</p>
      <ul className="cl-score-steps">
        {steps.map((p) => (
          <li key={p!.key} data-status={p!.status}>
            {p!.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Scores({ lab, ev }: { lab: LabState; ev: Evaluation }) {
  const reels = lab.reels;
  const ids = reels.map((r) => r.id);
  const summaries = useMemo(() => new Map(ev.summaries.map((s) => [s.reel, s])), [ev.summaries]);
  return (
    <>
      <div className="cl-score-top">
        <div className="cl-score-left">
          <Ranking reels={reels} ev={ev} summaries={summaries} />
          <Verdict ev={ev} />
          <Diversity ev={ev} />
        </div>
        <CreativityMap />
      </div>
      <Disclosure className="cl-full" label="Full scorecard: seven dimensions, head-to-head votes, judge agreement and the audience panel" openLabel="Hide the full scorecard">
        <div className="cl-full-grid">
          <Dimensions ev={ev} reels={reels} />
          <HeadToHead ev={ev} ids={ids} />
          <Agreement ev={ev} ids={ids} />
          <AudienceRates reels={reels} summaries={summaries} />
        </div>
        <AudiencePanel ev={ev} reels={reels} />
      </Disclosure>
    </>
  );
}

// ---- the ranking ------------------------------------------------------------------------------------

function Ranking({ reels, ev, summaries }: { reels: Reel[]; ev: Evaluation; summaries: Map<string, ReelSummary> }) {
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
        <li key={row.r.id} className="cl-rank-row" data-kind={row.r.kind} data-top={i === 0 || undefined}>
          <span className="cl-rank-n num">{i + 1}</span>
          <ReelBadge id={row.r.id} size={22} />
          <span className="cl-rank-name">
            <b>{row.r.label}</b>
            <span>{row.r.title}</span>
          </span>
          <span className="cl-rank-score" title={`Judges' mean creativity, ± ${row.sd.toFixed(1)} sd`}>
            <span className="cl-rank-line">
              <span className="cl-rank-big num">{row.creativity != null ? row.creativity.toFixed(1) : "–"}</span>
              <span className="cl-rank-of">/ 10</span>
            </span>
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
      <p className="cl-quiet cl-verdict-wait">
        <span className="spinner spinner-sm" aria-hidden /> The head judge writes the verdict once the panel, the judges and the novelty scores are in.
      </p>
    );
  }
  const short = ev.verdict
    ? (() => {
        const one = firstSentence(ev.verdict);
        const rest = ev.verdict.slice(ev.verdict.indexOf(one) + one.length).trim();
        return rest ? `${one} ${firstSentence(rest)}` : one;
      })()
    : "";
  const lead = leadCaveat(ev.notes);
  const caveats = `${ev.notes.length} caveat${ev.notes.length === 1 ? "" : "s"}`;
  return (
    <div className="cl-verdict-block">
      {ev.verdict && (
        <figure className="cl-quote">
          <blockquote>{full ? ev.verdict : short}</blockquote>
          <figcaption>
            <span>{head.name !== "head judge" ? head.name : "Head judge"}</span>
            {short !== ev.verdict && (
              <>
                <span aria-hidden>·</span>
                <button className="cl-link-btn" onClick={() => setFull((f) => !f)} aria-expanded={full}>
                  {full ? "Shorter" : "Full verdict"}
                </button>
              </>
            )}
          </figcaption>
        </figure>
      )}
      {ev.notes.length > 0 && (
        <Disclosure
          className="cl-caveats"
          label={
            <span className="cl-caveat-label">
              <b>{caveats}</b>
              {lead && <span className="cl-caveat-lead">{lead}</span>}
            </span>
          }
          openLabel={
            <span className="cl-caveat-label">
              <b>{caveats}</b>
            </span>
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
      Idea diversity: room <b className="num">{room.toFixed(2)}</b>, single agent <b className="num">{single.toFixed(2)}</b>.{" "}
      {close ? "About the same spread of first ideas." : room > single ? "The room's first ideas were more varied." : "The single agent's first ideas were more varied."}
    </p>
  );
}

// ---- seven dimensions ---------------------------------------------------------------------------------

function Dimensions({ ev, reels }: { ev: Evaluation; reels: Reel[] }) {
  const means = useMemo(() => Object.fromEntries(reels.map((r) => [r.id, dimensionMeans(ev.ratings, r.id)])), [ev.ratings, reels]);
  return (
    <div className="cl-full-block">
      <h3 className="cl-h3">Seven dimensions · judges' mean, 1–10</h3>
      <table className="cl-table cl-dims-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Dimension</span>
            </th>
            {reels.map((r) => (
              <th key={r.id} scope="col" className="cl-col-reel">
                <ReelBadge id={r.id} size={18} />
                <span className="sr-only">{r.label}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {DIMENSIONS.map((d) => {
            const best = Math.max(...reels.map((r) => means[r.id]![d]));
            return (
              <tr key={d}>
                <th scope="row">{DIMENSION_LABEL[d]}</th>
                {reels.map((r) => {
                  const v = means[r.id]![d];
                  return (
                    <td key={r.id} className="cl-col-reel" data-best={v === best || undefined}>
                      <span className="num">{v.toFixed(1)}</span>
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

function HeadToHead({ ev, ids }: { ev: Evaluation; ids: string[] }) {
  const t = useMemo(() => headToHead(ev.pairwise, ids), [ev.pairwise, ids]);
  return (
    <div className="cl-full-block">
      <h3 className="cl-h3">
        Head to head · which is more creative? <span className="cl-h3-aside num">{ev.pairwise.length} votes</span>
      </h3>
      <table className="cl-table cl-h2h-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Reel</span>
            </th>
            {ids.map((id) => (
              <th key={id} scope="col" className="cl-col-reel">
                <span className="cl-h2h-vs">vs</span> <ReelBadge id={id} size={18} />
              </th>
            ))}
            <th scope="col" className="cl-col-reel">
              Won
            </th>
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
                  <ReelBadge id={a} size={18} />
                </th>
                {ids.map((b) => {
                  if (a === b)
                    return (
                      <td key={b} className="cl-col-reel cl-h2h-self" aria-label="same reel">
                        –
                      </td>
                    );
                  const c = row[b]!;
                  const share = c.total ? c.wins / c.total : 0;
                  return (
                    <td key={b} className="cl-col-reel num" data-win={share > 0.5 || undefined} title={`${a} beat ${b} in ${c.wins} of ${c.total} votes`}>
                      {c.wins}/{c.total}
                    </td>
                  );
                })}
                <td className="cl-col-reel num cl-h2h-total">{total ? fmtPct(wins / total) : "–"}</td>
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
  const a = ev.agreement;
  const panel = [a.judges && `${a.judges} judges`, a.reels && `${a.reels} reels`, a.pairs_judged && `${a.pairs_judged} pairs judged both ways`].filter(Boolean).join(" · ");
  return (
    <div className="cl-full-block cl-agree">
      <h3 className="cl-h3">
        Do the judges agree? <span className="cl-h3-aside">Kendall's W</span>
      </h3>
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
      <ul className="cl-agree-notes">
        {a.pairwise_consistency != null && (
          <li>
            Judges' head-to-head votes matched their own scores <b className="num">{fmtPct(a.pairwise_consistency)}</b> of the time.
          </li>
        )}
        {a.order_consistency != null && (
          <li>
            Shown a pair in both orders, judges picked the same winner <b className="num">{fmtPct(a.order_consistency)}</b> of the time.
          </li>
        )}
        {a.first_shown_wins != null && (
          <li>
            The reel shown first won <b className="num">{fmtPct(a.first_shown_wins)}</b> of head-to-head votes (50% means no position bias).
          </li>
        )}
        {(a.kendall_w_p != null || panel) && (
          <li className="num">
            {a.kendall_w_p != null && <>p = {a.kendall_w_p < 0.001 ? "<0.001" : a.kendall_w_p.toFixed(3)}</>}
            {a.kendall_w_p != null && panel && " · "}
            {panel}
          </li>
        )}
      </ul>
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

function AudienceRates({ reels, summaries }: { reels: Reel[]; summaries: Map<string, ReelSummary> }) {
  return (
    <div className="cl-full-block">
      <h3 className="cl-h3">Audience panel · weighted by segment</h3>
      <table className="cl-table cl-rates-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Measure</span>
            </th>
            {reels.map((r) => (
              <th key={r.id} scope="col" className="cl-col-reel">
                <ReelBadge id={r.id} size={18} />
                <span className="sr-only">{r.label}</span>
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
                  <td key={reels[i]!.id} className="cl-col-reel num" data-best={(v === best && v > 0) || undefined}>
                    {fmtPct(v)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="cl-foot">Intents are what the viewer agents said they'd do (0–100), not platform rates.</p>
    </div>
  );
}

function AudiencePanel({ ev, reels }: { ev: Evaluation; reels: Reel[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!ev.viewers.length) return null;
  const byKey = new Map(ev.reactions.map((r) => [`${r.viewer}:${r.reel}`, r]));
  const sel = open ? byKey.get(open) : undefined;
  const selViewer = sel ? ev.viewers.find((v) => v.id === sel.viewer) : undefined;
  return (
    <div className="cl-full-block cl-viewers-block">
      <h3 className="cl-h3">
        The viewers · what each one did
        <span className="cl-h3-aside cl-viewers-key">
          <span className="cl-vdot" data-stopped aria-hidden /> stopped
          <span className="cl-vdot" aria-hidden /> scrolled past
          <Check size={11} aria-hidden /> watched to the end
        </span>
      </h3>
      <div className="cl-viewers">
        <table className="cl-table cl-viewers-table">
          <thead>
            <tr>
              <th scope="col">Viewer</th>
              <th scope="col" className="cl-col-num">
                Share
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
            {ev.viewers.map((v) => (
              <tr key={v.id}>
                <th scope="row">
                  <span className="cl-viewer">
                    <b>{v.name}</b>
                    <small title={v.segment}>{v.segment}</small>
                  </span>
                </th>
                <td className="cl-col-num num">{fmtPct(v.weight)}</td>
                {reels.map((r) => {
                  const x = byKey.get(`${v.id}:${r.id}`);
                  if (!x)
                    return (
                      <td key={r.id} className="cl-h2h-self">
                        –
                      </td>
                    );
                  const key = `${v.id}:${r.id}`;
                  const frac = Math.min(1, x.watch_seconds / Math.max(0.1, r.seconds));
                  return (
                    <td key={r.id}>
                      <button
                        className="cl-vcell"
                        data-stopped={x.stopped || undefined}
                        aria-pressed={open === key}
                        onClick={() => setOpen(open === key ? null : key)}
                        aria-label={`${v.name} on reel ${r.id}: ${x.stopped ? "stopped" : "scrolled past"}, watched ${x.watch_seconds.toFixed(1)} s${x.completed ? " to the end" : ""}. ${x.first_impression}`}
                      >
                        <span className="cl-vdot" data-stopped={x.stopped || undefined} aria-hidden />
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
                <b>{selViewer.name}</b> on <ReelBadge id={sel.reel} size={16} />
              </p>
              <blockquote>“{sel.first_impression}”</blockquote>
              {sel.why && <p className="cl-reaction-why">{sel.why}</p>}
              {sel.comment_text && (
                <p className="cl-reaction-line">
                  <span>Would comment</span> {sel.comment_text}
                </p>
              )}
              {sel.share_note && (
                <p className="cl-reaction-line">
                  <span>Would send it to</span> {sel.share_note}
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
