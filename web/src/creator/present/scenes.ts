// The one-minute walkthrough, in order. Durations add up to about 62 s.

import { SceneCritique } from "./SceneCritique";
import { SceneHook } from "./SceneHook";
import { ScenePayoff } from "./ScenePayoff";
import { ScenePitch } from "./ScenePitch";
import { SceneProof } from "./SceneProof";
import { SceneReels } from "./SceneReels";
import type { SceneDef } from "./types";

const x = (v: number | null) => (v == null ? "more" : `${v >= 9.95 ? Math.round(v) : v.toFixed(1)}×`);

export const SCENES: SceneDef[] = [
  {
    id: "hook",
    label: "The problem",
    duration: 7000,
    caption: () => "Every listing reel sounds the same. Just listed. Stunning. Schedule a tour. Buyers scroll straight past.",
    Component: SceneHook,
  },
  {
    id: "pitch",
    label: "The creative team",
    duration: 10000,
    caption: (s) => `So we gave every listing a creative team: ${s.roomAgents} AI agents. ${s.creators} creators pitch at once, each with a different instinct.`,
    Component: ScenePitch,
  },
  {
    id: "critique",
    label: "Critique & remix",
    duration: 12000,
    caption: (s) => `${s.critics} critics tear the pitches apart: clichés die, false claims get caught, and the best ideas get stolen and remixed.`,
    Component: SceneCritique,
  },
  {
    id: "reels",
    label: "Before / after",
    duration: 11000,
    caption: () => "The showrunner greenlights the winners, and the studio turns them into finished reels.",
    Component: SceneReels,
  },
  {
    id: "proof",
    label: "Measured",
    duration: 10000,
    caption: (s) => `${s.viewers + s.judges} independent AI viewers and judges score every reel blind, and novelty is measured against ${s.cliches} real-estate clichés.`,
    Component: SceneProof,
  },
  {
    id: "payoff",
    label: "The payoff",
    duration: 12000,
    caption: (s) => `Predicted first ${s.hours} hours: ${x(s.viewsX)} the views and the only buyer inquiries, for $${s.usd.toFixed(2)} of AI time.`,
    Component: ScenePayoff,
  },
];
