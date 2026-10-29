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

// The judge files each object under a snake_case category ("wall_plate"); people read "Wall plate".
const CATEGORY: Record<string, string> = { wall_plate: "Wall plate", built_in: "Built-in", tv: "TV", other: "" };
const category = (c: string) => (c in CATEGORY ? CATEGORY[c]! : sentence(c.replace(/_/g, " ")));

// A judge v2 description is already a sentence about the change ("A white outlet plate appears ..."); the
// change word is added only to a bare object name, as the v1 judge gave ("white door" + "duplicated").
const SAYS_CHANGE = /\b(appear\w*|missing|disappear\w*|vanish\w*|chang\w*|duplicat\w*|mov(e|ed|es|ing)|shift\w*|added|removed|not in|no longer)\b/i;

function finding(cat: string | null, text: string, change?: string, frame?: string | number | null): Reason {
  let body = text.replace(/\s+/g, " ").trim();
  if (change && !SAYS_CHANGE.test(body)) body = `${body.replace(/\s*\([^)]*\)/g, "")} ${change.replace(/_/g, " ")}${at(frame)}`;
  const label = cat ? category(cat) : "";
  const plain = label ? `${label}: ${sentence(body)}` : sentence(body);
  return { plain, detail: plain, kind: "judge" };
}

/** One reason per object the judge failed: "Wall plate: A white outlet plate appears ...", "White door duplicated at 3.5 s". */
export function judgeReasons(judge: ClipJudge | null | undefined): Reason[] {
  if (judge?.failing?.length) return judge.failing.map((f) => finding(f.category, f.description));
  return (judge?.changes ?? []).map((c) => {
    // v2 changes repeat the category inside the object: "wall_plate: A white outlet plate appears ..."
    const m = c.object.match(/^([a-z_]+):\s+(.+)$/s);
    return m ? finding(m[1]!, m[2]!, c.change, c.first_frame) : finding(null, c.object, c.change, c.first_frame);
  });
}

/** The judge's line in the checker's failures, for attempts recorded without the judge's own fields. */
function judgeLine(f: string): Reason[] {
  const text = f.replace(/^judge(\s*\([^)]*\))?:\s*/, "");
  // v2: "built_in new at FRAME 6 (medium): Dark vanity cabinet ...; furniture new at FRAME 7 (medium): ..."
  const v2 = [...text.matchAll(/([a-z_]+) (?:new|missing|changed|moved|shifted) at frame \d+ \([a-z]+\):\s*([^;]+)/gi)];
  if (v2.length) return v2.map((m) => ({ ...finding(m[1]!, m[2]!), detail: f }));
  // v1: "cluster of grey pendant lights appeared at t=1.2s (top center)"
  return [{ plain: sentence(text.split(" at t=")[0]!.trim()), detail: f, kind: "judge" }];
}

const PLAIN: [RegExp, string][] = [
  [/^room-to-room walk/, "A walk from one room into another can't be checked against a single photo"],
];

/** Reasons from the checker's failure strings, skipping "judge …" lines when the judge's changes are known. */
export function failureReasons(failures: string[], hasJudge: boolean): Reason[] {
  const out: Reason[] = [];
  for (const f of failures) {
    if (/^judge\b/.test(f)) {
      if (!hasJudge) out.push(...judgeLine(f));
      continue;
    }
    const plain = PLAIN.find(([re]) => re.test(f));
    if (plain) {
      out.push({ plain: plain[1], detail: f, kind: "other" });
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
