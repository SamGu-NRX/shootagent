import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { buildTimeMap, keyframes, labAt, recordingEnd, reconcile } from "./replay";
import type { LabState } from "./types";

const fixture = JSON.parse(readFileSync(new URL("../../public/mock/creative/lab.json", import.meta.url), "utf8")) as LabState;

/** A small recorded lab: a 10 s brief, a burst of pitches, then a long, silent render. */
function tiny(): LabState {
  const base = structuredClone(fixture);
  return {
    ...base,
    status: "done",
    elapsed: 400,
    phases: [
      { key: "brief", label: "Brief", status: "done", started: 0, ended: 10, detail: "brief done" },
      { key: "pitch", label: "Pitches", status: "done", started: 10, ended: 20, detail: "2 pitches" },
      { key: "greenlight", label: "Greenlight", status: "done", started: 20, ended: 30, detail: "B" },
      { key: "single_agent", label: "Single-agent control", status: "skipped", started: null, ended: null, detail: "" },
      { key: "render", label: "Render", status: "done", started: 30, ended: 390, detail: "1 of 1" },
      { key: "simulate", label: "Launch sim", status: "done", started: 390, ended: 400, detail: "" },
    ],
    feed: [
      { id: "m1", t: 9, agent: "showrunner", kind: "brief", text: "brief", concept: null, refs: [] },
      { id: "m2", t: 12, agent: "comedian", kind: "pitch", text: "p1", concept: "c03", refs: [] },
      { id: "m3", t: 12.5, agent: "detective", kind: "pitch", text: "p2", concept: "c01", refs: [] },
      { id: "m4", t: 25, agent: "showrunner", kind: "greenlight", text: "go", concept: "c03", refs: [] },
    ],
    concepts: base.concepts.filter((c) => c.id === "c01" || c.id === "c03").map((c) => ({ ...c, status: c.id === "c03" ? "finalist" : "killed" })),
    critiques: [],
    finalists: ["c03"],
    single_agent: [],
    reels: [],
    evaluation: null,
    simulations: base.simulations,
  };
}

describe("buildTimeMap", () => {
  it("fits the recording into the target and maps both ways", () => {
    const map = buildTimeMap(fixture, 75);
    expect(map.duration).toBeCloseTo(75, 6);
    expect(map.toReplay(0)).toBe(0);
    expect(map.toReplay(recordingEnd(fixture))).toBeCloseTo(75, 6);
    for (const t of [0, 3.3, 47.2, 128.6, 333, 500, recordingEnd(fixture)]) expect(map.toRecorded(map.toReplay(t))).toBeCloseTo(t, 6);
  });

  it("is monotonic", () => {
    const map = buildTimeMap(fixture, 75);
    let last = -1;
    for (let r = 0; r <= 75; r += 0.1) {
      const t = map.toRecorded(r);
      expect(t).toBeGreaterThanOrEqual(last);
      last = t;
    }
  });

  it("shrinks dead air more than bursts", () => {
    const lab = tiny();
    const map = buildTimeMap(lab, 60);
    const renderShare = (map.toReplay(390) - map.toReplay(30)) / 60;
    const recordedShare = 360 / 400;
    expect(renderShare).toBeLessThan(recordedShare / 2);
    // the 0.5 s between two pitches still gets visible time
    expect(map.toReplay(12.5) - map.toReplay(12)).toBeGreaterThan(0.5);
  });

  it("puts every message and phase edge on a keyframe", () => {
    const ks = keyframes(fixture);
    for (const m of fixture.feed) expect(ks).toContain(m.t);
    expect([...ks].sort((a, b) => a - b)).toEqual(ks);
  });
});

