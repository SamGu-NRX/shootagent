// The lab's headline numbers, computed once so the presenter's scenes and the dashboard always agree.
// Launch figures come from the simulated launch (a model fed by the viewer panel), not from a platform.

import { CONTROL_AGENT } from "../phases";
import type { LabState, Reel } from "../types";

export interface ReelStats {
  id: string;
  label: string;
  title: string;
  kind: Reel["kind"];
  concept: string;
  seconds: number;
  /** simulated launch totals */
  views: number;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  follows: number;
  inquiries: number;
  /** views from buyer segments (first-time, local, relocating, investor, downsizer) */
  buyerViews: number;
  /** judges' mean creativity, 1-10 */
  creativity: number;
  /** 0-1, distance from the cliche corpus */
  novelty: number;
  /** viewer panel, 0-1 */
  stopRate: number;
  completion: number;
  watched: number;
}

export interface Story {
  reels: ReelStats[];
  template: ReelStats | null;
  /** the reel with the most simulated views */
  hero: ReelStats | null;
  /** the reel the judges rated most creative */
  mostCreative: ReelStats | null;
  /** hero views / template views */
  viewsX: number | null;
  /** hero novelty / template novelty */
  noveltyX: number | null;
  /** room concepts */
  ideas: number;
  critiques: number;
  remixes: number;
  /** remixes that borrowed from another creator's concept */
  steals: number;
  killed: number;
  truthFlags: number;
  clicheFlags: number;
  creators: number;
  critics: number;
  /** showrunner + creators + critics */
  roomAgents: number;
  judges: number;
  viewers: number;
  /** stock phrases in the hidden corpus */
  cliches: number;
  seconds: number;
  usd: number;
  calls: number;
  reelsRendered: number;
  usdPerReel: number;
  followers: number;
  hours: number;
  /** listing price in dollars, when the listing has one */
  price: number | null;
  /** assumption for the economics line, shown wherever it is used */
  commissionRate: number;
  /** price x commissionRate */
  commission: number | null;
  kendallW: number | null;
}

const BUYER = /buyer|relocat|downsiz|investor/i;
export const COMMISSION_RATE = 0.025;

/** "$489,000" -> 489000 */
export function parsePrice(price: string | null | undefined): number | null {
  if (!price) return null;
  const m = /([\d.,]+)\s*([kKmM])?/.exec(price.replace(/\s/g, ""));
  if (!m) return null;
  const n = parseFloat(m[1]!.replace(/,/g, ""));
  if (!Number.isFinite(n)) return null;
  const mult = m[2] ? (/k/i.test(m[2]) ? 1e3 : 1e6) : 1;
  return n * mult;
}

export function buildStory(lab: LabState, listingPrice?: string | null): Story {
  const ev = lab.evaluation;
  const sims = new Map(lab.simulations.map((s) => [s.reel, s]));
  const summaries = new Map((ev?.summaries ?? []).map((s) => [s.reel, s]));
  const buyers = new Set((ev?.viewers ?? []).filter((v) => BUYER.test(v.segment)).map((v) => v.id));
  const reels: ReelStats[] = lab.reels.map((r) => {
    const sim = sims.get(r.id);
    const s = summaries.get(r.id);
    const tot = sim?.totals;
    const novelty = ev?.novelty.find((n) => n.kind === "reel" && n.id === r.id)?.novelty ?? s?.novelty ?? 0;
    return {
      id: r.id,
      label: r.label,
      title: r.title,
      kind: r.kind,
      concept: r.concept,
      seconds: r.seconds,
      views: tot?.views ?? 0,
      likes: tot?.likes ?? 0,
      comments: tot?.comments ?? 0,
      shares: tot?.shares ?? 0,
      saves: tot?.saves ?? 0,
      follows: tot?.follows ?? 0,
      inquiries: tot?.inquiries ?? 0,
      buyerViews: sim ? Object.entries(sim.by_segment).reduce((a, [k, v]) => a + (buyers.has(k) ? v : 0), 0) : 0,
      creativity: s?.creativity ?? 0,
      novelty,
      stopRate: s?.stop_rate ?? 0,
      completion: s?.completion_rate ?? 0,
      watched: s?.avg_watch_fraction ?? 0,
    };
  });
  const template = reels.find((r) => r.kind === "baseline") ?? null;
  const hero = reels.length ? [...reels].sort((a, b) => b.views - a.views)[0]! : null;
  const mostCreative = reels.length ? [...reels].sort((a, b) => b.creativity - a.creativity)[0]! : null;

  const room = lab.concepts.filter((c) => c.agent !== "template" && c.agent !== CONTROL_AGENT && !lab.single_agent.includes(c.id));
  const byId = new Map(lab.concepts.map((c) => [c.id, c]));
  const remixes = room.filter((c) => c.parents.length);
  const steals = remixes.filter((c) => c.parents.some((p) => byId.get(p) && byId.get(p)!.agent !== c.agent)).length;
  const price = parsePrice(listingPrice);
  const rendered = lab.reels.length;
  const sim0 = lab.simulations[0];
  return {
    reels,
    template,
    hero,
    mostCreative,
    viewsX: hero && template && template.views > 0 ? hero.views / template.views : null,
    noveltyX: hero && template && template.novelty > 0 ? hero.novelty / template.novelty : null,
    ideas: room.length,
    critiques: lab.critiques.length,
    remixes: remixes.length,
    steals,
    killed: room.filter((c) => c.status === "killed").length,
    truthFlags: lab.critiques.reduce((a, c) => a + c.truth_flags.length, 0),
    clicheFlags: lab.critiques.reduce((a, c) => a + c.cliche_flags.length, 0),
    creators: lab.agents.filter((a) => a.role === "creator" && a.id !== CONTROL_AGENT).length,
    critics: lab.agents.filter((a) => a.role === "critic").length,
    roomAgents: lab.agents.filter((a) => a.role === "showrunner" || a.role === "critic" || (a.role === "creator" && a.id !== CONTROL_AGENT)).length,
    judges: ev?.judges.filter((j) => j.id !== "head_judge").length ?? 0,
    viewers: ev?.viewers.length ?? 0,
    cliches: ev?.map?.points.filter((p) => p.kind === "cliche").length ?? 0,
    seconds: lab.elapsed,
    usd: lab.usage.usd,
    calls: lab.usage.calls,
    reelsRendered: rendered,
    usdPerReel: rendered ? lab.usage.usd / rendered : 0,
    followers: sim0?.followers ?? lab.options.followers,
    hours: sim0?.hours ?? lab.options.hours,
    price,
    commissionRate: COMMISSION_RATE,
    commission: price != null ? price * COMMISSION_RATE : null,
    kendallW: ev?.agreement.kendall_w ?? null,
  };
}
