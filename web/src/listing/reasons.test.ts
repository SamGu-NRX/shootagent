import { describe, expect, it } from "vitest";
import { attemptReasons } from "./reasons";

// Failure strings and judge copied from run 20261028-185544-ddefff (7-photo house, shot 0 and shot 1).
const SHOT0_FAILURES = [
  "motion no rigid camera explains: 3.66 px from t=1.25s in cell [0.267, 0.83, 0.333, 0.948]",
  "structure not in the first frame: 7.39% of the frame from t=5.75s at [0.0, 0.0, 0.261, 1.0]",
  "a straight edge bends by 145.83 px from t=3.0s at [-15838.739, -3.617, 993.489, 366713.837]",
  "judge (low confidence): bathroom doorway/vanity room beyond the bedroom door changed_shape at t=2.5s (center-right)",
];
const SHOT0_JUDGE = {
  pass: false,
  changes: [
    { object: "bathroom doorway/vanity room and door on right wall", change: "changed_shape", first_frame: "t=2.5s", where: "center-right" },
    { object: "white door", change: "duplicated", first_frame: "t=3.5s", where: "right side" },
  ],
  confidence: "low",
};

describe("attemptReasons", () => {
  it("leads with what the judge saw and folds the geometry failures into one line", () => {
    const r = attemptReasons(SHOT0_FAILURES, SHOT0_JUDGE);
    expect(r.map((x) => x.plain)).toEqual([
      "Bathroom doorway/vanity room and door on right wall changed shape at 2.5 s",
      "White door duplicated at 3.5 s",
      "Part of the room moved in a way a real camera can't, and 2 more motion checks failed",
    ]);
  });

  it("uses the judge line from the failures when the judge record is missing (manifest attempts)", () => {
    const r = attemptReasons(["judge (high confidence): cluster of grey pendant lights appeared at t=1.2s (top)"], null);
    expect(r[0]!.plain).toBe("Cluster of grey pendant lights appeared");
  });

  it("says a missing second attempt is an offline replay limit, not a failed check", () => {
    const r = attemptReasons(["no recorded clip for this request in C:\\data\\replay (fingerprint 37b7)"], null);
    expect(r[0]!.kind).toBe("unavailable");
  });
});

describe("judge v2 findings", () => {
  // Attempt from the final demo run (manifest shot 1): the judge files the object under "wall_plate" and its
  // description already says what changed.
  const judge = {
    failing: [{ kind: "new", category: "wall_plate", frame: "FRAME 7", confidence: "medium",
      description: "A white outlet plate appears on the left wall near the baseboard behind the sideboard. The photo shows no outlet there." }],
    changes: [{ object: "wall_plate: A white outlet plate appears on the left wall near the baseboard behind the sideboard. The photo shows no outlet there.", change: "appeared", first_frame: "FRAME 7" }],
  };

  it("names the category in words and does not repeat the change word", () => {
    expect(attemptReasons([], judge)[0]!.plain).toBe(
      "Wall plate: A white outlet plate appears on the left wall near the baseboard behind the sideboard. The photo shows no outlet there.",
    );
    expect(attemptReasons([], { changes: judge.changes })[0]!.plain).toBe(attemptReasons([], judge)[0]!.plain);
  });

  it("reads the judge's line in the failures when the attempt has no judge record", () => {
    const line = "judge: built_in new at FRAME 6 (medium): Dark vanity cabinet with gold pulls at right foreground, not in end photo; furniture new at FRAME 7 (medium): Dark vanity cabinet in place of the closed door";
    expect(attemptReasons(["room-to-room walk: can't be verified against a photo", line], null).map((r) => r.plain)).toEqual([
      "A walk from one room into another can't be checked against a single photo",
      "Built-in: Dark vanity cabinet with gold pulls at right foreground, not in end photo",
      "Furniture: Dark vanity cabinet in place of the closed door",
    ]);
  });
});
