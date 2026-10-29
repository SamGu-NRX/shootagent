import { describe, expect, it } from "vitest";
import type { FilmState, ManifestShot } from "../api/listing";
import type { TraceEvent } from "../api/types";
import { reduceAll } from "../trace/reduce";
import { buildStoryboard, roomChips } from "./storyboard";

const shot = (index: number, view: string, extra: Partial<ManifestShot> = {}): ManifestShot => ({
  index,
  labels: { en: view },
  source_photos: [{ view, version: `${view}.0`, blob: `${view}.jpg`, room: null }],
  planned_motion: "depth",
  motion_source: "depth",
  fell_back: false,
  ai_label: false,
  check: null,
  attempts: [],
  note: "",
  times: { en: [index * 4, index * 4 + 4] },
  ...extra,
});

const film = (shots: ManifestShot[]): FilmState => ({
  spec: { languages: ["en"] },
  manifest: { shoot: "s", created: "", media_mode: "replay", disclosure: { law: "", label: {}, rule: "" }, shots, languages: {} },
  variants: {},
});

const ev = (seq: number, kind: string, view: string | null, data: object) =>
  ({ run: "r", seq, t: new Date(1e12 + seq).toISOString(), kind, view, data }) as TraceEvent;

describe("buildStoryboard", () => {
  // Run 20261028-185544-ddefff: the agent re-planned shot v01 from generated to depth after its clip failed,
  // so the final manifest lists no attempts. The rejection must still show, from the trace.
  it("keeps a rejected clip from the trace when the re-planned manifest dropped it", () => {
    const trace = reduceAll([
      ev(1, "run_start", null, { mode: "film", views: ["v01", "v03"] }),
      ev(2, "check", "v01", { shot: 0, clip: "e20e", passed: false, failures: [], images: ["sheet.jpg"], judge: { pass: false, changes: [{ object: "white door", change: "duplicated" }] } }),
    ]);
    const [v01, v03] = buildStoryboard(film([shot(0, "v01"), shot(1, "v03")]), trace);
    expect(v01!.rejected).toHaveLength(1);
    expect(v01!.rejected[0]!.sheet).toBe("sheet.jpg");
    expect(v01!.rejected[0]!.reasons[0]!.plain).toBe("White door duplicated");
    expect(v03!.rejected).toEqual([]);
  });

  it("never shows a failed 40 px depth-move check as a rejected AI clip", () => {
    const trace = reduceAll([
      ev(1, "run_start", null, { mode: "film", views: ["v01"] }),
      ev(2, "check", "v01", { shot: 0, clip: "d40", source: "depth", passed: false, failures: ["a straight edge bends by 1.65 px"] }),
    ]);
    const [v01] = buildStoryboard(film([shot(0, "v01")]), trace);
    expect(v01!.rejected).toEqual([]);
    expect(v01!.fellBack).toBe(false);
  });

  it("prefers the manifest's own attempts over the trace", () => {
    const attempts = [{ passed: false, reasons: ["judge (high confidence): pendant lights appeared"] }];
    const trace = reduceAll([
      ev(1, "run_start", null, { mode: "film", views: ["v07"] }),
      ev(2, "check", "v07", { shot: 0, clip: "x", passed: false, failures: ["something else"] }),
    ]);
    const [v07] = buildStoryboard(film([shot(0, "v07", { planned_motion: "generated", fell_back: true, attempts })]), trace);
    expect(v07!.rejected.map((r) => r.reasons[0]!.plain)).toEqual(["Pendant lights appeared"]);
  });
});

describe("roomChips", () => {
  it("gives a room the film shows twice one chip that covers both shots", () => {
    const s = (index: number, en: string) => ({ index, view: `v${index}`, end_view: null, labels: { en }, source: "depth" as const, ai_label: false, times: {} });
    expect(roomChips([s(0, "Dining room"), s(1, "Desk nook"), s(2, "Dining room")], "en", "en")).toEqual([
      { name: "Dining room", shots: [0, 2] },
      { name: "Desk nook", shots: [1] },
    ]);
  });
});
