// The presenter: a one-minute, full-screen walkthrough of a finished lab, built to be screen-recorded.
// Each scene draws on a fixed 1920x1080 stage (the presenter scales it to the window) and is driven by `t`,
// the milliseconds since the scene started, so a take can be paused, scrubbed and replayed identically.

import type { ComponentType } from "react";
import type { ShootInfo } from "../api";
import type { LabState } from "../types";
import type { Story } from "./metrics";

export interface SceneProps {
  lab: LabState;
  shoot: ShootInfo | null;
  /** the lab's headline numbers, shared by every scene so they always agree */
  story: Story;
  /** URL of a file in the lab folder: reel videos (`reel.video`) and posters (`reel.poster`) */
  fileUrl: (path: string) => string;
  /** ms since this scene started; it stops advancing while paused */
  t: number;
  /** this scene's length in ms */
  duration: number;
  playing: boolean;
}

export interface SceneDef {
  id: string;
  /** a word or two for the progress bar */
  label: string;
  /** ms */
  duration: number;
  /** the narration for this scene; the presenter shows it as a caption when captions are on */
  caption: (story: Story) => string;
  Component: ComponentType<SceneProps>;
}

/** Eased progress of `t` through the window [from, from + span] ms, clamped to 0-1. */
export function phase(t: number, from: number, span: number, ease: (x: number) => number = easeOut): number {
  if (span <= 0) return t >= from ? 1 : 0;
  return ease(Math.min(1, Math.max(0, (t - from) / span)));
}

export const easeOut = (x: number) => 1 - Math.pow(1 - x, 3);
export const easeInOut = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
export const linear = (x: number) => x;
/** overshoots a little, then settles: for things that pop in */
export const easeBack = (x: number) => {
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
};

/** A number counting up from 0 over [from, from + span] ms. */
export const countUp = (t: number, from: number, span: number, to: number) => to * phase(t, from, span);
