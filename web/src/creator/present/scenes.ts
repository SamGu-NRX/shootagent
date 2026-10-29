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
    caption: () => "Every listing reel says the same things: just listed, stunning, schedule a tour. So buyers swipe past in seconds.",
    Component: SceneHook,
  },
  {
    id: "pitch",
    label: "The creative team",
    duration: 10000,
    caption: (s) => `So we built a creative team of ${s.roomAgents} AI agents. ${s.creators} of them write reel ideas at the same time, each in a different style.`,
    Component: ScenePitch,
  },
  {
    id: "critique",
    label: "Critique & remix",
    duration: 12000,
    caption: (s) => `${s.critics} critics review every idea. They cut what's overused, catch claims the photos don't support, and the best ideas get combined.`,
    Component: SceneCritique,
  },
  {
    id: "reels",
    label: "Before / after",
    duration: 11000,
    caption: () => "Two ideas make the cut and become finished reels. Same listing, same photos, a very different reel.",
    Component: SceneReels,
  },
  {
    id: "proof",
    label: "Measured",
    duration: 10000,
    caption: (s) => `${s.viewers + s.judges} AI viewers and judges score every reel without knowing who made it, and we measure how far each one is from ${s.cliches} overused listing lines.`,
    Component: SceneProof,
  },
  {
    id: "payoff",
    label: "The payoff",
    duration: 12000,
    caption: (s) => `In a simulated first ${s.hours} hours, the AI team's reel gets ${x(s.viewsX)} more views and ${s.hero?.inquiries ?? 0} buyer messages, for $${s.usd.toFixed(2)} of AI time.`,
    Component: ScenePayoff,
  },
];
