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
    e("retry", { attempt: 2, reason: "seam", next: "SAM mask" }, "v01"),
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
