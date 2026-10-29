import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { simulateLab, simulateReel } from "./simModel";
import { agreementLabel, dimensionMeans, headToHead, kendallFromRatings, kendallW, mean, ranks, sd, winRate } from "./stats";
import type { JudgeRating, LabState, PairwiseVote } from "./types";

const recorded = JSON.parse(readFileSync(new URL("../../public/mock/creative/lab.json", import.meta.url), "utf8")) as LabState;

describe("basic stats", () => {
  it("mean and sample sd", () => {
    expect(mean([2, 4, 6])).toBe(4);
    expect(mean([])).toBe(0);
    expect(sd([2, 4, 6])).toBeCloseTo(2);
    expect(sd([5])).toBe(0);
  });

  it("ranks with ties sharing the average rank", () => {
    expect(ranks([9, 7, 8])).toEqual([1, 3, 2]);
    expect(ranks([8, 8, 5, 9])).toEqual([2.5, 2.5, 4, 1]);
  });
});

describe("Kendall's W", () => {
  it("is 1 when every judge gives the same order and 0 when two judges are opposite", () => {
    expect(kendallW([[3, 2, 1], [9, 5, 1], [7, 6, 2]])).toBeCloseTo(1);
    expect(kendallW([[3, 2, 1], [1, 2, 3]])).toBeCloseTo(0);
  });

  it("matches a hand-worked value", () => {
    // ranks: judge 1 C>B>S>A, judge 2 B>C>S>A, judge 3 B>S>C>A, judges 4-5 C>B>S>A -> W = 1188 / 1500
    const scores = [
      [3, 8, 9, 4],
      [2, 8, 7, 5],
      [4, 8, 5, 6],
      [3, 8, 10, 4],
      [2, 7, 9, 5],
    ];
    expect(kendallW(scores)).toBeCloseTo(0.792, 3);
  });

  it("is computed from ratings, ignoring judges who skipped a reel", () => {
    const r = (judge: string, reel: string, creativity: number) => ({ judge, reel, creativity }) as JudgeRating;
    const ratings = [r("j1", "A", 2), r("j1", "B", 8), r("j2", "A", 3), r("j2", "B", 9), r("j3", "A", 5)];
    expect(kendallFromRatings(ratings, ["A", "B"])).toBeCloseTo(1);
    expect(kendallFromRatings(recorded.evaluation!.ratings, ["A", "B", "C", "S"])).toBeCloseTo(recorded.evaluation!.agreement.kendall_w!, 3);
  });

  it("has a plain-English label for every range", () => {
    expect(agreementLabel(0.79).short).toBe("Very strong agreement");
    expect(agreementLabel(0.6).short).toBe("Strong agreement");
    expect(agreementLabel(0.4).short).toBe("Moderate agreement");
    expect(agreementLabel(0.2).short).toBe("Weak agreement");
    expect(agreementLabel(0.02).short).toBe("No agreement");
  });
});

describe("head to head", () => {
  const v = (judge: string, first: string, second: string, winner: string): PairwiseVote => ({ judge, first, second, winner, reason: "" });
  const votes = [v("j1", "A", "B", "B"), v("j2", "B", "A", "B"), v("j3", "A", "B", "A"), v("j1", "B", "C", "C")];

  it("counts wins per ordered pair", () => {
    const t = headToHead(votes, ["A", "B", "C"]);
    expect(t.B!.A).toEqual({ wins: 2, total: 3 });
    expect(t.A!.B).toEqual({ wins: 1, total: 3 });
    expect(t.C!.B).toEqual({ wins: 1, total: 1 });
    expect(t.A!.C).toEqual({ wins: 0, total: 0 });
  });

  it("win rate is the share of a reel's comparisons it won", () => {
    expect(winRate(votes, "B")).toBeCloseTo(2 / 4);
    expect(winRate(votes, "C")).toBe(1);
    expect(winRate(votes, "Z")).toBe(0);
  });

  it("the recorded run's summaries agree with its raw votes and ratings", () => {
    const ev = recorded.evaluation!;
    for (const s of ev.summaries) {
      expect(winRate(ev.pairwise, s.reel)).toBeCloseTo(s.pairwise_win_rate, 3);
      expect(mean(ev.ratings.filter((r) => r.reel === s.reel).map((r) => r.creativity))).toBeCloseTo(s.creativity, 3);
    }
    expect(dimensionMeans(ev.ratings, "C").originality).toBeGreaterThan(dimensionMeans(ev.ratings, "A").originality);
  });
});

describe("launch model (port of simulate.py)", () => {
  const ev = recorded.evaluation!;
  const reel = recorded.reels.find((r) => r.id === "B")!;
  const summary = ev.summaries.find((s) => s.reel === "B");

  it("is seeded: the same inputs give the same launch", () => {
    const a = simulateReel(reel, summary, ev.reactions, ev.viewers, 8000, 48, 7);
    const b = simulateReel(reel, summary, ev.reactions, ev.viewers, 8000, 48, 7);
    expect(a).toEqual(b);
    const c = simulateReel(reel, summary, ev.reactions, ev.viewers, 8000, 48, 8);
    expect(c.totals.views).not.toBe(a.totals.views);
  });

  it("produces hourly cumulative series, retention from 1 and every assumption", () => {
    const s = simulateReel(reel, summary, ev.reactions, ev.viewers, 8000, 48, 7);
    expect(s.series).toHaveLength(49);
    expect(s.series[0]!.hour).toBe(0);
    for (let i = 1; i < s.series.length; i++) for (const k of ["views", "likes", "shares", "inquiries"] as const) expect(s.series[i]![k]).toBeGreaterThanOrEqual(s.series[i - 1]![k]);
    expect(s.totals).toEqual(s.series[48]);
    expect(s.retention[0]!.fraction).toBeCloseTo(1, 1);
    expect(s.retention[s.retention.length - 1]!.second).toBeCloseTo(reel.seconds, 1);
    expect(Object.values(s.by_segment).reduce((a, b) => a + b, 0)).toBe(s.totals.views);
    expect(s.notes.some((n) => n.startsWith("Wave 0 reaches 25% of 8,000 followers"))).toBe(true);
    for (const c of s.comments) expect(ev.reactions.some((r) => r.reel === "B" && r.comment_text === c.text)).toBe(true);
  });

  it("reaches further with more followers", () => {
    const small = simulateReel(reel, summary, ev.reactions, ev.viewers, 2000, 48, 7).totals.views;
    const big = simulateReel(reel, summary, ev.reactions, ev.viewers, 50000, 48, 7).totals.views;
    expect(big).toBeGreaterThan(small * 5);
  });

  it("agrees with the recorded Python launch: same order for clearly separated reels, totals within 2x", () => {
    // The port draws its own random numbers, so totals differ from numpy's; rankings and scale must not.
    const sims = simulateLab(recorded, { followers: 8000, hours: 48, seed: 7 });
    expect(sims.map((s) => s.reel)).toEqual(recorded.reels.map((r) => r.id));
    const ts = Object.fromEntries(sims.map((s) => [s.reel, s.totals.views]));
    const py = Object.fromEntries(recorded.simulations.map((s) => [s.reel, s.totals.views]));
    for (const a of Object.keys(py)) {
      expect(ts[a]! / py[a]!).toBeGreaterThan(0.5);
      expect(ts[a]! / py[a]!).toBeLessThan(2);
      for (const b of Object.keys(py)) if (py[a]! > py[b]! * 1.25) expect(ts[a]!).toBeGreaterThan(ts[b]!);
    }
  });
});
