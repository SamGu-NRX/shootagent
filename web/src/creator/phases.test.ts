import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { groupFeed, messagePhase, PHASES, stepPhases } from "./phases";
import type { LabPhase, LabState, RoomKind, RoomMessage } from "./types";

const phase = (key: string, started: number | null, ended: number | null, status: LabPhase["status"] = "done"): LabPhase => ({
  key,
  label: key,
  status,
  started,
  ended,
  detail: "",
});
let n = 0;
const msg = (t: number, agent: string, kind: RoomKind, concept: string | null = null): RoomMessage => ({ id: `m${++n}`, t, agent, kind, text: `${kind} at ${t}`, concept, refs: [] });

const phases = [
  phase("brief", 0, 30),
  phase("pitch", 31, 120),
  phase("critique", 121, 200),
  phase("remix", 201, 280),
  phase("greenlight", 281, 330),
  phase("single_agent", 32, 96),
  phase("render", 331, 460),
];

describe("stepPhases", () => {
  it("always shows the contract's phases, pending until the lab reports them", () => {
    const steps = stepPhases(null);
    expect(steps.map((p) => p.key)).toEqual(PHASES.map((p) => p.key));
    expect(steps.every((p) => p.status === "pending")).toBe(true);
    expect(steps.find((p) => p.key === "render")!.section).toBe("reels");
    expect(steps.find((p) => p.key === "simulate")!.section).toBe("launch");
  });

  it("uses the lab's statuses and keeps unknown phases next to their neighbours", () => {
    const lab = { phases: [phase("brief", 0, 10), phase("render", 11, null, "running"), phase("color_grade", 12, null, "running")] } as LabState;
    const steps = stepPhases(lab);
    expect(steps.find((p) => p.key === "brief")!.status).toBe("done");
    expect(steps.find((p) => p.key === "render")!.status).toBe("running");
    const i = steps.findIndex((p) => p.key === "color_grade");
    expect(steps[i]!.section).toBe("reels");
    expect(steps[i - 1]!.key).toBe("render");
  });
});

describe("messagePhase", () => {
  it("files a message under the room phase running at its time", () => {
    expect(messagePhase(phases, msg(50, "comedian", "note"))).toBe("pitch");
    expect(messagePhase(phases, msg(150, "fact_checker", "critique"))).toBe("critique");
    expect(messagePhase(phases, msg(400, "producer", "system"))).toBe("render");
  });

  it("prefers the phase that matches the kind on a boundary", () => {
    const touching = [phase("pitch", 31, 121), phase("critique", 121, 200)];
    expect(messagePhase(touching, msg(121, "algorithm", "critique"))).toBe("critique");
    expect(messagePhase(touching, msg(121, "comedian", "pitch"))).toBe("pitch");
    // a wrap-up at the boundary belongs to the phase that is ending
    expect(messagePhase(touching, msg(121, "producer", "system"))).toBe("pitch");
  });

  it("uses the kind for a message between two phases", () => {
    expect(messagePhase(phases, msg(120.5, "algorithm", "critique"))).toBe("critique");
    expect(messagePhase(phases, msg(30.5, "showrunner", "brief"))).toBe("brief");
  });

  it("never files room talk under the single-agent control, even while it runs", () => {
    expect(messagePhase(phases, msg(60, "detective", "pitch"))).toBe("pitch");
  });

  it("falls back to the kind, then the last phase started", () => {
    expect(messagePhase([], msg(5, "showrunner", "greenlight"))).toBe("greenlight");
    expect(messagePhase([phase("brief", 0, 10)], msg(20, "producer", "system"))).toBe("brief");
  });
});

describe("overlapping phases", () => {
  const evals = [phase("watch", 469, 541.3), phase("judge", 472, 566), phase("novelty", 474, 488.2)];

  it("files a message at a phase's edge under that phase", () => {
    expect(messagePhase(evals, msg(469.6, "producer", "system"))).toBe("watch");
    expect(messagePhase(evals, msg(472.4, "producer", "system"))).toBe("judge");
    expect(messagePhase(evals, msg(487.7, "producer", "system"))).toBe("novelty");
    expect(messagePhase(evals, msg(540.7, "producer", "system"))).toBe("watch");
  });

  it("falls back to the latest start away from any edge", () => {
    expect(messagePhase(evals, msg(500, "producer", "system"))).toBe("judge");
  });

  it("groups the recorded lab's feed in the order its phases ran", () => {
    const lab = JSON.parse(readFileSync(new URL("../../public/mock/creative/lab.json", import.meta.url), "utf8")) as LabState;
    const { room, control } = groupFeed(lab);
    expect(room[0]!.phase).toBe("brief");
    expect(room[room.length - 1]!.phase).toBe("simulate");
    for (const key of ["pitch", "critique", "greenlight", "render", "verdict"]) expect(room.some((g) => g.phase === key)).toBe(true);
    const starts = room.map((g) => g.messages[0]!.t);
    for (let i = 1; i < starts.length; i++) expect(starts[i]!).toBeGreaterThanOrEqual(starts[i - 1]!);
    expect(control.length).toBe(lab.feed.filter((m) => m.agent === "single_agent").length);
  });
});

describe("groupFeed", () => {
  const feed = [
    msg(29, "showrunner", "brief"),
    msg(45, "detective", "pitch", "c01"),
    msg(60, "single_agent", "pitch", "sa1"),
    msg(70, "comedian", "pitch", "c03"),
    msg(130, "fact_checker", "critique", "c01"),
    msg(209, "comedian", "steal", "c09"),
    msg(327, "showrunner", "greenlight", "c09"),
    msg(371, "producer", "system", "template"),
  ];

  it("groups the room's messages into runs per phase, in time order", () => {
    const { room } = groupFeed({ feed: [...feed].reverse(), phases });
    expect(room.map((g) => g.phase)).toEqual(["brief", "pitch", "critique", "remix", "greenlight", "render"]);
    expect(room[1]!.messages.map((m) => m.concept)).toEqual(["c01", "c03"]);
    expect(room[1]!.start).toBe(45);
    expect(new Set(room.map((g) => g.id)).size).toBe(room.length);
  });

  it("keeps the single agent's messages apart", () => {
    const { room, control } = groupFeed({ feed, phases });
    expect(control.map((m) => m.concept)).toEqual(["sa1"]);
    expect(room.flatMap((g) => g.messages).some((m) => m.agent === "single_agent")).toBe(false);
  });

  it("labels groups with the lab's own phase labels", () => {
    const labelled = phases.map((p) => (p.key === "pitch" ? { ...p, label: "Pitches" } : p));
    expect(groupFeed({ feed, phases: labelled }).room[1]!.label).toBe("Pitches");
  });
});
