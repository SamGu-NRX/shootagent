// Scripted trace replays for the mock API. Each step waits `gap` ms, runs `effect` against the mock shoot
// (adding versions, moving heads, updating findings the way the real loop does), then emits one event.
// Metric names and check shapes follow src/shootagent/agent/verify.py.

import type { Finding, Shoot, TraceDataByKind, TraceKind, View } from "../types";
import { caseOf, finding } from "./data";

export interface Ctx {
  shoot: Shoot;
  runId: string;
}

export interface Step {
  gap: number;
  kind: TraceKind;
  view?: string | null;
  data: (ctx: Ctx) => object;
}

const AGENT = "claude-sonnet-5-5";
const JUDGE = "claude-sonnet-5-5";

function view(shoot: Shoot, id: string): View {
  const v = shoot.views.find((x) => x.id === id);
  if (!v) throw new Error(`mock: no view ${id}`);
  return v;
}

function addVersion(
  ctx: Ctx,
  viewId: string,
  blob: string,
  op: string,
  params: Record<string, unknown>,
  mask: string | null,
  note: string,
): string {
  const v = view(ctx.shoot, viewId);
  const id = `${viewId}.${v.versions.length}`;
  v.versions.push({
    id,
    parent: v.head,
    blob,
    op,
    params,
    mask,
    note,
    run: ctx.runId,
    created_at: new Date().toISOString(),
    check: null,
  });
  v.head = id;
  return id;
}

function addMask(ctx: Ctx, viewId: string, id: string, blob: string, source: string, query: string | null, area: number) {
  ctx.shoot.masks[id] = {
    id,
    view: viewId,
    blob,
    source,
    query,
    bbox: caseOf(viewId).box,
    area,
    created_at: new Date().toISOString(),
  };
}

function setFinding(ctx: Ctx, viewId: string, f: Finding) {
  const v = view(ctx.shoot, viewId);
  const i = v.findings.findIndex((x) => x.id === f.id);
  if (i >= 0) v.findings[i] = f;
  else v.findings.push(f);
  return { finding: { ...f } };
}

class Script {
  steps: Step[] = [];
  private n = 0;

  push(gap: number, kind: TraceKind, viewId: string | null, data: (ctx: Ctx) => object) {
    this.steps.push({ gap, kind, view: viewId, data });
  }
  thought(gap: number, viewId: string | null, text: string) {
    this.push(gap, "thought", viewId, () => ({ text }));
  }
  usage(gap: number, input: number, output: number, usd: number, model = AGENT, purpose = "agent") {
    this.push(gap, "usage", null, () => ({ model, input_tokens: input, output_tokens: output, usd, purpose }));
  }
  /** tool_call then tool_result `ms` later. `result` may create versions; it returns the result fields. */
  tool(
    gap: number,
    viewId: string | null,
    tool: string,
    input: Record<string, unknown>,
    ms: number,
    result: (ctx: Ctx) => Partial<TraceDataByKind["tool_result"]> & { summary: string },
  ): string {
    const call_id = `toolu_${(++this.n).toString().padStart(2, "0")}${Math.random().toString(36).slice(2, 8)}`;
    this.push(gap, "tool_call", viewId, () => ({ call_id, tool, input }));
    this.push(ms, "tool_result", viewId, (ctx) => ({ call_id, tool, ok: true, ms, ...result(ctx) }));
    return call_id;
  }
  check(
    gap: number,
    viewId: string,
    callId: string,
    passed: boolean,
    metrics: Record<string, number>,
    reason: string,
    failures: string[] = [],
  ) {
    this.push(gap, "check", viewId, (ctx) => {
      const v = view(ctx.shoot, viewId);
      const head = v.versions.find((x) => x.id === v.head)!;
      const check = {
        call_id: callId,
        version: head.id,
        parent: head.parent,
        passed,
        metrics,
        judge: { verdict: passed ? "pass" : "fail", reason, model: JUDGE },
        failures,
        intent: head.note,
      };
      head.check = check;
      // A failed check moves the head back to the parent (agent/verify.py contract).
      if (!passed && head.parent) v.head = head.parent;
      return check;
    });
  }
}

