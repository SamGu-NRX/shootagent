import { describe, expect, it } from "vitest";
import type { TraceDataByKind, TraceEvent, TraceKind } from "../api/types";
import { reduceAll, type ToolItem } from "./reduce";

function stream() {
  let seq = 0;
  const t0 = Date.parse("2026-10-28T03:15:00.000Z");
  return <K extends TraceKind>(kind: K, data: TraceDataByKind[K], view: string | null = null, atMs = seq * 100): TraceEvent =>
    ({ run: "r1", seq: ++seq, t: new Date(t0 + atMs).toISOString(), kind, view, data }) as TraceEvent;
}

function qcRun() {
  const e = stream();
  return [
    e("run_start", { mode: "qc", views: ["v01"] }),
    e("tool_call", { call_id: "c1", tool: "run_qc_checks", input: {} }),
    e("tool_result", { call_id: "c1", tool: "run_qc_checks", ok: true, summary: "1 candidate", ms: 4000 }),
    // plan arrives after perception, as the real agent does
    e("plan", {
      steps: [
        { intent: "Compare with brackets", tool: "run_qc_checks" },
        { view: "v01", intent: "Restore the lamp", tool: "restore_from_brackets" },
      ],
    }),
    e("finding", { finding: { id: "f1", type: "ghosting_grouping", explanation: "doubled lamp", region: [0, 0, 1, 1], confidence: 0.9, status: "open", evidence: {} } }, "v01"),
    e("tool_call", { call_id: "c2", tool: "restore_from_brackets", input: { mask: "m1" } }, "v01"),
    e("tool_result", { call_id: "c2", tool: "restore_from_brackets", ok: true, summary: "box restore", version: "v01.1", ms: 1400 }, "v01"),
    e("usage", { model: "judge", input_tokens: 100, output_tokens: 10, usd: 0.01 }),
    e("check", { call_id: "c2", version: "v01.1", passed: false, metrics: { outside_max_delta: 0 }, failures: ["visible seam"] }, "v01"),
    e("retry", { attempt: 1, reason: "seam", next: "SAM mask", max_attempts: 3 }, "v01"),
    e("tool_call", { call_id: "c3", tool: "restore_from_brackets", input: { mask: "m2" } }, "v01"),
    e("tool_result", { call_id: "c3", tool: "restore_from_brackets", ok: true, summary: "lamp restored", version: "v01.2", ms: 1200 }, "v01"),
    e("usage", { model: "judge", input_tokens: 100, output_tokens: 10, usd: 0.02 }),
    e("check", { call_id: "c3", version: "v01.2", passed: true, metrics: { outside_max_delta: 0 } }, "v01"),
    e("finding", { finding: { id: "f1", type: "ghosting_grouping", explanation: "doubled lamp", region: [0, 0, 1, 1], confidence: 0.9, status: "fixed", evidence: {} } }, "v01"),
    e("run_end", { ok: true, summary: "1 fixed", ms: 9000, usd: 0.05 }),
  ];
}

