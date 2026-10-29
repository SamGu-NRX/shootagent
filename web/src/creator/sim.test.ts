import { describe, expect, it } from "vitest";
import { clockHour, formatRatio, interpolateSeries, launchOutcome, marginText, segmentsAt, visibleComments, zeroPoint } from "./sim";
import type { Reel, SimPoint, Simulation } from "./types";

const pt = (hour: number, views: number, inquiries = 0): SimPoint => ({ ...zeroPoint(hour), views, likes: views / 10, inquiries });

function sim(reel: string, series: SimPoint[], extra: Partial<Simulation> = {}): Simulation {
  return {
    reel,
    followers: 8000,
    hours: 48,
    seed: 7,
    series,
    retention: [],
    totals: series[series.length - 1] ?? zeroPoint(48),
    comments: [],
    by_segment: {},
    notes: [],
    ...extra,
  };
}

const reels = (ids: [string, Reel["kind"]][]) => ids.map(([id, kind]) => ({ id, kind }));

describe("interpolateSeries", () => {
  const series = [pt(0, 0), pt(1, 100), pt(3, 400), pt(48, 1000)];

  it("returns the recorded point at an exact hour", () => {
    expect(interpolateSeries(series, 1).views).toBe(100);
    expect(interpolateSeries(series, 3).views).toBe(400);
  });

  it("interpolates linearly between points and rounds to whole counts", () => {
    expect(interpolateSeries(series, 0.5).views).toBe(50);
    expect(interpolateSeries(series, 2).views).toBe(250);
    expect(interpolateSeries(series, 2).likes).toBe(25);
    expect(Number.isInteger(interpolateSeries(series, 1.37).views)).toBe(true);
  });

  it("holds the last point after the end and starts from the first point", () => {
    expect(interpolateSeries(series, 60).views).toBe(1000);
    expect(interpolateSeries(series, 60).hour).toBe(60);
    expect(interpolateSeries(series, -2).views).toBe(0);
  });

  it("rises from zero when the series starts after hour 0", () => {
    const late = [pt(2, 200), pt(4, 400)];
    expect(interpolateSeries(late, 1).views).toBe(100);
    expect(interpolateSeries(late, 0).views).toBe(0);
  });

  it("copes with unsorted and empty series", () => {
    expect(interpolateSeries([pt(3, 300), pt(1, 100)], 2).views).toBe(200);
    expect(interpolateSeries([], 5)).toEqual(zeroPoint(5));
  });

  it("never decreases for a cumulative series", () => {
    let last = -1;
    for (let h = 0; h <= 48; h += 0.25) {
      const v = interpolateSeries(series, h).views;
      expect(v).toBeGreaterThanOrEqual(last);
      last = v;
    }
  });
});

describe("clockHour", () => {
  it("maps playback time onto the launch window", () => {
    expect(clockHour(0, 20_000, 48)).toBe(0);
    expect(clockHour(10_000, 20_000, 48)).toBe(24);
    expect(clockHour(20_000, 20_000, 48)).toBe(48);
  });
  it("clamps to the window", () => {
    expect(clockHour(25_000, 20_000, 48)).toBe(48);
    expect(clockHour(-5, 20_000, 48)).toBe(0);
    expect(clockHour(100, 0, 48)).toBe(48);
  });
});

describe("launchOutcome and marginText", () => {
  const sims = [sim("A", [pt(0, 0), pt(48, 1000, 2)]), sim("B", [pt(0, 0), pt(48, 6200, 9)]), sim("C", [pt(0, 0), pt(48, 3000, 4)])];
  const rs = reels([
    ["A", "baseline"],
    ["B", "creative"],
    ["C", "creative"],
  ]);

  it("measures the winner against the template", () => {
    const o = launchOutcome(sims, rs)!;
    expect(o.winner).toBe("B");
    expect(o.against).toBe("A");
    expect(o.ratio).toBeCloseTo(6.2);
    expect(marginText(o)).toBe("B reached 6.2× the views of A");
    expect(o.ranking.map((r) => r.reel)).toEqual(["B", "C", "A"]);
  });

  it("works on any metric", () => {
    const o = launchOutcome(sims, rs, "inquiries")!;
    expect(o.winner).toBe("B");
    expect(o.ratio).toBeCloseTo(4.5);
    expect(marginText(o)).toBe("B reached 4.5× the inquiries of A");
  });

  it("compares with the runner-up when the template wins", () => {
    const flipped = [sim("A", [pt(48, 9000)]), sim("B", [pt(48, 3000)])];
    const o = launchOutcome(flipped, reels([["A", "baseline"], ["B", "creative"]]))!;
    expect(o.winner).toBe("A");
    expect(o.against).toBe("B");
    expect(marginText(o)).toBe("A reached 3.0× the views of B");
  });

  it("reports a tie and a zero baseline honestly", () => {
    const tie = launchOutcome([sim("B", [pt(48, 500)]), sim("C", [pt(48, 500)])], reels([["B", "creative"], ["C", "creative"]]))!;
    expect(tie.tie).toBe(true);
    expect(marginText(tie)).toBe("B and C tied on views");
    const zero = launchOutcome([sim("A", [pt(48, 0)]), sim("B", [pt(48, 1200)])], reels([["A", "baseline"], ["B", "creative"]]))!;
    expect(zero.ratio).toBeNull();
    expect(marginText(zero)).toBe("B reached 1,200 views; A reached none");
  });

  it("formats big ratios without decimals", () => {
    expect(formatRatio(6.2012)).toBe("6.2×");
    expect(formatRatio(14.04)).toBe("14×");
    expect(launchOutcome([], rs)).toBeNull();
  });
});

describe("visibleComments and segmentsAt", () => {
  const withComments = [
    sim("A", [pt(0, 0), pt(48, 100)], { comments: [{ hour: 5, viewer: "v1", name: "One", text: "a", likes: 1 }] }),
    sim("B", [pt(0, 0), pt(24, 500), pt(48, 1000)], {
      comments: [
        { hour: 1, viewer: "v2", name: "Two", text: "b", likes: 2 },
        { hour: 30, viewer: "v3", name: "Three", text: "c", likes: 3 },
      ],
      by_segment: { v1: 600, v2: 400 },
    }),
  ];

  it("shows comments once the clock passes their hour, newest first", () => {
    expect(visibleComments(withComments, 0.5)).toEqual([]);
    expect(visibleComments(withComments, 6).map((c) => c.text)).toEqual(["a", "b"]);
    expect(visibleComments(withComments, 48).map((c) => [c.text, c.reel])).toEqual([
      ["c", "B"],
      ["a", "A"],
      ["b", "B"],
    ]);
  });

  it("scales the segment split by how far views have got", () => {
    expect(segmentsAt(withComments[1]!, 24)).toEqual({ v1: 300, v2: 200 });
    expect(segmentsAt(withComments[1]!, 48)).toEqual({ v1: 600, v2: 400 });
  });
});
