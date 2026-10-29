// "How the room got there": the writers' room condensed into its key moments (brief, pitches, the critics'
// picks, remixes and steals, the vote, the greenlight), built from LabState alone so it works on any lab and
// grows while one runs. The full transcript stays one click away.

import { CONTROL_AGENT } from "./phases";
import type { Critique, LabState, ReelConcept, RoomMessage } from "./types";

export interface CriticPick {
  critic: string;
  strongest: string | null;
  weakest: string | null;
  strongestWhy: string;
  weakestWhy: string;
}

export interface StealExample {
  agent: string;
  child: string;
  parents: string[];
}

export type StoryStep =
  | { kind: "brief"; t: number; agent: string; summary: string; heroes: { view: string; label: string; full: string }[] }
  | { kind: "pitches"; t: number; concepts: string[]; creators: number }
  | { kind: "critique"; t: number; round: number; picks: CriticPick[]; agreed: boolean; notes: number }
  | { kind: "remix"; t: number; remixes: number; steals: number; examples: StealExample[] }
  | { kind: "vote"; t: number; tally: { concept: string; votes: number }[]; total: number }
  | { kind: "greenlight"; t: number; picks: { concept: string; reason: string }[]; passed: { concept: string; reason: string }[] };

// ---- text helpers ---------------------------------------------------------------------------------

/** The first sentence of a paragraph. */
export function firstSentence(text: string): string {
  const s = text.trim();
  const m = /^(.+?[.!?])(\s+|$)/s.exec(s);
  return (m ? m[1]! : s).trim();
}

