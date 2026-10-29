// How each agent looks on the presenter's stage: an icon for its job, in the walkthrough's ink.

import {
  Activity,
  BookOpen,
  Clapperboard,
  Crown,
  Gavel,
  Laugh,
  LayoutTemplate,
  Repeat2,
  Scissors,
  Search,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  User,
  type LucideIcon,
} from "lucide-react";
import { CONTROL_AGENT } from "../phases";
import type { AgentCard, LabState } from "../types";

const ICONS: Record<string, LucideIcon> = {
  showrunner: Crown,
  storyteller: BookOpen,
  contrarian: Repeat2,
  comedian: Laugh,
  cinematographer: Clapperboard,
  detective: Search,
  trend_scout: TrendingUp,
  cliche_hunter: Scissors,
  fact_checker: ShieldCheck,
  algorithm: Activity,
  single_agent: User,
  template: LayoutTemplate,
  head_judge: Gavel,
};

/** The icon for an agent id (a sparkle for anyone unlisted). */
export const agentIcon = (id: string): LucideIcon => ICONS[id] ?? (id.startsWith("judge") ? Gavel : Sparkles);

/** One plain-English line per agent, for captions and labels on stage. */
export const AGENT_JOB: Record<string, string> = {
  showrunner: "Writes the brief, greenlights the winners",
  storyteller: "Finds the human story",
  contrarian: "Flips the listing tropes",
  comedian: "Deadpan, relatable punchlines",
  cinematographer: "Reveals, match cuts, rhythm",
  detective: "The detail nobody noticed",
  trend_scout: "Platform-native formats",
  cliche_hunter: "Kills anything templated",
  fact_checker: "Checks every claim against the photos",
  algorithm: "Thinks like the feed: stop power, watch time",
};

/** Agents share the walkthrough's ink: identity comes from the icon and the name, not from colour. */
export function stageColor(_hex?: string): string {
  return "#141414";
}

/** For the dark dashboard (not the walkthrough): the agent's own colour, lifted until it reads on near-black. */
export function dashColor(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "#cfcfcf";
  const c = [0, 2, 4].map((i) => parseInt(m[1]!.slice(i, i + 2), 16));
  const lum = (0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!) / 255;
  const t = lum >= 0.55 ? 0 : Math.min(0.5, (0.55 - lum) * 0.9);
  return `rgb(${c.map((v) => Math.round(v + (255 - v) * t)).join(" ")})`;
}

export interface Cast {
  showrunner: AgentCard | undefined;
  creators: AgentCard[];
  critics: AgentCard[];
  /** showrunner, creators and critics: the room */
  room: AgentCard[];
}

/** The writers' room in stage order. */
export function roomCast(lab: LabState): Cast {
  const showrunner = lab.agents.find((a) => a.role === "showrunner");
  const creators = lab.agents.filter((a) => a.role === "creator" && a.id !== CONTROL_AGENT);
  const critics = lab.agents.filter((a) => a.role === "critic");
  return { showrunner, creators, critics, room: [...(showrunner ? [showrunner] : []), ...creators, ...critics] };
}

/** A sharper copy of a listing photo for the big stage: the demo's HD export, or the live API at 1600 px. */
export function hdPhoto(url: string | undefined): string | undefined {
  if (!url) return url;
  return url.replace("/mock/creative/views/", "/mock/creative/views-hd/").replace(/\?w=\d+/, "?w=1600");
}