export function qcScript(shoot: Shoot): Step[] {
  const s = new Script();
  const ids = shoot.views.map((v) => v.id);
  s.push(0, "run_start", null, () => ({ mode: "qc", views: ids }));
  s.thought(500, null, "Starting with the deterministic pass: each finished photo against an aligned fusion of its own brackets.");
  s.usage(80, 6120, 210, 0.0215);
  s.tool(300, null, "list_views", {}, 240, () => ({
    summary: "7 views: living room, kitchen, nursery, laundry, garage and drive, primary bath, great room.",
  }));
  s.tool(250, null, "run_qc_checks", { views: ids }, 4200, () => ({
    summary: "5 candidate regions in 5 views. Laundry and primary bath agree with their brackets.",
  }));
  s.usage(120, 9480, 640, 0.0381);
  s.push(450, "plan", null, () => ({
    text: "Adjudicate each candidate with vision, fix what the brackets prove is wrong, and check every fix.",
    steps: [
      { intent: "Compare all 7 photos with their brackets", tool: "run_qc_checks" },
      { view: "v01", intent: "Lamp left of the sofa looks doubled. Restore it from the brackets", tool: "restore_from_brackets" },
      { view: "v02", intent: "The clock wall lost its blue paint. Recolour it to the bracket hue", tool: "recolor_region" },
      { view: "v03", intent: "The floor lamp's stand was erased. Restore it", tool: "restore_from_brackets" },
      { view: "v05", intent: "A hand still covers the upper-left sky. Remove it", tool: "generative_fill" },
      { view: "v07", intent: "Confirm the fire was in the brackets", tool: "compare_to_brackets" },
    ],
  }));

  // ---- v01: failed check, retry, passed check -------------------------------------------------------------
  const f1 = finding("v01", "f_lamp01", "ghosting_grouping", "The lamp left of the sofa has a doubled shade and finial; the brackets show one lamp.", 0.91);
  s.tool(500, "v01", "view_photo", { view: "v01", region: caseOf("v01").box, compare: "brackets" }, 900, () => ({
    summary: "Crop shows two offset lampshades; the middle bracket has one.",
    images: ["0001_finished_w360.jpg"],
  }));
  s.usage(60, 4210, 180, 0.0164);
  s.push(300, "finding", "v01", (ctx) => setFinding(ctx, "v01", f1));
  s.tool(400, "v01", "mask_from_box", { view: "v01", box: [0.31, 0.36, 0.57, 0.72], refine: false }, 160, (ctx) => {
    addMask(ctx, "v01", "m_box0001", "0001_mask_try1.png", "box", null, 0.094);
    return { summary: "Box mask around the lamp, 9.4% of the frame.", mask: "m_box0001" };
  });
  const c1 = s.tool(300, "v01", "restore_from_brackets", { view: "v01", mask: "m_box0001", bracket: 2 }, 1400, (ctx) => {
    const v = addVersion(ctx, "v01", "0001_fix_try1.jpg", "restore_from_brackets", { mask: "m_box0001", bracket: 2 }, "m_box0001", "Restore the single lamp from the brackets");
    return { summary: "Replaced the box with bracket 2, exposure-matched to the finished photo.", version: v, mask: "m_box0001", images: ["0001_fix_try1_w360.jpg"] };
  });
  s.usage(200, 3880, 120, 0.0142, JUDGE, "judge_edit");
  s.check(700, "v01", c1, false, { outside_max_delta: 0, mask_area: 0.094, region_ssim_to_reference: 0.712, region_ssim_before: 0.744 },
    "The restored box is visibly darker than the wall and sofa around it; the seam reads as a patch.",
    ["region moved away from the brackets (SSIM 0.74 -> 0.71)", "vision judge: visible rectangular seam around the lamp"]);
  s.push(350, "retry", "v01", () => ({
    attempt: 1,
    max_attempts: 3,
    reason: "A box mask pulled wall and sofa from a darker bracket and left a seam.",
    next: "Segment only the lamp with SAM and restore through that mask.",
  }));
  s.tool(300, "v01", "find_regions", { view: "v01", prompt: "floor lamp left of the sofa" }, 1700, (ctx) => {
    addMask(ctx, "v01", "m_lamp0001", "0001_mask.png", "grounding+sam", "floor lamp left of the sofa", 0.009);
    return { summary: "One lamp found (score 0.83). Mask covers 0.9% of the frame.", mask: "m_lamp0001" };
  });
  const c2 = s.tool(300, "v01", "restore_from_brackets", { view: "v01", mask: "m_lamp0001", bracket: 2, feather_px: 6 }, 1200, (ctx) => {
    const v = addVersion(ctx, "v01", "0001_fix.jpg", "restore_from_brackets", { mask: "m_lamp0001", bracket: 2, feather_px: 6 }, "m_lamp0001", "Restore the single lamp from the brackets");
    return { summary: "Restored the lamp from bracket 2 through the SAM mask (0.9% of the frame).", version: v, mask: "m_lamp0001", images: ["0001_fix_w360.jpg"] };
  });
  s.usage(200, 3910, 96, 0.0139, JUDGE, "judge_edit");
  s.check(650, "v01", c2, true, { outside_max_delta: 0, mask_area: 0.009, region_ssim_to_reference: 0.941, region_ssim_before: 0.744 },
    "One lamp with a single shade and finial, matching the bracket. No seam at the mask edge.");
  s.push(250, "finding", "v01", (ctx) => setFinding(ctx, "v01", { ...f1, status: "fixed" }));

  // ---- v02: colour from the brackets ------------------------------------------------------------------------
  const f2 = finding("v02", "f_wall0030", "paint_color", "The clock wall reads grey in the finished photo but slate blue in every bracket.", 0.88);
  s.push(500, "finding", "v02", (ctx) => setFinding(ctx, "v02", f2));
  s.tool(250, "v02", "sample_color", { view: "v02", point: [0.78, 0.44], source: "brackets" }, 300, () => ({
    summary: "Brackets (exposure-normalised): #6F8FB5. Finished photo: #9C9EA0.",
  }));
  s.tool(250, "v02", "find_regions", { view: "v02", prompt: "painted wall behind the clock" }, 1500, (ctx) => {
    addMask(ctx, "v02", "m_wall0030", "0030_mask.png", "grounding+sam", "painted wall behind the clock", 0.066);
    return { summary: "Wall segment found (score 0.77), 6.6% of the frame, clock excluded.", mask: "m_wall0030" };
  });
  const c3 = s.tool(250, "v02", "recolor_region", { view: "v02", mask: "m_wall0030", hex: "#6F8FB5", keep_luminance: true }, 900, (ctx) => {
    const v = addVersion(ctx, "v02", "0030_fix.jpg", "recolor_region", { mask: "m_wall0030", hex: "#6F8FB5" }, "m_wall0030", "Recolour the clock wall to the bracket blue #6F8FB5");
    return { summary: "Recoloured the wall to #6F8FB5, keeping its shading.", version: v, mask: "m_wall0030", images: ["0030_fix_w360.jpg"] };
  });
  s.usage(200, 3790, 88, 0.0137, JUDGE, "judge_edit");
  s.check(600, "v02", c3, true, { outside_max_delta: 0, mask_area: 0.066, delta_e_before: 21.4, delta_e_after: 3.1 },
    "Wall is blue like the brackets; clock, cabinets and trim untouched.");
  s.push(250, "finding", "v02", (ctx) => setFinding(ctx, "v02", { ...f2, status: "fixed" }));

  // ---- v03 ---------------------------------------------------------------------------------------------------
  const f3 = finding("v03", "f_lamp0015", "camera_person_removal", "Tripod removal erased the floor lamp's shaft and legs; the shade floats.", 0.86);
  s.push(450, "finding", "v03", (ctx) => setFinding(ctx, "v03", f3));
  s.tool(250, "v03", "find_regions", { view: "v03", prompt: "floor lamp stand and legs" }, 1600, (ctx) => {
    addMask(ctx, "v03", "m_stand0015", "0015_mask.png", "grounding+sam", "floor lamp stand and legs", 0.031);
    return { summary: "Lamp stand found in the brackets (score 0.71), 3.1% of the frame.", mask: "m_stand0015" };
  });
  const c4 = s.tool(250, "v03", "restore_from_brackets", { view: "v03", mask: "m_stand0015", bracket: 2 }, 1100, (ctx) => {
    const v = addVersion(ctx, "v03", "0015_fix.jpg", "restore_from_brackets", { mask: "m_stand0015", bracket: 2 }, "m_stand0015", "Restore the floor lamp's stand from the brackets");
    return { summary: "Restored the lamp's shaft and three legs from bracket 2.", version: v, mask: "m_stand0015", images: ["0015_fix_w360.jpg"] };
  });
  s.usage(200, 3850, 102, 0.0141, JUDGE, "judge_edit");
  s.check(600, "v03", c4, true, { outside_max_delta: 0, mask_area: 0.031, region_ssim_to_reference: 0.902, region_ssim_before: 0.611 },
    "The lamp stands on its legs again and matches the bracket.");
  s.push(250, "finding", "v03", (ctx) => setFinding(ctx, "v03", { ...f3, status: "fixed" }));

  // ---- v05: two attempts, both fail; flagged for the photographer -------------------------------------------
  const f5 = finding("v05", "f_hand0004", "camera_person_removal", "A hand enters the sky from the upper-left edge.", 0.94);
  s.push(450, "finding", "v05", (ctx) => setFinding(ctx, "v05", f5));
  s.tool(250, "v05", "mask_from_box", { view: "v05", box: caseOf("v05").box, refine: true }, 900, (ctx) => {
    addMask(ctx, "v05", "m_hand0004", "0004_mask.png", "box+sam", null, 0.041);
    return { summary: "Hand mask from the box, 4.1% of the frame.", mask: "m_hand0004" };
  });
  const c5 = s.tool(250, "v05", "remove_object", { view: "v05", mask: "m_hand0004", method: "lama" }, 2100, (ctx) => {
    const v = addVersion(ctx, "v05", "0004_try1.jpg", "remove_object", { mask: "m_hand0004", method: "lama" }, "m_hand0004", "Remove the hand from the sky");
    return { summary: "Inpainted the hand with LaMa.", version: v, mask: "m_hand0004", images: ["0004_try1_w360.jpg"] };
  });
  s.usage(200, 3700, 90, 0.0133, JUDGE, "judge_edit");
  s.check(600, "v05", c5, false, { outside_max_delta: 0, mask_area: 0.041 },
    "The hand is gone but the fill is a soft smear with no cloud texture; it reads as a blur in the sky.",
    ["vision judge: smeared fill with no cloud texture"]);
  s.push(300, "retry", "v05", () => ({ attempt: 1, max_attempts: 3, reason: "LaMa smeared the clouds.", next: "Generative fill with the surrounding sky as reference." }));
  const c6 = s.tool(300, "v05", "generative_fill", { view: "v05", mask: "m_hand0004", prompt: "blue sky with soft white clouds" }, 5200, (ctx) => {
    const v = addVersion(ctx, "v05", "0004_try2.jpg", "generative_fill", { mask: "m_hand0004" }, "m_hand0004", "Remove the hand from the sky");
    return { summary: "Filled the masked sky with gpt-image-2.", version: v, mask: "m_hand0004", images: ["0004_try2_w360.jpg"] };
  });
  s.usage(150, 0, 0, 0.042, "gpt-image-2", "generative_edit");
  s.usage(150, 3720, 92, 0.0134, JUDGE, "judge_edit");
  s.check(700, "v05", c6, false, { outside_max_delta: 0, mask_area: 0.041 },
    "The filled area is a flat patch lighter than the sky around it.",
    ["vision judge: flat patch, brighter than the surrounding sky"]);
  s.thought(300, "v05", "Two fills failed their checks. Keeping the original and flagging this one for the photographer rather than shipping a visible patch.");
  s.push(200, "finding", "v05", (ctx) => setFinding(ctx, "v05", { ...f5, status: "failed" }));

  // ---- v07: candidate dismissed ---------------------------------------------------------------------------
  s.tool(450, "v07", "compare_to_brackets", { view: "v07", region: [0.66, 0.42, 0.74, 0.58] }, 700, () => ({
    summary: "The fire is present in brackets 1 to 3 at the same position.",
  }));
  s.thought(250, "v07", "Great room fire is in the brackets, so it was captured, not added. Not a defect.");
  s.usage(100, 5210, 410, 0.0219);
  s.push(400, "message", null, () => ({
    text:
      "Fixed 3 of 4 defects: the doubled lamp in the living room, the kitchen wall colour, and the nursery lamp stand. " +
      "The hand in the garage sky needs your call: two automatic fills left visible patches, so I kept the original. " +
      "Laundry, primary bath and great room match their brackets.",
  }));
  s.push(250, "run_end", null, () => ({
    ok: true,
    summary: "3 fixed, 1 needs review, 3 clean.",
    ms: 0,
    usd: 0,
    counts: { views: 7, findings: 4, fixed: 3, failed: 1 },
  }));
  return s.steps;
}