describe("labAt", () => {
  it("returns the recording itself once it has played", () => {
    expect(labAt(fixture, recordingEnd(fixture))).toBe(fixture);
    expect(labAt(fixture, 1e9)).toBe(fixture);
  });

  it("starts with nothing revealed", () => {
    const st = labAt(tiny(), 0);
    expect(st.status).toBe("running");
    expect(st.feed).toEqual([]);
    expect(st.concepts).toEqual([]);
    expect(st.brief).toBeNull();
    expect(st.phases.find((p) => p.key === "brief")!.status).toBe("running");
    expect(st.phases.find((p) => p.key === "pitch")!.status).toBe("pending");
    expect(st.phases.find((p) => p.key === "pitch")!.started).toBeNull();
    expect(st.usage.usd).toBe(0);
  });

  it("reveals messages, concepts and phases as recorded time passes", () => {
    const st = labAt(tiny(), 12.2);
    expect(st.feed.map((m) => m.id)).toEqual(["m1", "m2"]);
    expect(st.concepts.map((c) => c.id)).toEqual(["c03"]);
    expect(st.concepts[0]!.status).toBe("pitched");
    expect(st.phase).toBe("pitch");
    expect(st.phases.find((p) => p.key === "brief")).toMatchObject({ status: "done", ended: 10 });
    expect(st.phases.find((p) => p.key === "pitch")).toMatchObject({ status: "running", ended: null, detail: "" });
    expect(st.elapsed).toBe(12.2);
    expect(st.usage.usd).toBeCloseTo((tiny().usage.usd * 12.2) / 400, 6);
  });

  it("settles statuses when the greenlight lands; a skipped phase shows as skipped from the start", () => {
    expect(labAt(tiny(), 0).phases.find((p) => p.key === "single_agent")!.status).toBe("skipped");
    const before = labAt(tiny(), 29);
    expect(before.finalists).toEqual([]);
    expect(before.concepts.map((c) => c.status)).toEqual(["pitched", "pitched"]);
    expect(before.phases.find((p) => p.key === "single_agent")!.status).toBe("skipped");
    const after = labAt(tiny(), 31);
    expect(after.finalists).toEqual(["c03"]);
    expect(Object.fromEntries(after.concepts.map((c) => [c.id, c.status]))).toEqual({ c03: "finalist", c01: "killed" });
  });

  it("holds simulations back until the launch has run", () => {
    expect(labAt(tiny(), 389).simulations).toEqual([]);
    expect(labAt(tiny(), 399.9).simulations).toEqual([]);
  });

  it("brings the evaluation in across its phases on the real fixture", () => {
    const watch = fixture.phases.find((p) => p.key === "watch")!;
    const judge = fixture.phases.find((p) => p.key === "judge")!;
    const mid = labAt(fixture, (watch.started! + watch.ended!) / 2);
    expect(mid.evaluation).not.toBeNull();
    expect(mid.evaluation!.viewers.length).toBe(fixture.evaluation!.viewers.length);
    expect(mid.evaluation!.reactions.length).toBeGreaterThan(0);
    expect(mid.evaluation!.reactions.length).toBeLessThan(fixture.evaluation!.reactions.length);
    expect(mid.evaluation!.verdict).toBe("");
    expect(mid.evaluation!.summaries).toEqual([]);
    const judged = labAt(fixture, judge.ended! + 0.01);
    expect(judged.evaluation!.ratings.length).toBe(fixture.evaluation!.ratings.length);
    expect(judged.evaluation!.agreement).toEqual(fixture.evaluation!.agreement);
  });

  it("lands each reel with its render message", () => {
    const render = fixture.phases.find((p) => p.key === "render")!;
    const firstDone = fixture.feed.find((m) => m.t > render.started! && m.concept && fixture.reels.some((r) => r.concept === m.concept))!;
    const st = labAt(fixture, firstDone.t + 0.01);
    expect(st.reels.length).toBe(1);
    expect(st.reels[0]!.concept).toBe(firstDone.concept);
  });
});

describe("demo replay timing", () => {
  it("plays every message exactly once, in recorded order, inside the replay window", () => {
    const map = buildTimeMap(fixture, 75);
    const seen: string[] = [];
    let previous = -1;
    for (let r = 0; r <= 75.001; r += 0.25) {
      const st = labAt(fixture, map.toRecorded(r));
      expect(st.feed.length).toBeGreaterThanOrEqual(previous);
      st.feed.slice(seen.length).forEach((m) => seen.push(m.id));
      previous = st.feed.length;
    }
    const recorded = [...fixture.feed].sort((a, b) => a.t - b.t).map((m) => m.id);
    expect(seen).toEqual(recorded);
  });

  it("keeps the lab running until the last second, then serves the recording", () => {
    const map = buildTimeMap(fixture, 75);
    expect(labAt(fixture, map.toRecorded(74.9)).status).toBe("running");
    expect(labAt(fixture, map.toRecorded(75)).status).toBe(fixture.status);
  });

  it("spreads the room's conversation over a good share of the replay", () => {
    const map = buildTimeMap(fixture, 75);
    const greenlight = fixture.phases.find((p) => p.key === "greenlight")!;
    // the writers' room (brief to greenlight) gets between a third and two thirds of the minute
    const share = map.toReplay(greenlight.ended!) / 75;
    expect(share).toBeGreaterThan(0.33);
    expect(share).toBeLessThan(0.75);
  });
});

describe("reconcile", () => {
  it("keeps unchanged sections by reference", () => {
    const a = labAt(fixture, 300);
    const b = labAt(fixture, 300.001);
    const r = reconcile(a, b);
    expect(r.feed).toBe(a.feed);
    expect(r.concepts).toBe(a.concepts);
    expect(r.elapsed).toBe(300.001);
    expect(r).not.toBe(a);
  });

  it("returns the previous object when nothing changed", () => {
    const a = labAt(fixture, 300);
    expect(reconcile(a, structuredClone(a))).toBe(a);
  });

  it("replaces only the sections that changed", () => {
    const a = labAt(fixture, 300);
    const later = labAt(fixture, 480);
    const r = reconcile(a, later);
    expect(r.feed).not.toBe(a.feed);
    expect(r.feed).toEqual(later.feed);
    expect(r.agents).toBe(a.agents);
  });
});
