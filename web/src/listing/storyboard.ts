// One storyboard row per film shot: the photo, what plays, and every generated clip that was rejected on
// the way. The manifest lists rejected attempts for shots it kept as generated plans; when the agent re-planned
// a shot (generated -> depth) the manifest no longer carries them, so they come from the film run's trace.

import type { FilmState, MotionSource, PublicShot } from "../api/listing";
import type { CheckItem, TraceState } from "../trace/reduce";
import { attemptReasons, type Reason } from "./reasons";

export interface Rejection {
  reasons: Reason[];
  /** contact sheet blob the checker looked at */
  sheet: string | null;
}

export interface StoryShot {
  index: number;
  view: string;
  endView: string | null;
  photo: string;
  labels: Record<string, string>;
  planned: MotionSource;
  source: MotionSource | null;
  fellBack: boolean;
  aiLabel: boolean;
  times: Record<string, [number, number]>;
  rejected: Rejection[];
  /** a generated clip that passed its check */
  passed: boolean;
}

export function buildStoryboard(film: FilmState, trace: TraceState | null): StoryShot[] {
  const shots = film.manifest?.shots ?? [];
  const traceChecks = (trace?.items ?? []).filter((i): i is CheckItem => i.type === "check" && !!i.clip);
  return shots.map((s) => {
    const view = s.source_photos[0]?.view ?? "";
    let rejected: Rejection[] = s.attempts
      .filter((a) => !a.passed)
      .map((a) => ({ reasons: attemptReasons(a.reasons, a.judge), sheet: a.sheet ?? null }));
    if (!rejected.length) {
      rejected = traceChecks
        .filter((c) => !c.passed && c.view === view)
        .map((c) => ({ reasons: attemptReasons(c.failures, c.judge), sheet: c.images?.[0] ?? null }));
    }
    return {
      index: s.index,
      view,
      endView: s.source_photos[1]?.view ?? null,
      photo: s.source_photos[0]?.blob ?? "",
      labels: s.labels,
      planned: s.planned_motion,
      source: s.motion_source,
      fellBack: s.fell_back || (rejected.length > 0 && s.motion_source !== "generated"),
      aiLabel: s.ai_label,
      times: s.times,
      rejected,
      passed: s.motion_source === "generated" && s.attempts.some((a) => a.passed),
    };
  });
}

/** Index of the shot playing at `t` seconds in `lang`, or -1. */
export function shotAt(shots: { times: Record<string, [number, number]> }[], lang: string, t: number): number {
  return shots.findIndex((s) => {
    const span = s.times[lang];
    return !!span && t >= span[0] && t < span[1];
  });
}

/** Latest film run's trace, the one whose shots the manifest describes. */
export function latestFilmTrace(traces: Record<string, TraceState>, runIds: string[]): TraceState | null {
  for (let i = runIds.length - 1; i >= 0; i--) {
    const t = traces[runIds[i]!];
    if (t?.mode === "film") return t;
  }
  return null;
}

/**
 * One chip per room, in film order. A room the film shows twice (QC can split one photo into two camera
 * positions, and both may play) gets one chip that lights up for either shot and jumps to the first.
 */
export function roomChips(shots: PublicShot[], lang: string, fallback: string) {
  const out: { name: string; shots: number[] }[] = [];
  shots.forEach((s, i) => {
    const name = s.labels[lang] ?? s.labels[fallback] ?? "";
    const hit = out.find((c) => c.name === name);
    if (hit) hit.shots.push(i);
    else out.push({ name, shots: [i] });
  });
  return out;
}