/** Keyword-routed chat replies, enough to demonstrate applied / not-needed / scoped results. */
export function chatScript(shoot: Shoot, message: string, views: string[] | undefined, maskId: string | undefined): Step[] {
  const s = new Script();
  const scope = views?.length ? views : shoot.views.map((v) => v.id);
  const label = (id: string) => view(shoot, id).label || id;
  const lower = message.toLowerCase();
  s.push(0, "run_start", null, () => ({ mode: "chat", request: message, views: scope }));
  s.usage(300, 7400, 260, 0.0262);

  if (/bright|exposure|dark/.test(lower)) {
    const kitchens = scope.filter((id) => view(shoot, id).room === "kitchen");
    s.push(400, "plan", null, () => ({
      text: "Brighten kitchen photos only, protecting window highlights.",
      steps: [
        { intent: "Find the kitchen photos", tool: "list_views" },
        ...kitchens.map((id) => ({ view: id, intent: "Raise exposure about +0.4 EV", tool: "adjust_exposure" })),
      ],
    }));
    s.tool(300, null, "list_views", {}, 200, () => ({
      summary: kitchens.length ? `Kitchen photos in scope: ${kitchens.map(label).join(", ")}.` : "No kitchen photos in scope.",
    }));
    for (const id of scope.filter((x) => !kitchens.includes(x))) s.thought(120, id, `${label(id)} is not a kitchen.`);
    for (const id of kitchens) {
      const c = s.tool(300, id, "adjust_exposure", { view: id, ev: 0.4, protect_highlights: true }, 600, (ctx) => {
        const v = view(ctx.shoot, id);
        const blob = v.versions.find((x) => x.id === v.head)?.blob === "0030_fix.jpg" ? "0030_bright.jpg" : "0030_bright_only.jpg";
        const vid = addVersion(ctx, id, blob, "adjust_exposure", { ev: 0.4 }, null, "Brighten the kitchen by about +0.4 EV");
        return { summary: "Raised exposure +0.4 EV; window highlights held below clipping.", version: vid, images: [blob.replace(".jpg", "_w360.jpg")] };
      });
      s.usage(200, 3600, 80, 0.0128, JUDGE, "judge_edit");
      s.check(500, id, c, true, { ev_target: 0.4, ev_measured: 0.38 }, "Kitchen is brighter and the window view still holds detail.");
    }
    s.push(300, "message", null, () => ({
      text: kitchens.length
        ? `Brightened ${kitchens.map(label).join(", ")} by +0.4 EV. The other photos aren't kitchens, so I left them alone.`
        : "None of the selected photos is a kitchen, so nothing changed.",
    }));
  } else if (/trash|bin|garbage|remove/.test(lower)) {
    s.push(400, "plan", null, () => ({
      text: maskId ? "Remove the object under your mask." : "Look for trash bins in every photo, then remove any found.",
      steps: maskId
        ? [{ view: shoot.masks[maskId]?.view, intent: "Remove the masked object", tool: "remove_object" }]
        : [{ intent: "Search all photos for trash bins", tool: "find_regions" }],
    }));
    if (maskId) {
      const id = shoot.masks[maskId]?.view ?? scope[0]!;
      const c = s.tool(300, id, "remove_object", { view: id, mask: maskId, method: "lama" }, 1500, (ctx) => {
        const v = view(ctx.shoot, id);
        const blob = v.versions.find((x) => x.id === v.head)!.blob;
        const vid = addVersion(ctx, id, blob, "remove_object", { mask: maskId }, maskId, "Remove the object under the user's mask");
        return { summary: "Removed the masked object (demo data: image unchanged).", version: vid, mask: maskId };
      });
      s.check(500, id, c, true, { outside_max_delta: 0, mask_area: shoot.masks[maskId]?.area ?? 0 }, "Object removed; surroundings intact.");
      s.push(300, "message", null, () => ({ text: `Removed the object you marked in ${label(id)}.` }));
    } else {
      s.tool(300, null, "find_regions", { views: scope, prompt: "trash bin, garbage can, recycling bin" }, 2600, () => ({
        summary: "No bins in any photo. Best match 0.12 (a shadow on the garage driveway), below the 0.35 threshold.",
      }));
      for (const id of scope) s.thought(80, id, id === "v05" ? "Closest match was a shadow on the driveway (0.12)." : "No bins detected.");
      s.push(300, "message", null, () => ({
        text: "I didn't find trash bins in these photos. If you can see one, shift-click it in the viewer and send the request again.",
      }));
    }
  } else if (/paint|colou?r|match|original|tile/.test(lower)) {
    s.push(400, "plan", null, () => ({
      text: "Compare surface colours with the brackets and fix the ones that drifted.",
      steps: [
        { intent: "Sample wall and floor colours against the brackets", tool: "sample_color" },
        { view: "v04", intent: "Return the floor tiles to their bracket blue", tool: "recolor_region" },
      ],
    }));
    s.tool(300, null, "sample_color", { views: scope, source: "brackets" }, 900, () => ({
      summary: "Laundry tiles differ by 14.8 ΔE from the brackets. Other surfaces are within 4 ΔE.",
    }));
    for (const id of scope.filter((x) => x !== "v04")) {
      s.thought(80, id, id === "v02" && view(shoot, "v02").versions.length > 1 ? "Kitchen wall already matches the bracket blue (ΔE 3.1)." : "Colours match the brackets.");
    }
    if (scope.includes("v04")) {
      s.tool(250, "v04", "find_regions", { view: "v04", prompt: "floor tiles" }, 1400, (ctx) => {
        addMask(ctx, "v04", "m_tile0013", "0013_mask.png", "grounding+sam", "floor tiles", 0.072);
        return { summary: "Floor tiles found, 7.2% of the frame; washer and threshold excluded.", mask: "m_tile0013" };
      });
      const c = s.tool(250, "v04", "recolor_region", { view: "v04", mask: "m_tile0013", hex: "#4A6FA5" }, 800, (ctx) => {
        const vid = addVersion(ctx, "v04", "0013_fix.jpg", "recolor_region", { mask: "m_tile0013", hex: "#4A6FA5" }, "m_tile0013", "Recolour floor tiles to the bracket blue #4A6FA5");
        return { summary: "Recoloured the tiles to #4A6FA5.", version: vid, mask: "m_tile0013", images: ["0013_fix_w360.jpg"] };
      });
      s.usage(200, 3650, 85, 0.013, JUDGE, "judge_edit");
      s.check(500, "v04", c, true, { outside_max_delta: 0, mask_area: 0.072, delta_e_before: 14.8, delta_e_after: 2.7 }, "Tiles are blue like the brackets; washer untouched.");
    }
    s.push(300, "message", null, () => ({ text: "Matched the laundry floor tiles to the original. Every other surface already matched its brackets." }));
  } else {
    s.push(400, "message", null, () => ({
      text: "With demo data I can brighten rooms, match colours to the brackets, or look for objects to remove. Try one of the suggestions below the box.",
    }));
  }
  s.push(250, "run_end", null, () => ({ ok: true, summary: "Chat request handled.", ms: 0, usd: 0 }));
  return s.steps;
}

export function reelScript(shoot: Shoot): Step[] {
  const s = new Script();
  const approved = shoot.views.filter((v) => v.approved).map((v) => v.id);
  s.push(0, "run_start", null, () => ({ mode: "reel", views: approved }));
  s.tool(300, null, "plan_reel", { views: approved, aspect: "9:16", seconds: 15 }, 900, () => ({
    summary: `Ordered ${approved.length} approved photos: exterior first, then living spaces, then bedrooms.`,
  }));
  s.tool(250, null, "render_reel", { fps: 30 }, 2600, () => ({ summary: "Rendered 1080×1920, 15 s." }));
  s.push(300, "message", null, () => ({ text: "Demo data has no rendered video file. With the live server, the reel plays here." }));
  s.push(200, "run_end", null, () => ({ ok: true, summary: "Reel rendered.", ms: 0, usd: 0 }));
  return s.steps;
}