describe("reduceTrace", () => {
  it("records a failed check, the retry, and the passing second attempt as distinct steps", () => {
    const events = qcRun();
    const failedAt = events.findIndex((e) => e.kind === "check");
    const midway = reduceAll(events.slice(0, failedAt + 1));
    // a failed check must not tick the edit's plan step
    expect(midway.plan.steps.map((s) => s.done)).toEqual([true, false]);

    const s = reduceAll(events);
    expect(s.items.map((i) => i.type)).toEqual(["tool", "finding", "tool", "check", "retry", "tool", "check", "finding", "end"]);
    const tools = s.items.filter((i): i is ToolItem => i.type === "tool");
    expect(tools.map((t) => [t.callId, t.status, t.attempt])).toEqual([
      ["c1", "ok", 1],
      ["c2", "ok", 1],
      ["c3", "ok", 2],
    ]);
    expect(s.items.filter((i) => i.type === "check").map((c) => (c.type === "check" ? [c.passed, c.attempt, c.tool] : null))).toEqual([
      [false, 1, "restore_from_brackets"],
      [true, 2, "restore_from_brackets"],
    ]);
    expect(s.plan.steps.map((st) => st.done)).toEqual([true, true]);
    expect(s.totals).toMatchObject({
      toolCalls: 3,
      checksPassed: 1,
      checksFailed: 1,
      retries: 1,
      findingsFound: 1,
      findingsFixed: 1,
      usd: 0.05,
      ms: 9000,
    });
    expect(s.status).toBe("done");
    expect(s.items[1]!.at).toBe(400);
  });

  it("ignores events replayed after an SSE reconnect", () => {
    const events = qcRun();
    const once = reduceAll(events);
    const replayed = reduceAll([...events.slice(0, 9), ...events]);
    expect(replayed).toEqual(once);
  });

  it("settles each chat view as applied, not needed with the agent's reason, or failed", () => {
    const e = stream();
    const s = reduceAll([
      e("run_start", { mode: "chat", request: "brighten the kitchen", views: ["v1", "v2", "v3"] }),
      e("thought", { text: "Not a kitchen." }, "v2"),
      e("tool_call", { call_id: "a", tool: "adjust_exposure", input: { ev: 0.4 } }, "v1"),
      e("tool_result", { call_id: "a", tool: "adjust_exposure", ok: true, summary: "+0.4 EV", version: "v1.1", ms: 500 }, "v1"),
      e("check", { call_id: "a", version: "v1.1", passed: true, metrics: {} }, "v1"),
      e("tool_call", { call_id: "b", tool: "adjust_exposure", input: { ev: 0.4 } }, "v3"),
      e("tool_result", { call_id: "b", tool: "adjust_exposure", ok: true, summary: "+0.4 EV", version: "v3.1", ms: 500 }, "v3"),
      e("check", { call_id: "b", version: "v3.1", passed: false, metrics: {}, failures: ["windows clipped"] }, "v3"),
      e("run_end", { ok: true, summary: "done", ms: 3000, usd: 0.02 }),
    ]);
    expect(s.viewResults).toEqual({
      v1: { outcome: "applied", reason: "+0.4 EV", version: "v1.1" },
      v2: { outcome: "not_needed", reason: "Not a kitchen." },
      v3: { outcome: "failed", reason: "windows clipped" },
    });
  });
});

describe("view_result", () => {
  it("is authoritative over outcomes inferred from checks, including a later passed check", () => {
    const e = stream();
    const s = reduceAll([
      e("run_start", { mode: "chat", request: "remove the car", views: ["v01", "v02"] }),
      e("view_result", { results: [{ request: "remove the car", status: "not_needed", reason: "No car in this kitchen." }] }, "v01"),
      // a stray passed check on v01 after the report must not flip it to applied
      e("check", { call_id: "c9", version: "v01.1", passed: true, metrics: {} }, "v01"),
      e("view_result", { results: [{ request: "remove the car", status: "failed", reason: "Every fill left a seam." }] }, "v02"),
      e("run_end", { ok: true, summary: "", ms: 1, usd: 0 }),
    ]);
    expect(s.viewResults.v01).toMatchObject({ outcome: "not_needed", reason: "No car in this kitchen." });
    expect(s.viewResults.v02).toMatchObject({ outcome: "failed", reason: "Every fill left a seam." });
  });
});

