import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { labAt } from "./replay";
import { buildStory, cleanReason, criticScore, firstSentence, shortFeature, type StoryStep } from "./story";
import type { LabState } from "./types";

const lab = JSON.parse(readFileSync(new URL("../../public/mock/creative/lab.json", import.meta.url), "utf8")) as LabState;
const step = <K extends StoryStep["kind"]>(steps: StoryStep[], kind: K) => steps.filter((s): s is Extract<StoryStep, { kind: K }> => s.kind === kind);

describe("text helpers", () => {
  it("takes the first sentence", () => {
    expect(firstSentence("A small flat. It glows.")).toBe("A small flat.");
    expect(firstSentence("No full stop")).toBe("No full stop");
  });

  it("shortens hero details for chips", () => {
    expect(shortFeature("The arched headboard wall: a white arch set into a full-height oak panel.")).toBe("The arched headboard wall");
    const long = shortFeature("A wobbly, organic-shaped mirror with a thick frame striped terracotta and white, upper right.");
    expect(long.endsWith("…")).toBe(true);
    expect(long.length).toBeLessThanOrEqual(35);
  });

  it("strips pick and greenlight preambles", () => {
    expect(cleanReason("Strongest: c05. It's a true find-it game.")).toBe("It's a true find-it game.");
    expect(cleanReason('Greenlit: c17, "Two Things Out of Place". This is my pick. A flat this tidy hides two true slips.')).toBe("A flat this tidy hides two true slips.");
    expect(cleanReason('Wildcard: c24, "Find the Earbud\'s Counter". This is the wildcard. The one thing nobody styled narrates.')).toBe("The one thing nobody styled narrates.");
    expect(cleanReason('c19 "Number Seven Is a Wall" got all five votes.')).toBe("Got all five votes.");
    expect(cleanReason('Greenlit: c13, "Shut. Shut. Shut. Not Shut.". Tight loop.')).toBe("Tight loop.");
  });
});

describe("buildStory on the recorded lab", () => {
  const steps = buildStory(lab);

  it("tells the room's story in the order it happened", () => {
    expect(steps.map((s) => s.kind)).toEqual(["brief", "pitches", "critique", "remix", "vote", "critique", "greenlight"]);
    const times = steps.map((s) => s.t);
    expect([...times].sort((a, b) => a - b)).toEqual(times);
  });

  it("condenses the brief to one sentence and three hero details", () => {
    const [brief] = step(steps, "brief");
    expect(brief!.summary).toBe(firstSentence(lab.brief!.summary));
    expect(brief!.heroes).toHaveLength(3);
    expect(brief!.heroes[0]!.label).toBe("The arched headboard wall");
  });

  it("counts the pitches and their creators", () => {
    const [p] = step(steps, "pitches");
    expect(p!.concepts).toHaveLength(12);
    expect(p!.creators).toBe(6);
  });

  it("notices when the critics agree, and lists each pick when they don't", () => {
    const [first, second] = step(steps, "critique");
    expect(first!.round).toBe(1);
    expect(first!.agreed).toBe(true);
    expect(first!.picks[0]).toMatchObject({ strongest: "c05", weakest: "c06" });
    expect(first!.picks[0]!.strongestWhy.startsWith("It's")).toBe(true);
    expect(second!.round).toBe(2);
    expect(second!.agreed).toBe(false);
    expect(second!.picks.map((p) => p.strongest).sort()).toEqual(["c13", "c18", "c19"]);
  });

  it("counts remixes and steals and leads with steals that became finalists", () => {
    const [r] = step(steps, "remix");
    expect(r!.remixes).toBe(12);
    expect(r!.steals).toBe(6);
    expect(r!.examples[0]!.child).toBe("c24");
    expect(r!.examples[0]!.parents).toEqual(["c05"]);
  });

  it("tallies the room's votes, not the judges'", () => {
    const [v] = step(steps, "vote");
    expect(v!.total).toBe(6);
    expect(v!.tally[0]).toEqual({ concept: "c19", votes: 5 });
  });

  it("gives each finalist its reason and keeps the one passed over", () => {
    const [g] = step(steps, "greenlight");
    expect(g!.picks.map((p) => p.concept)).toEqual(lab.finalists);
    expect(g!.picks[0]!.reason.startsWith("A flat this tidy")).toBe(true);
    expect(g!.passed.map((p) => p.concept)).toEqual(["c19"]);
  });

  it("scores concepts by their critics", () => {
    expect(criticScore(lab, "c17")).toBeGreaterThan(5);
    expect(criticScore(lab, "nope")).toBe(0);
  });
});

describe("buildStory while the lab runs", () => {
  it("only tells what has happened so far", () => {
    // between the third and fourth room pitch
    const pitches = lab.feed.filter((m) => m.kind === "pitch" && m.agent !== "single_agent").sort((a, b) => a.t - b.t);
    const early = buildStory(labAt(lab, (pitches[2]!.t + pitches[3]!.t) / 2 || pitches[2]!.t));
    expect(early.map((s) => s.kind)).toEqual(["brief", "pitches"]);
    const pitched = step(early, "pitches")[0]!;
    expect(pitched.concepts.length).toBeGreaterThan(0);
    expect(pitched.concepts.length).toBeLessThan(12);
    expect(buildStory(labAt(lab, 0))).toEqual([]);
  });
});
