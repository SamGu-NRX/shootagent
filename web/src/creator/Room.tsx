import { ArrowDown, ArrowRight, Check, type LucideIcon, RotateCw, X } from "lucide-react";
import { memo, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { AgentLanes } from "./AgentLanes";
import { plannedReels } from "./Reels";
import { CONTROL_AGENT, groupFeed } from "./phases";
import { AGENT_JOB, agentIcon, dashColor } from "./present/cast";
import { useCreator } from "./store";
import { buildStory, criticScore, type CriticPick, type StoryStep } from "./story";
import type { Critique, CritiqueVerdict, LabState, ReelConcept, RoomMessage } from "./types";
import { Disclosure, fmtClock, focusStyle, numberWord, ReelBadge, Rich, SectionHead, useAgents, useReducedMotion, type Who } from "./ui";

// ---- shared bits ----------------------------------------------------------------------------------

export const conceptSeconds = (c: ReelConcept) => c.beats.reduce((a, b) => a + b.seconds, 0);

const VERDICT: Record<CritiqueVerdict, { label: string; icon: LucideIcon }> = {
  greenlight: { label: "Greenlight", icon: Check },
  revise: { label: "Revise", icon: RotateCw },
  kill: { label: "Kill", icon: X },
};

export function VerdictChip({ verdict, who }: { verdict: CritiqueVerdict; who?: Who }) {
  const v = VERDICT[verdict];
  return (
    <span className="cl-verdict" data-verdict={verdict} title={who ? `${who.name}: ${v.label}` : v.label}>
      <v.icon size={11} strokeWidth={2.2} aria-hidden />
      {who ? who.initials : v.label}
    </span>
  );
}

const STATUS_LABEL: Record<ReelConcept["status"], string> = {
  pitched: "Pitched",
  revised: "Remixed",
  killed: "Killed",
  finalist: "Finalist",
  rendered: "Rendered",
};

/** Mean critic score per field for a concept. */
export function criticMeans(cs: Critique[]) {
  const m = (k: "originality" | "hook" | "specificity" | "truth" | "virality") => (cs.length ? cs.reduce((a, c) => a + c[k], 0) / cs.length : 0);
  return { originality: m("originality"), hook: m("hook"), specificity: m("specificity"), truth: m("truth"), virality: m("virality") };
}

/** A concept's title as a link into the drawer. */
export function ConceptChip({ id, prefix }: { id: string; prefix?: string }) {
  const concept = useCreator((s) => s.lab?.concepts.find((c) => c.id === id));
  const open = useCreator((s) => s.openConcept);
  if (!concept) return <span className="cl-cchip" data-missing>{id}</span>;
  return (
    <button className="cl-cchip" onClick={() => open(id)} data-status={concept.status} title={`Open ${concept.title}`}>
      {prefix && <span className="cl-cchip-prefix">{prefix}</span>}
      {concept.title}
    </button>
  );
}

/** An inline title that opens the drawer. */
function TitleLink({ id }: { id: string }) {
  const title = useCreator((s) => s.lab?.concepts.find((c) => c.id === id)?.title);
  const open = useCreator((s) => s.openConcept);
  return (
    <button className="cl-tlink" onClick={() => open(id)}>
      {title ?? id}
    </button>
  );
}

// ---- section --------------------------------------------------------------------------------------

export function RoomSection() {
  const lab = useCreator((s) => s.lab);
  if (!lab) return null;
  const room = lab.concepts.filter((c) => isRoom(lab, c));
  const creators = lab.agents.filter((a) => a.role === "creator" && a.id !== CONTROL_AGENT).length;
  const critics = lab.agents.filter((a) => a.role === "critic").length;
  const remixes = room.filter((c) => c.parents.length).length;
  const specialists = lab.agents.filter((a) => a.role === "showrunner" || a.role === "critic" || (a.role === "creator" && a.id !== CONTROL_AGENT)).length;
  const counts = [
    `${lab.critiques.length} critique${lab.critiques.length === 1 ? "" : "s"}`,
    remixes ? `${remixes} remix${remixes === 1 ? "" : "es"}` : null,
    `${creators} creators · ${critics} critics`,
  ].filter(Boolean);
  return (
    <section id="cl-room" className="cl-section" aria-labelledby="cl-room-title">
      <SectionHead
        id="cl-room-title"
        title="Your AI creative team"
        sub={
          room.length > 0 ? (
            <>
              {specialists ? numberWord(specialists, true) : "The"} specialists brainstormed {room.length} reel ideas for this listing, critiqued each other and remixed the
              best. The showrunner greenlit {lab.options.finalists === 1 ? "one" : numberWord(lab.options.finalists)}.
            </>
          ) : (
            <>
              {specialists ? numberWord(specialists, true) : "The"} specialists brainstorm reel ideas for this listing, critique each other and remix the best; the
              showrunner greenlights {lab.options.finalists === 1 ? "one" : numberWord(lab.options.finalists)}.
            </>
          )
        }
      >
        {room.length > 0 && <p className="cl-counts">{counts.join(" · ")}</p>}
      </SectionHead>
      <Cast lab={lab} />
      <AgentLanes />
      <div className="cl-room">
        <div className="cl-room-story">
          {lab.status === "running" && <Latest lab={lab} />}
          <Story lab={lab} />
          <Transcript count={lab.feed.filter((m) => m.agent !== CONTROL_AGENT).length} />
        </div>
        <aside className="cl-room-side" aria-label="Concepts">
          <Finalists lab={lab} />
          <OtherConcepts lab={lab} />
          <ControlLine lab={lab} />
        </aside>
      </div>
    </section>
  );
}

// ---- cast: the agents by role -----------------------------------------------------------------------

const Cast = memo(function Cast({ lab }: { lab: LabState }) {
  const speaking = lab.status === "running" ? [...lab.feed].sort((a, b) => a.t - b.t).pop()?.agent : undefined;
  const groups: [string, typeof lab.agents][] = [
    ["Showrunner", lab.agents.filter((a) => a.role === "showrunner")],
    ["Creators", lab.agents.filter((a) => a.role === "creator" && a.id !== CONTROL_AGENT)],
    ["Critics", lab.agents.filter((a) => a.role === "critic")],
  ];
  return (
    <div className="cl-cast" role="list" aria-label="The creative team">
      {groups.map(
        ([label, list]) =>
          list.length > 0 && (
            <div key={label} className="cl-cast-group" role="listitem" data-group={label.toLowerCase()}>
              <p className="cl-cast-label">{label}</p>
              <ul>
                {list.map((a) => {
                  const Icon = agentIcon(a.id);
                  return (
                    <li key={a.id} data-speaking={a.id === speaking || undefined} title={a.lens} style={{ "--chip": dashColor(a.color) } as CSSProperties}>
                      <span className="cl-cast-chip" aria-hidden>
                        <Icon size={14} />
                      </span>
                      <span className="cl-cast-text">
                        <span className="cl-cast-name">{a.name}</span>
                        <span className="cl-cast-job">{AGENT_JOB[a.id] ?? a.lens}</span>
                      </span>
                      <span className="sr-only">: {a.lens}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ),
      )}
    </div>
  );
});

// ---- the live edge while a lab runs -----------------------------------------------------------------

function Latest({ lab }: { lab: LabState }) {
  const agents = useAgents();
  const msgs = useMemo(
    () =>
      [...lab.feed]
        .filter((m) => m.agent !== CONTROL_AGENT)
        .sort((a, b) => a.t - b.t)
        .slice(-3)
        .reverse(),
    [lab.feed],
  );
  if (!msgs.length) {
    return (
      <p className="cl-latest-empty">
        <span className="spinner spinner-sm" aria-hidden /> The showrunner is studying the photos and writing the brief…
      </p>
    );
  }
  return (
    <div className="cl-latest" aria-live="polite">
      <p className="cl-latest-label">
        <span className="live-dot" aria-hidden /> Happening now
      </p>
      <ul>
        {msgs.map((m) => (
          <li key={m.id}>
            <b>{agents(m.agent).name}</b> {m.text}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---- how the room got there -------------------------------------------------------------------------

function Story({ lab }: { lab: LabState }) {
  const steps = useMemo(() => buildStory(lab), [lab]);
  if (!steps.length) return null;
  return (
    <div className="cl-story-wrap">
      <h3 className="cl-h3">How the room got there</h3>
      <ol className="cl-story">
        {steps.map((s, i) => (
          <li key={`${s.kind}-${i}`} className="cl-step" data-kind={s.kind}>
            <time className="cl-step-t num">{Number.isFinite(s.t) ? fmtClock(s.t) : ""}</time>
            <Step s={s} lab={lab} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function StepHead({ title, meta }: { title: string; meta?: string }) {
  return (
    <p className="cl-step-head">
      <span className="cl-step-title">{title}</span>
      {meta && <span className="cl-step-meta">{meta}</span>}
    </p>
  );
}

function Step({ s, lab }: { s: StoryStep; lab: LabState }) {
  const agents = useAgents();
  const conceptOf = useMemo(() => new Map(lab.concepts.map((c) => [c.id, c])), [lab.concepts]);
  switch (s.kind) {
    case "brief": {
      const brief = lab.brief;
      return (
        <div className="cl-step-body">
          <StepHead title="The brief" />
          <p className="cl-step-text">{s.summary}</p>
          {brief && (brief.facts.length > 0 || brief.hero_features.length > 0) && (
            <Disclosure label="Brief details" openLabel="Hide brief details">
              <div className="cl-brief-cols">
                {brief.hero_features.length > 0 && (
                  <div>
                    <p className="cl-mini-label">Hero details</p>
                    <ul className="cl-plain">
                      {brief.hero_features.map((f) => (
                        <li key={f.feature}>{f.feature}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div>
                  <p className="cl-mini-label">Can claim</p>
                  <ul className="cl-plain">
                    {brief.facts.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                  {brief.avoid_claims.length > 0 && (
                    <>
                      <p className="cl-mini-label">Won't claim</p>
                      <ul className="cl-plain">
                        {brief.avoid_claims.map((f) => (
                          <li key={f}>{f}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              </div>
              {brief.tone && <p className="cl-brief-tone">Tone: {brief.tone}</p>}
            </Disclosure>
          )}
        </div>
      );
    }
    case "pitches":
      return (
        <div className="cl-step-body">
          <StepHead title={`${s.concepts.length} pitches from ${numberWord(s.creators)} creator${s.creators === 1 ? "" : "s"}`} />
          <Disclosure label="Show the titles" openLabel="Hide the titles">
            <ul className="cl-titles">
              {s.concepts.map((id) => (
                <li key={id}>
                  <TitleLink id={id} />
                  <span className="cl-titles-by">{agents(conceptOf.get(id)?.agent ?? "").name}</span>
                </li>
              ))}
            </ul>
          </Disclosure>
        </div>
      );
    case "critique":
      return (
        <div className="cl-step-body">
          <StepHead title={s.round <= 1 ? "The critics' picks" : "Second pass, on the remixes"} meta={`${s.notes} critiques`} />
          {s.agreed ? (
            <PickLine pick={s.picks[0]!} who={null} agreedCount={s.picks.length} />
          ) : (
            <ul className="cl-picks">
              {s.picks.map((p) => (
                <li key={p.critic}>
                  <PickLine pick={p} who={agents(p.critic)} />
                </li>
              ))}
            </ul>
          )}
        </div>
      );
    case "remix":
      return (
        <div className="cl-step-body">
          <StepHead title={`${s.remixes} remixes, ${s.steals} of them steals`} />
          <ul className="cl-steals">
            {s.examples.map((e) => (
              <li key={e.child}>
                <span className="cl-steals-who">{agents(e.agent).name}</span> took{" "}
                {e.parents.map((p, i) => (
                  <span key={p}>
                    {i > 0 && " and "}
                    <TitleLink id={p} />
                  </span>
                ))}
                <ArrowRight size={12} className="cl-arrow" aria-label="into" />
                <TitleLink id={e.child} />
              </li>
            ))}
          </ul>
        </div>
      );
    case "vote": {
      const top = s.tally[0]!;
      return (
        <div className="cl-step-body">
          <StepHead title="The vote" />
          <ul className="cl-tally">
            {s.tally.slice(0, 3).map((v) => (
              <li key={v.concept} data-top={v === top || undefined}>
                <TitleLink id={v.concept} />
                <span className="cl-tally-bar" aria-hidden>
                  <span style={{ width: `${(v.votes / s.total) * 100}%` }} />
                </span>
                <span className="cl-tally-n">
                  <span className="num">{v.votes}</span> of <span className="num">{s.total}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      );
    }
    case "greenlight":
      return <GreenlightStep s={s} lab={lab} />;
  }
}

function PickLine({ pick, who, agreedCount }: { pick: CriticPick; who: Who | null; agreedCount?: number }) {
  return (
    <div className="cl-pick">
      <p className="cl-pick-who">{who ? who.name : `All ${agreedCount ? numberWord(agreedCount) : ""} critics agree`}</p>
      <dl className="cl-pick-items">
        {pick.strongest && (
          <div title={pick.strongestWhy}>
            <dt>Strongest</dt>
            <dd>
              <TitleLink id={pick.strongest} />
            </dd>
          </div>
        )}
        {pick.weakest && (
          <div title={pick.weakestWhy}>
            <dt>Weakest</dt>
            <dd>
              <TitleLink id={pick.weakest} />
            </dd>
          </div>
        )}
      </dl>
      {!who && pick.strongestWhy && <p className="cl-pick-why">“{pick.strongestWhy}”</p>}
    </div>
  );
}

function GreenlightStep({ s, lab }: { s: Extract<StoryStep, { kind: "greenlight" }>; lab: LabState }) {
  const planned = useMemo(() => plannedReels(lab), [lab]);
  return (
    <div className="cl-step-body">
      <StepHead title="The greenlight" />
      <ul className="cl-greens">
        {s.picks.map((p) => {
          const reel = planned.find((r) => r.concept?.id === p.concept);
          return (
            <li key={p.concept}>
              {reel ? <ReelBadge id={reel.id} size={18} /> : <span className="cl-greens-mark" aria-hidden />}
              <p>
                <TitleLink id={p.concept} />
                {reel && <span className="cl-greens-label"> · {reel.label}</span>}
                {p.reason && <span className="cl-greens-why">{p.reason}</span>}
              </p>
            </li>
          );
        })}
        {s.passed.map((p) => (
          <li key={p.concept} className="cl-greens-passed">
            <span className="cl-greens-mark" aria-hidden>
              <X size={11} />
            </span>
            <p>
              Passed over: <TitleLink id={p.concept} />
              {p.reason && <span className="cl-greens-why">{p.reason}</span>}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---- the full transcript ------------------------------------------------------------------------------

function Transcript({ count }: { count: number }) {
  if (!count) return null;
  return (
    <Disclosure className="cl-transcript" label={`Full transcript (${count} messages)`} openLabel="Hide the transcript">
      <Feed />
    </Disclosure>
  );
}

const KIND_LABEL: Partial<Record<RoomMessage["kind"], string>> = {
  brief: "Brief",
  pitch: "Pitch",
  critique: "Critique",
  remix: "Remix",
  steal: "Steal",
  vote: "Vote",
  greenlight: "Greenlight",
};

function Feed() {
  const lab = useCreator((s) => s.lab)!;
  const { room } = useMemo(() => groupFeed(lab), [lab]);
  const running = lab.status === "running";
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(running);
  const [behind, setBehind] = useState(0);
  const reduced = useReducedMotion();
  const count = room.reduce((n, g) => n + g.messages.length, 0);
  const lastCount = useRef(count);

  useLayoutEffect(() => {
    const el = scroller.current;
    const grew = count > lastCount.current;
    lastCount.current = count;
    if (!el) return;
    if (pinned.current) {
      el.scrollTo({ top: el.scrollHeight, behavior: reduced || !running ? "auto" : "smooth" });
      setBehind(0);
    } else if (grew) setBehind((n) => n + 1);
  }, [count, reduced, running]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (pinned.current) setBehind(0);
  };

  return (
    <div className="cl-feed">
      <div className="cl-feed-scroll" ref={scroller} onScroll={onScroll} role="log" aria-label="Writers' room transcript" tabIndex={0}>
        {room.map((g) => (
          <div key={g.id} className="cl-feed-group">
            <h4 className="cl-feed-phase">
              <span>{g.label}</span>
              <span className="num">{fmtClock(g.start)}</span>
            </h4>
            {g.messages.map((m) => (
              <Message key={m.id} m={m} />
            ))}
          </div>
        ))}
      </div>
      {behind > 0 && !pinned.current && (
        <button
          className="cl-new-pill"
          onClick={() => {
            pinned.current = true;
            scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: reduced ? "auto" : "smooth" });
            setBehind(0);
          }}
        >
          <ArrowDown size={12} aria-hidden /> New messages
        </button>
      )}
    </div>
  );
}

const Message = memo(function Message({ m }: { m: RoomMessage }) {
  const who = useAgents()(m.agent);
  const critique = useCreator((s) => (m.kind === "critique" ? s.lab?.critiques.find((c) => c.critic === m.agent && c.concept === m.concept) : undefined));
  if (m.kind === "system") {
    return (
      <div className="cl-msg-sys">
        <p>{m.text}</p>
        <time className="num">{fmtClock(m.t)}</time>
      </div>
    );
  }
  const refs = m.refs.filter((r) => r !== m.concept);
  return (
    <article className="cl-msg" data-kind={m.kind}>
      <header className="cl-msg-head">
        <span className="cl-msg-who">{who.name}</span>
        {KIND_LABEL[m.kind] && <span className="cl-kind">{KIND_LABEL[m.kind]}</span>}
        {critique && <VerdictChip verdict={critique.verdict} />}
        <time className="num">{fmtClock(m.t)}</time>
      </header>
      <p className="cl-msg-text">{m.text}</p>
      {(m.concept || refs.length > 0) && (
        <div className="cl-msg-refs">
          {m.concept && <ConceptChip id={m.concept} />}
          {refs.map((r) => (
            <ConceptChip key={r} id={r} />
          ))}
        </div>
      )}
    </article>
  );
});

// ---- finalists and the rest ---------------------------------------------------------------------------

const isRoom = (lab: LabState, c: ReelConcept) => c.agent !== "template" && c.agent !== CONTROL_AGENT && !lab.single_agent.includes(c.id);

function Finalists({ lab }: { lab: LabState }) {
  const planned = useMemo(() => plannedReels(lab), [lab]);
  const leading = useMemo(() => {
    if (lab.finalists.length) return [];
    return lab.concepts
      .filter((c) => isRoom(lab, c) && c.status !== "killed" && lab.critiques.some((x) => x.concept === c.id))
      .sort((a, b) => criticScore(lab, b.id) - criticScore(lab, a.id))
      .slice(0, 2);
  }, [lab]);
  const finalists = lab.finalists.map((id) => lab.concepts.find((c) => c.id === id)).filter((c): c is ReelConcept => !!c);
  const shown = finalists.length ? finalists : leading;
  return (
    <div className="cl-fins">
      <h3 className="cl-h3">{finalists.length ? "Greenlit" : leading.length ? "Leading so far" : "Finalists"}</h3>
      {shown.length === 0 ? (
        <p className="cl-quiet">The showrunner greenlights two concepts after the critique and remix rounds.</p>
      ) : (
        shown.map((c) => {
          const reel = planned.find((p) => p.concept?.id === c.id && lab.finalists.includes(c.id));
          return <FinalistCard key={c.id} c={c} reelId={reel?.id} label={reel?.label} />;
        })
      )}
    </div>
  );
}

function FinalistCard({ c, reelId, label }: { c: ReelConcept; reelId?: string; label?: string }) {
  const who = useAgents()(c.agent);
  const photos = useCreator((s) => s.shoot?.photos);
  const lab = useCreator((s) => s.lab)!;
  const open = useCreator((s) => s.openConcept);
  const first = c.beats[0];
  const score = criticScore(lab, c.id);
  return (
    <article className="cl-fin" data-reel={reelId}>
      <button className="cl-fin-photo" onClick={() => open(c.id)} tabIndex={-1} aria-hidden>
        {first && photos?.[first.view] && <img src={photos[first.view]} alt="" loading="lazy" style={focusStyle(first.focus)} />}
      </button>
      <div className="cl-fin-body">
        {reelId && (
          <p className="cl-fin-reel">
            <ReelBadge id={reelId} size={18} /> {label}
          </p>
        )}
        <p className="cl-fin-hook">
          “<Rich text={c.hook.text} />”
        </p>
        <p className="cl-fin-who">
          <button className="cl-fin-title" onClick={() => open(c.id)}>
            {c.title}
          </button>
          <span> by {who.name}</span>
        </p>
        {score > 0 && (
          <p className="cl-fin-score" title="Mean of the three critics' scores">
            <span className="num">{score.toFixed(1)}</span> / 10 from the critics
          </p>
        )}
      </div>
    </article>
  );
}

function OtherConcepts({ lab }: { lab: LabState }) {
  const agents = useAgents();
  const open = useCreator((s) => s.openConcept);
  const [all, setAll] = useState(false);
  const others = useMemo(
    () =>
      lab.concepts
        .filter((c) => isRoom(lab, c) && !lab.finalists.includes(c.id))
        .map((c) => ({ c, score: criticScore(lab, c.id) }))
        .sort((a, b) => b.score - a.score || a.c.id.localeCompare(b.c.id)),
    [lab],
  );
  if (!others.length) return null;
  const shown = all ? others : others.slice(0, 6);
  return (
    <div className="cl-others">
      <h3 className="cl-h3">
        {others.length} {lab.finalists.length ? "other " : ""}concept{others.length === 1 ? "" : "s"}
      </h3>
      <ul>
        {shown.map(({ c, score }) => (
          <li key={c.id}>
            <button onClick={() => open(c.id)} title={`${c.title} by ${agents(c.agent).name}`}>
              <span className="cl-others-title">{c.title}</span>
              <span className="cl-others-status" data-status={c.status}>
                {STATUS_LABEL[c.status]}
              </span>
              <span className="cl-others-score num">{score > 0 ? score.toFixed(1) : ""}</span>
            </button>
          </li>
        ))}
      </ul>
      {others.length > 6 && (
        <button className="cl-more-btn" onClick={() => setAll((a) => !a)} aria-expanded={all}>
          {all ? "Show fewer" : `Show all ${others.length}`}
        </button>
      )}
    </div>
  );
}

function ControlLine({ lab }: { lab: LabState }) {
  const control = lab.concepts.filter((c) => c.agent === CONTROL_AGENT || lab.single_agent.includes(c.id));
  const phase = lab.phases.find((p) => p.key === "single_agent");
  if (phase?.status === "skipped" || (!phase && !control.length)) return null;
  const pick = lab.feed.find((m) => m.agent === CONTROL_AGENT && m.kind === "note" && m.concept)?.concept ?? control[0]?.id;
  return (
    <p className="cl-control">
      <span className="cl-control-k">Single-agent control</span>
      {control.length ? (
        <>
          {numberWord(control.length, true)} concepts written alone{pick && <>; its pick: </>}
          {pick && <TitleLink id={pick} />}
        </>
      ) : phase?.status === "running" ? (
        "Writing concepts alone…"
      ) : (
        "Runs beside the room with the same brief."
      )}
    </p>
  );
}