describe("QC candidates and plan progress (shapes from run 20261027-235730-b73cba)", () => {
  const cand = (id: string, status: "open" | "dismissed" | "fixed") => ({
    finding: { id, type: "paint_color", explanation: "surface colour differs", region: [0, 0, 1, 1], confidence: 0.3, status, evidence: {} },
    images: [`${id}.jpg`],
  });

  it("lists candidates inside their run_qc_checks call, folds verdicts into set_finding rows, and counts only undismissed ones", () => {
    const e = stream();
    const s = reduceAll([
      e("run_start", { mode: "qc", views: ["v02"] }),
      e("tool_call", { call_id: "q", tool: "run_qc_checks", input: { view: "v02" } }, "v02"),
      e("finding", cand("f1", "open"), "v02"),
      e("finding", cand("f2", "open"), "v02"),
      e("tool_result", { call_id: "q", tool: "run_qc_checks", ok: true, summary: "2 candidates", ms: 900 }, "v02"),
      // the agent's verdict: set_finding call, the finding update it causes, then its result
      e("tool_call", { call_id: "d1", tool: "set_finding", input: { view: "v02", finding: "f1", status: "dismissed", note: "grading" } }, "v02"),
      e("finding", cand("f1", "dismissed"), "v02"),
      e("tool_result", { call_id: "d1", tool: "set_finding", ok: true, summary: "f1 dismissed", ms: 20 }, "v02"),
      // an update with no set_finding call (made by the loop) still gets its own row
      e("finding", cand("f2", "fixed"), "v02"),
    ]);
    const tool = s.items[0] as ToolItem;
    expect(tool.candidates).toEqual(["f1", "f2"]);
    expect(s.items.map((i) => (i.type === "finding" ? [i.findingId, i.status] : i.type === "tool" ? i.tool : i.type))).toEqual([
      "run_qc_checks",
      "set_finding",
      ["f2", "fixed"],
    ]);
    expect(s.findingImages.f1).toEqual(["f1.jpg"]);
    expect(s.totals).toMatchObject({ findingsFound: 1, findingsFixed: 1 });
  });

  it("ticks plan steps that name no tool when a tool word appears in the intent, and the rest at a clean finish", () => {
    const e = stream();
    const events = [
      e("run_start", { mode: "qc", views: ["v01"] }),
      e("plan", { steps: [{ intent: "Run QC checks on every view and label rooms" }, { intent: "Adjudicate candidates and fix confirmed defects" }] }),
      e("tool_call", { call_id: "q", tool: "run_qc_checks", input: { view: "v01" } }, "v01"),
      e("tool_result", { call_id: "q", tool: "run_qc_checks", ok: true, summary: "0 candidates", ms: 900 }, "v01"),
    ];
    expect(reduceAll(events).plan.steps.map((st) => st.done)).toEqual([true, false]);
    const done = reduceAll([...events, e("run_end", { ok: true, summary: "", ms: 1, usd: 0 })]);
    expect(done.plan.steps.map((st) => st.done)).toEqual([true, true]);
  });

  it("ticks a step whose planned tool name carries a stray prefix once that tool's edit passes its check", () => {
    const e = stream();
    const s = reduceAll([
      e("run_start", { mode: "chat", request: "put the reference image on this screen", views: ["v08"] }),
      e("plan", { steps: [{ view: "v08", intent: "Place the reference image on the TV", tool: "gloom_place_on_screen" }] }),
      e("tool_call", { call_id: "p", tool: "place_on_screen", input: { view: "v08" } }, "v08"),
      e("tool_result", { call_id: "p", tool: "place_on_screen", ok: true, summary: "placed", version: "v08.1", ms: 600 }, "v08"),
      e("check", { call_id: "p", version: "v08.1", passed: true, metrics: {} }, "v08"),
    ]);
    expect(s.plan.steps.map((st) => st.done)).toEqual([true]);
  });

  it("does not call photos 'not needed' when the run stopped before reaching them, and keeps measured cost and time", () => {
    const e = stream();
    // shape of run 20261028-002223-44dda0: the server restarted and api/app.py appended run_end with t null, ms 0, usd 0
    const s = reduceAll([
      e("run_start", { mode: "chat", request: "remove the cars", views: ["v01", "v02"] }),
      e("usage", { model: "m", input_tokens: 10, output_tokens: 1, usd: 0.05 }),
      e("thought", { text: "Checking v01." }, "v01", 12000),
      { run: "r1", seq: 4, t: null as unknown as string, kind: "run_end", view: null, data: { ok: false, summary: "The run did not finish (server restarted).", ms: 0, usd: 0 } },
    ]);
    expect(s.viewResults.v02).toEqual({ outcome: "failed", reason: "Not reached: the run stopped first." });
    expect(s.totals).toMatchObject({ usd: 0.05, ms: 12000 });
  });
});
