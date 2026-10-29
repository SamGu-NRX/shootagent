import {
  ArrowDown,
  Ban,
  Check,
  Clapperboard,
  Cpu,
  GitMerge,
  Lightbulb,
  type LucideIcon,
  MessageCircle,
  RotateCw,
  ScrollText,
  Shuffle,
  Sparkles,
  Star,
  Vote,
  X,
} from "lucide-react";
import { memo, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { CONTROL_AGENT, groupFeed } from "./phases";
import { useCreator } from "./store";
import type { Critique, CritiqueVerdict, LabState, ReelConcept, RoomKind, RoomMessage } from "./types";
import { Avatar, fmtClock, focusStyle, numberWord, onDark, ReelBadge, reelColors, SectionHead, useAgents, useReducedMotion, type Who } from "./ui";

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
      <v.icon size={11} strokeWidth={2.4} aria-hidden />
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

export function ConceptChip({ id, prefix }: { id: string; prefix?: string }) {
  const concept = useCreator((s) => s.lab?.concepts.find((c) => c.id === id));
  const open = useCreator((s) => s.openConcept);
  const who = useAgents()(concept?.agent ?? "");
  if (!concept) return <span className="cl-cchip" data-missing>{id}</span>;
  return (
    <button className="cl-cchip" onClick={() => open(id)} data-status={concept.status} title={`Open ${concept.title}`}>
      {prefix && <span className="cl-cchip-prefix">{prefix}</span>}
      <span className="cl-cchip-dot" style={{ background: onDark(who.color) }} aria-hidden />
      <span className="cl-cchip-title">{concept.title}</span>
    </button>
  );
}

// ---- section --------------------------------------------------------------------------------------

export function RoomSection() {
  const lab = useCreator((s) => s.lab);
  if (!lab) return null;
  const room = lab.concepts.filter((c) => c.agent !== "template" && c.agent !== CONTROL_AGENT);
  const remixes = room.filter((c) => c.parents.length).length;
  const creators = lab.agents.filter((a) => a.role === "creator" && a.id !== CONTROL_AGENT).length;
  const critics = lab.agents.filter((a) => a.role === "critic").length;
  const roomMessages = lab.feed.filter((m) => m.agent !== CONTROL_AGENT).length;
  return (
    <section id="cl-room" className="cl-section" aria-labelledby="cl-room-title">
      <SectionHead
        n="01"
        id="cl-room-title"
        title="Writers' room"
        sub={`${creators ? numberWord(creators, true) : "The"} creators pitch, ${critics ? numberWord(critics) : "the"} critics tear the pitches apart, everyone remixes and steals, and the showrunner greenlights the finalists.`}
      >
        <dl className="cl-stats">
          <div>
            <dt>Concepts</dt>
            <dd className="num">{room.length}</dd>
          </div>
          <div>
            <dt>Critiques</dt>
            <dd className="num">{lab.critiques.length}</dd>
          </div>
          <div>
            <dt>Remixes</dt>
            <dd className="num">{remixes}</dd>
          </div>
          <div>
            <dt>Messages</dt>
            <dd className="num">{roomMessages}</dd>
          </div>
        </dl>
      </SectionHead>
      <div className="cl-stage">
        <Roster lab={lab} />
        <Feed />
        <Board />
      </div>
      <ControlLane />
    </section>
  );
}

// ---- roster ---------------------------------------------------------------------------------------

const Roster = memo(function Roster({ lab }: { lab: LabState }) {
  const last = lab.status === "running" ? lab.feed[lab.feed.length - 1]?.agent : undefined;
  const groups: [string, typeof lab.agents][] = [
    ["Showrunner", lab.agents.filter((a) => a.role === "showrunner")],
    ["Creators", lab.agents.filter((a) => a.role === "creator" && a.id !== CONTROL_AGENT)],
    ["Critics", lab.agents.filter((a) => a.role === "critic")],
  ];
  const finalistAgents = new Set(lab.concepts.filter((c) => lab.finalists.includes(c.id)).map((c) => c.agent));
  const stat = (id: string, role: string) => {
    if (role === "creator") {
      const n = lab.concepts.filter((c) => c.agent === id).length;
      return n ? `${n} concept${n === 1 ? "" : "s"}` : "";
    }
    if (role === "critic") {
      const n = lab.critiques.filter((c) => c.critic === id).length;
      return n ? `${n} notes` : "";
    }
    const n = lab.feed.filter((m) => m.agent === id).length;
    return n ? `${n} messages` : "";
  };
  return (
    <aside className="cl-roster cl-panel" aria-label="Who is in the room">
      <div className="cl-panel-head">
        <h3 className="cl-label">The room</h3>
        <span className="cl-panel-count num">{groups.reduce((n, [, g]) => n + g.length, 0)} agents</span>
      </div>
      <div className="cl-roster-body">
        {groups.map(
          ([label, list]) =>
            list.length > 0 && (
              <div key={label} className="cl-roster-group">
                <h4 className="cl-roster-label">{label}</h4>
                <ul>
                  {list.map((a) => (
                    <li key={a.id} className="cl-agent" data-speaking={a.id === last || undefined} title={`${a.name}: ${a.lens}`}>
                      <Avatar who={a} size={32} />
                      <div className="cl-agent-text">
                        <p className="cl-agent-name">
                          <span>{a.name}</span>
                          {finalistAgents.has(a.id) && <Star size={11} className="cl-agent-star" aria-label="has a finalist" />}
                          <span className="cl-agent-stat num">{stat(a.id, a.role)}</span>
                        </p>
                        <p className="cl-agent-lens">{a.lens}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ),
        )}
      </div>
    </aside>
  );
});

// ---- feed -----------------------------------------------------------------------------------------

const KIND: Record<RoomKind, { label: string; icon: LucideIcon }> = {
  brief: { label: "Brief", icon: ScrollText },
  pitch: { label: "Pitch", icon: Lightbulb },
  critique: { label: "Critique", icon: MessageCircle },
  remix: { label: "Remix", icon: GitMerge },
  steal: { label: "Steal", icon: Shuffle },
  vote: { label: "Vote", icon: Vote },
  greenlight: { label: "Greenlight", icon: Clapperboard },
  note: { label: "Note", icon: MessageCircle },
  system: { label: "System", icon: Cpu },
};

function Feed() {
  const lab = useCreator((s) => s.lab)!;
  const { room } = useMemo(() => groupFeed(lab), [lab]);
  const running = lab.status === "running";
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(running);
  const seen = useRef<Set<string> | null>(null);
  if (seen.current === null) seen.current = new Set(lab.feed.map((m) => m.id));
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
    <div className="cl-feed cl-panel">
      <div className="cl-panel-head">
        <h3 className="cl-label">
          {running && <span className="live-dot" aria-hidden />} Room feed
        </h3>
        <span className="cl-panel-count num">{count} messages</span>
      </div>
      <div className="cl-feed-scroll" ref={scroller} onScroll={onScroll} role="log" aria-live="polite" aria-relevant="additions" aria-label="Writers' room messages">
        {room.length === 0 && (
          <p className="cl-empty-line">
            <span className="spinner spinner-sm" aria-hidden /> The showrunner is reading the photos and writing the brief…
          </p>
        )}
        {room.map((g) => (
          <div key={g.id} className="cl-feed-group">
            <h4 className="cl-feed-phase">
              <span>{g.label}</span>
              <span className="num">{fmtClock(g.start)}</span>
            </h4>
            {g.messages.map((m) => (
              <Message key={m.id} m={m} fresh={!seen.current!.has(m.id)} />
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

const Message = memo(function Message({ m, fresh }: { m: RoomMessage; fresh: boolean }) {
  const who = useAgents()(m.agent);
  const critique = useCreator((s) => (m.kind === "critique" ? s.lab?.critiques.find((c) => c.critic === m.agent && c.concept === m.concept) : undefined));
  const kind = KIND[m.kind];
  if (m.kind === "system") {
    return (
      <div className="cl-msg-sys" data-new={fresh || undefined}>
        <Cpu size={12} aria-hidden />
        <p>
          <span className="cl-msg-sys-who">{who.name}</span> {m.text}
        </p>
        <time className="num">{fmtClock(m.t)}</time>
      </div>
    );
  }
  return (
    <article className="cl-msg" data-kind={m.kind} data-new={fresh || undefined}>
      <Avatar who={who} size={28} />
      <div className="cl-msg-body">
        <header className="cl-msg-head">
          <span className="cl-msg-who">{who.name}</span>
          {m.kind !== "note" && (
            <span className="cl-kind" data-kind={m.kind}>
              <kind.icon size={10} strokeWidth={2.2} aria-hidden />
              {kind.label}
            </span>
          )}
          {critique && <VerdictChip verdict={critique.verdict} />}
          <time className="num">{fmtClock(m.t)}</time>
        </header>
        <p className="cl-msg-text">{m.text}</p>
        {m.kind === "brief" && <BriefCard />}
        {critique && (
          <p className="cl-msg-scores num" aria-label="Scores out of 10">
            {(["originality", "hook", "specificity", "truth", "virality"] as const).map((k) => (
              <span key={k}>
                {k.slice(0, k === "truth" ? 5 : 4)} <b>{critique[k]}</b>
              </span>
            ))}
          </p>
        )}
        <MessageRefs m={m} />
      </div>
    </article>
  );
});

function MessageRefs({ m }: { m: RoomMessage }) {
  const refs = m.refs.filter((r) => r !== m.concept);
  if (m.kind === "steal" || m.kind === "remix") {
    return (
      <div className="cl-msg-refs">
        {m.concept && <ConceptChip id={m.concept} />}
        {refs.length > 0 && <span className="cl-msg-from">{m.kind === "steal" ? "steals from" : "remixes"}</span>}
        {refs.map((r) => (
          <ConceptChip key={r} id={r} />
        ))}
      </div>
    );
  }
  if (m.kind === "greenlight") {
    const ids = m.refs.length ? m.refs : m.concept ? [m.concept] : [];
    return (
      <div className="cl-msg-refs">
        {ids.map((r) => (
          <ConceptChip key={r} id={r} />
        ))}
      </div>
    );
  }
  if (!m.concept && !refs.length) return null;
  return (
    <div className="cl-msg-refs">
      {m.concept && <ConceptChip id={m.concept} prefix={m.kind === "vote" ? "for" : undefined} />}
      {refs.map((r) => (
        <ConceptChip key={r} id={r} />
      ))}
    </div>
  );
}

function BriefCard() {
  const brief = useCreator((s) => s.lab?.brief);
  const photos = useCreator((s) => s.shoot?.photos);
  if (!brief) return null;
  return (
    <div className="cl-brief">
      {brief.hero_features.length > 0 && (
        <ul className="cl-brief-heroes">
          {brief.hero_features.slice(0, 5).map((f) => (
            <li key={f.view + f.feature} title={`${f.feature}: ${f.why}`}>
              <span className="cl-brief-thumb">{photos?.[f.view] && <img src={photos[f.view]} alt="" loading="lazy" />}</span>
              <span className="cl-brief-cap">{f.feature}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="cl-brief-claims">
        <p>
          <span className="cl-brief-k">Can claim</span>
          {brief.facts.slice(0, 5).join(" · ")}
          {brief.facts.length > 5 && <span className="muted"> · +{brief.facts.length - 5}</span>}
        </p>
        <p>
          <span className="cl-brief-k" data-tone="avoid">
            <Ban size={11} aria-hidden /> Won't claim
          </span>
          {brief.avoid_claims.slice(0, 4).join(" · ")}
          {brief.avoid_claims.length > 4 && <span className="muted"> · +{brief.avoid_claims.length - 4}</span>}
        </p>
      </div>
    </div>
  );
}

// ---- concept wall ---------------------------------------------------------------------------------

const STATUS_RANK: Record<ReelConcept["status"], number> = { rendered: 0, finalist: 0, pitched: 1, revised: 1, killed: 2 };

function Board() {
  const lab = useCreator((s) => s.lab)!;
  const concepts = useMemo(() => {
    // the card the room talked about last rises to the top, so the wall follows the feed
    const active = new Map<string, number>();
    for (const m of lab.feed) for (const id of [m.concept, ...m.refs]) if (id) active.set(id, Math.max(active.get(id) ?? 0, m.t));
    const room = lab.concepts.filter((c) => c.agent !== "template" && c.agent !== CONTROL_AGENT && !lab.single_agent.includes(c.id));
    return room.sort((a, b) => {
      const fa = lab.finalists.indexOf(a.id);
      const fb = lab.finalists.indexOf(b.id);
      if (fa >= 0 || fb >= 0) return (fa < 0 ? 99 : fa) - (fb < 0 ? 99 : fb);
      return STATUS_RANK[a.status] - STATUS_RANK[b.status] || (active.get(b.id) ?? 0) - (active.get(a.id) ?? 0);
    });
  }, [lab.concepts, lab.feed, lab.finalists, lab.single_agent]);
  const killed = concepts.filter((c) => c.status === "killed").length;
  return (
    <div className="cl-board cl-panel">
      <div className="cl-panel-head">
        <h3 className="cl-label">Concept wall</h3>
        <span className="cl-panel-count num">
          {concepts.length} concepts{lab.finalists.length ? ` · ${lab.finalists.length} greenlit` : ""}
          {killed ? ` · ${killed} killed` : ""}
        </span>
      </div>
      <div className="cl-board-scroll">
        {concepts.length === 0 ? (
          <p className="cl-empty-line">Pitches land here as the creators post them.</p>
        ) : (
          <ul className="cl-cards">
            {concepts.map((c) => (
              <li key={c.id}>
                <ConceptCard c={c} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export function Filmstrip({ c, max = 8 }: { c: ReelConcept; max?: number }) {
  const photos = useCreator((s) => s.shoot?.photos);
  const beats = c.beats.slice(0, max);
  return (
    <div className="cl-strip" aria-hidden>
      {beats.map((b, i) => (
        <span key={i} className="cl-strip-cell" style={{ flexGrow: Math.max(0.6, b.seconds) }}>
          {photos?.[b.view] && <img src={photos[b.view]} alt="" loading="lazy" style={focusStyle(b.focus)} />}
        </span>
      ))}
    </div>
  );
}

const ConceptCard = memo(function ConceptCard({ c }: { c: ReelConcept }) {
  const agents = useAgents();
  const who = agents(c.agent);
  const open = useCreator((s) => s.openConcept);
  const critiques = useCreator((s) => s.lab?.critiques);
  const reels = useCreator((s) => s.lab?.reels);
  const concepts = useCreator((s) => s.lab?.concepts);
  const children = useMemo(() => (concepts ?? []).filter((k) => k.parents.includes(c.id)), [concepts, c.id]);
  const mine = useMemo(() => (critiques ?? []).filter((x) => x.concept === c.id), [critiques, c.id]);
  const means = criticMeans(mine);
  const reel = reels?.find((r) => r.concept === c.id);
  const colors = reelColors(reels ?? []);
  const finalist = c.status === "finalist" || c.status === "rendered";
  return (
    <article className="cl-card" data-status={c.status} data-finalist={finalist || undefined} style={reel ? ({ "--reel": colors[reel.id] } as CSSProperties) : undefined}>
      <Filmstrip c={c} />
      <div className="cl-card-body">
        <header className="cl-card-head">
          <Avatar who={who} size={20} />
          <span className="cl-card-who">{who.name}</span>
          <span className="cl-card-round num">{c.parents.length ? "Remix" : `R${c.round}`}</span>
          <span className="cl-card-status" data-status={c.status}>
            {reel ? (
              <>
                <ReelBadge id={reel.id} color={colors[reel.id]!} size={16} /> {reel.label}
              </>
            ) : (
              <>
                {c.status === "killed" && <X size={11} strokeWidth={2.4} aria-hidden />}
                {c.status === "finalist" && <Sparkles size={11} aria-hidden />}
                {STATUS_LABEL[c.status]}
              </>
            )}
          </span>
        </header>
        <h4 className="cl-card-title">
          <button onClick={() => open(c.id)}>{c.title}</button>
        </h4>
        <p className="cl-card-hook">“{c.hook.text}”</p>
        <p className="cl-card-format">
          {c.format} · <span className="num">{conceptSeconds(c).toFixed(1)} s</span>
        </p>
        {mine.length > 0 ? (
          <>
            <dl className="cl-card-scores">
              {(
                [
                  ["Orig", means.originality],
                  ["Hook", means.hook],
                  ["Spec", means.specificity],
                  ["Truth", means.truth],
                  ["Viral", means.virality],
                ] as const
              ).map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd className="num" data-low={v < 5 || undefined}>
                    {v.toFixed(1)}
                  </dd>
                  <span className="cl-meter" aria-hidden>
                    <span style={{ width: `${v * 10}%` }} />
                  </span>
                </div>
              ))}
            </dl>
            <div className="cl-card-foot">
              <span className="cl-card-verdicts">
                {mine.map((x) => (
                  <VerdictChip key={x.critic} verdict={x.verdict} who={agents(x.critic)} />
                ))}
              </span>
              {(children?.length ?? 0) > 0 && <span className="cl-card-child">→ {children!.map((k) => k.title).join(", ")}</span>}
            </div>
          </>
        ) : (
          <p className="cl-card-waiting">Waiting for the critics</p>
        )}
      </div>
    </article>
  );
});

// ---- single-agent control -------------------------------------------------------------------------

function ControlLane() {
  const lab = useCreator((s) => s.lab)!;
  const agent = useAgents()(CONTROL_AGENT);
  const open = useCreator((s) => s.openConcept);
  const phase = lab.phases.find((p) => p.key === "single_agent");
  const concepts = lab.concepts.filter((c) => lab.single_agent.includes(c.id) || c.agent === CONTROL_AGENT);
  const diversity = lab.evaluation?.diversity ?? {};
  const room = diversity.room;
  const single = diversity.single_agent;
  if (phase?.status === "skipped" || (!phase && !concepts.length)) return null;
  return (
    <div className="cl-control">
      <div className="cl-control-who">
        <Avatar who={agent} size={30} />
        <div>
          <p className="cl-control-name">Single-agent control</p>
          <p className="cl-control-lens">{agent.lens ?? "One agent, one pass, the same brief, no room."}</p>
        </div>
      </div>
      <ul className="cl-control-concepts">
        {concepts.length === 0 ? (
          <li className="cl-empty-line">{phase?.status === "running" ? "Writing concepts alone…" : "Runs beside the room."}</li>
        ) : (
          concepts.map((c) => (
            <li key={c.id}>
              <button className="cl-mini" onClick={() => open(c.id)} data-status={c.status}>
                <Filmstrip c={c} max={6} />
                <span className="cl-mini-title">{c.title}</span>
                <span className="cl-mini-hook">“{c.hook.text}”</span>
              </button>
            </li>
          ))
        )}
      </ul>
      <div className="cl-control-div" aria-label="Concept diversity">
        <p className="cl-label">Idea diversity</p>
        {room != null && single != null ? (
          <>
            <DivBar label="Room" value={room} max={Math.max(room, single)} tone="room" />
            <DivBar label="Single agent" value={single} max={Math.max(room, single)} tone="single" />
            <p className="cl-control-note">Mean distance between concepts (0 = all the same idea). Higher is more varied.</p>
          </>
        ) : (
          <p className="cl-control-note">Measured after the reels are scored for novelty.</p>
        )}
      </div>
    </div>
  );
}

function DivBar({ label, value, max, tone }: { label: string; value: number; max: number; tone: "room" | "single" }) {
  return (
    <div className="cl-divbar" data-tone={tone}>
      <span className="cl-divbar-label">{label}</span>
      <span className="cl-divbar-track">
        <span style={{ width: `${(value / (max || 1)) * 100}%` }} />
      </span>
      <span className="cl-divbar-v num">{value.toFixed(2)}</span>
    </div>
  );
}
