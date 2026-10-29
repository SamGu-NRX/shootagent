// Plain-language reasons for a rejected generated clip. The clip checker (film/check.py) reports precise
// technical failures; the storyboard leads with what a person would notice and keeps the numbers one click away.

import type { ClipJudge } from "../api/listing";

export interface Reason {
  /** what a buyer would notice */
  plain: string;
  /** the checker's own words */
  detail: string;
  kind: "judge" | "geometry" | "unavailable" | "other";
}

const GEOMETRY: [RegExp, string][] = [
  [/^motion no rigid camera explains/, "Part of the room moved in a way a real camera can't"],
  [/^structure not in the first frame/, "Structure appeared that isn't in the photo"],
  [/^a straight edge bends/, "A straight edge bent"],
  [/^new straight edges/, "New edges appeared"],
  [/^straight edges vanished/, "Edges from the photo disappeared"],
  [/^endpoint/, "The clip doesn't land on the photo it should end on"],
];

const at = (frame: string | number | null | undefined) => {
  if (frame == null || frame === "") return "";
  const s = typeof frame === "number" ? frame : parseFloat(String(frame).replace(/^t=/, ""));
  return Number.isFinite(s) ? ` at ${s.toFixed(1)} s` : "";
};

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** One reason per judge change ("White door duplicated at 3.5 s"). */
export function judgeReasons(judge: ClipJudge | null | undefined): Reason[] {
  return (judge?.changes ?? []).map((c) => {
    const object = c.object.replace(/\s*\([^)]*\)/g, "").trim();
    const change = c.change.replace(/_/g, " ");
    const text = `${sentence(object)} ${change}${at(c.first_frame)}`;
    return { plain: text, detail: [text, c.where].filter(Boolean).join(" · "), kind: "judge" as const };
  });
}

/** Reasons from the checker's failure strings, skipping "judge …" lines when the judge's changes are known. */
export function failureReasons(failures: string[], hasJudge: boolean): Reason[] {
  const out: Reason[] = [];
  for (const f of failures) {
    if (/^judge\b/.test(f)) {
      if (hasJudge) continue;
      const text = f.replace(/^judge(\s*\([^)]*\))?:\s*/, "");
      out.push({ plain: sentence(text.split(" at t=")[0]!.trim()), detail: f, kind: "judge" });
      continue;
    }
    if (/no recorded clip|not_cached|unavailable/i.test(f)) {
      out.push({ plain: "No second attempt: this machine replays recorded clips only", detail: f, kind: "unavailable" });
      continue;
    }
    const hit = GEOMETRY.find(([re]) => re.test(f));
    out.push(hit ? { plain: hit[1], detail: f, kind: "geometry" } : { plain: sentence(f), detail: f, kind: "other" });
  }
  // several geometry failures read as one finding
  const geometry = out.filter((r) => r.kind === "geometry");
  if (geometry.length > 1) {
    const first = geometry[0]!;
    const merged: Reason = {
      plain: `${first.plain}, and ${geometry.length - 1} more motion check${geometry.length > 2 ? "s" : ""} failed`,
      detail: geometry.map((g) => g.detail).join("\n"),
      kind: "geometry",
    };
    return [...out.filter((r) => r.kind !== "geometry"), merged];
  }
  return out;
}

/** Everything wrong with one rejected attempt, judge findings first. */
export function attemptReasons(failures: string[], judge: ClipJudge | null | undefined): Reason[] {
  const fromJudge = judgeReasons(judge);
  return [...fromJudge, ...failureReasons(failures, fromJudge.length > 0)];
}

export const MOTION_LABEL: Record<string, string> = {
  generated: "Generated clip",
  depth: "Depth move",
  still: "Slow push-in",
};

export const MOTION_NOTE: Record<string, string> = {
  generated: "AI image-to-video, checked against the photo before it may play",
  depth: "A camera move made from the photo's own pixels; nothing invented",
  still: "A slow push-in on the photo; nothing invented",
};