/** A hero detail short enough for a chip: the part before a colon, else the first words. */
export function shortFeature(text: string, max = 34): string {
  const t = text.trim();
  const head = t.split(":")[0]!.trim();
  const base = head.length <= max ? head : t;
  if (base.length <= max) return base.replace(/[.,;]$/, "");
  const cut = base.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > 12 ? cut.slice(0, space) : cut).replace(/[,;:]$/, "")}…`;
}

/** A pick or greenlight message without its "Strongest: c05." / 'Greenlit: c17, "Title".' preamble. */
export function cleanReason(text: string): string {
  let s = text.trim();
  s = s.replace(/^(strongest|weakest)\s*:\s*c?\w+\s*[.:,-]?\s*/i, "");
  s = s.replace(/^(greenlit|wildcard|pick|finalist)\s*:\s*\w+\s*,?\s*(?:"[^"]*"|'[^']*'|“[^”]*”)?\s*\.?\s*/i, "");
  s = s.replace(/^this is (my pick|the wildcard|our wildcard|my wildcard)\.\s*/i, "");
  s = s.replace(/^[a-z]?\d+\s*,?\s*(?:"[^"]*"|'[^']*'|“[^”]*”)\s*/i, "");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ---- the story --------------------------------------------------------------------------------------

const isRoomConcept = (lab: Pick<LabState, "single_agent">, c: ReelConcept) =>
  c.agent !== "template" && c.agent !== CONTROL_AGENT && !lab.single_agent.includes(c.id);

const meanScore = (cs: Critique[]) =>
  cs.length ? cs.reduce((a, c) => a + (c.originality + c.hook + c.specificity + c.truth + c.virality) / 5, 0) / cs.length : 0;

/** Mean of every critic's five scores for a concept (0 when nobody critiqued it). */
export function criticScore(lab: Pick<LabState, "critiques">, conceptId: string): number {
  return meanScore(lab.critiques.filter((c) => c.concept === conceptId));
}

function critiqueSteps(lab: LabState, roles: Map<string, string>, conceptOf: Map<string, ReelConcept>): StoryStep[] {
  const critics = lab.agents.filter((a) => a.role === "critic").map((a) => a.id);
  const pickMsgs = lab.feed.filter((m) => roles.get(m.agent) === "critic" && m.kind === "critique" && m.concept && /^(strongest|weakest)\b/i.test(m.text.trim()));
  const byRound = new Map<number, RoomMessage[]>();
  for (const m of pickMsgs) {
    const round = conceptOf.get(m.concept!)?.round ?? 1;
    byRound.set(round, [...(byRound.get(round) ?? []), m]);
  }
  const rounds = [...new Set(lab.concepts.filter((c) => isRoomConcept(lab, c)).map((c) => c.round))].sort((a, b) => a - b);
  const out: StoryStep[] = [];
  for (const round of rounds) {
    const ids = new Set(lab.concepts.filter((c) => isRoomConcept(lab, c) && c.round === round).map((c) => c.id));
    const roundCritiques = lab.critiques.filter((c) => ids.has(c.concept));
    const msgs = byRound.get(round) ?? [];
    let picks: CriticPick[];
    if (msgs.length) {
      picks = critics
        .map((critic) => {
          const mine = msgs.filter((m) => m.agent === critic);
          const strong = mine.find((m) => /^strongest/i.test(m.text.trim()));
          const weak = mine.find((m) => /^weakest/i.test(m.text.trim()));
          return {
            critic,
            strongest: strong?.concept ?? null,
            weakest: weak?.concept ?? null,
            strongestWhy: strong ? firstSentence(cleanReason(strong.text)) : "",
            weakestWhy: weak ? firstSentence(cleanReason(weak.text)) : "",
          };
        })
        .filter((p) => p.strongest || p.weakest);
    } else {
      // no explicit picks: each critic's best and worst scores this round
      picks = critics
        .map((critic): CriticPick | null => {
          const mine = roundCritiques.filter((c) => c.critic === critic);
          if (!mine.length) return null;
          const ranked = [...mine].sort((a, b) => meanScore([b]) - meanScore([a]));
          const best = ranked[0]!;
          const worst = ranked[ranked.length - 1]!;
          return { critic, strongest: best.concept, weakest: worst.concept, strongestWhy: firstSentence(best.notes), weakestWhy: firstSentence(worst.notes) };
        })
        .filter((p): p is CriticPick => !!p);
    }
    if (!picks.length) continue;
    const agreed = picks.length > 1 && picks.every((p) => p.strongest === picks[0]!.strongest && p.weakest === picks[0]!.weakest);
    const times = msgs.length ? msgs.map((m) => m.t) : lab.feed.filter((m) => m.kind === "critique" && m.concept && ids.has(m.concept)).map((m) => m.t);
    out.push({ kind: "critique", t: times.length ? Math.min(...times) : Infinity, round, picks, agreed, notes: roundCritiques.length });
  }
  return out;
}

/** The room's key moments in the order they happened, as far as the lab has got. */
export function buildStory(lab: LabState): StoryStep[] {
  const roles = new Map(lab.agents.map((a) => [a.id, a.role]));
  const conceptOf = new Map(lab.concepts.map((c) => [c.id, c]));
  const room = lab.concepts.filter((c) => isRoomConcept(lab, c));
  const firstT = (pred: (m: RoomMessage) => boolean) => lab.feed.reduce((t, m) => (pred(m) && m.t < t ? m.t : t), Infinity);
  const steps: StoryStep[] = [];

  const briefMsg = lab.feed.find((m) => m.kind === "brief");
  if (lab.brief || briefMsg) {
    steps.push({
      kind: "brief",
      t: briefMsg?.t ?? 0,
      agent: briefMsg?.agent ?? "showrunner",
      summary: firstSentence(lab.brief?.summary || briefMsg?.text || ""),
      heroes: (lab.brief?.hero_features ?? []).slice(0, 3).map((f) => ({ view: f.view, label: shortFeature(f.feature, 28), full: f.feature })),
    });
  }

  const pitched = room.filter((c) => c.round <= 1);
  if (pitched.length) {
    steps.push({
      kind: "pitches",
      t: firstT((m) => m.kind === "pitch" && m.agent !== CONTROL_AGENT),
      concepts: pitched.map((c) => c.id),
      creators: new Set(pitched.map((c) => c.agent)).size,
    });
  }

  steps.push(...critiqueSteps(lab, roles, conceptOf));

  const remixed = room.filter((c) => c.parents.length > 0);
  if (remixed.length) {
    const steals = lab.feed.filter((m) => m.kind === "steal" && m.concept);
    const stolen = (c: ReelConcept) => c.parents.some((p) => conceptOf.get(p)?.agent !== c.agent);
    const examples = remixed
      .filter(stolen)
      .sort((a, b) => Number(lab.finalists.includes(b.id)) - Number(lab.finalists.includes(a.id)))
      .slice(0, 3)
      .map((c) => ({ agent: c.agent, child: c.id, parents: c.parents.filter((p) => conceptOf.get(p)?.agent !== c.agent) }));
    steps.push({
      kind: "remix",
      t: firstT((m) => m.kind === "remix" || m.kind === "steal"),
      remixes: remixed.length,
      steals: steals.length || remixed.filter(stolen).length,
      examples,
    });
  }

  const votes = lab.feed.filter((m) => m.kind === "vote" && m.concept && (roles.get(m.agent) === "creator" || roles.get(m.agent) === "showrunner") && m.agent !== CONTROL_AGENT);
  if (votes.length) {
    const counts = new Map<string, number>();
    for (const v of votes) counts.set(v.concept!, (counts.get(v.concept!) ?? 0) + 1);
    const tally = [...counts.entries()].map(([concept, n]) => ({ concept, votes: n })).sort((a, b) => b.votes - a.votes);
    steps.push({ kind: "vote", t: Math.min(...votes.map((v) => v.t)), tally, total: votes.length });
  }

  const gl = lab.feed.filter((m) => m.kind === "greenlight");
  if (gl.length || lab.finalists.length) {
    const picks = lab.finalists.map((id) => {
      const m = gl.find((x) => x.concept === id) ?? gl.find((x) => x.refs.includes(id) && !x.concept);
      return { concept: id, reason: m ? firstTwo(cleanReason(m.text)) : "" };
    });
    const passed = gl
      .filter((m) => !m.concept || !lab.finalists.includes(m.concept))
      .flatMap((m) => {
        const id = m.concept ?? m.refs.find((r) => !lab.finalists.includes(r));
        return id && !lab.finalists.includes(id) ? [{ concept: id, reason: firstTwo(cleanReason(m.text)) }] : [];
      });
    steps.push({ kind: "greenlight", t: gl.length ? Math.min(...gl.map((m) => m.t)) : Infinity, picks, passed });
  }

  return steps.sort((a, b) => a.t - b.t);
}

/** Up to two sentences. */
function firstTwo(text: string): string {
  const one = firstSentence(text);
  const rest = text.slice(text.indexOf(one) + one.length).trim();
  return rest ? `${one} ${firstSentence(rest)}` : one;
}
