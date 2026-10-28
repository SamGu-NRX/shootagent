import type { Finding, View } from "../api/types";

/** Elapsed time as m:ss.s, for the trace gutter and tape counter. */
export function clock(ms: number): string {
  const total = Math.max(0, ms) / 1000;
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

export function duration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return clock(ms);
}

export function usd(v: number): string {
  return v < 0.01 && v > 0 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

export function pct(v: number, digits = 1): string {
  return `${(v * 100).toFixed(digits)}%`;
}

export function relTime(iso: string | null): string {
  if (!iso) return "";
  const d = Date.now() - Date.parse(iso);
  if (Number.isNaN(d)) return "";
  if (d < 60_000) return "just now";
  if (d < 3_600_000) return `${Math.round(d / 60_000)} min ago`;
  if (d < 86_400_000) return `${Math.round(d / 3_600_000)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const FINDING_LABELS: Record<string, string> = {
  ghosting_grouping: "Ghosting",
  camera_person_removal: "Removal error",
  fireplace: "Fireplace",
  paint_color: "Paint colour",
  wood_floor_color: "Floor colour",
  window_sky: "Window / sky",
  tv_art: "TV / art",
  geometry_lens: "Lens geometry",
};

export const findingLabel = (type: string) => FINDING_LABELS[type] ?? type.replace(/_/g, " ");

export const toolLabel = (tool: string) => tool.replace(/_/g, " ");

export type ViewStatus = "approved" | "open" | "failed" | "fixed" | "edited" | "clean" | "unchecked";

export function viewStatus(view: View, qcRan: boolean): ViewStatus {
  if (view.approved) return "approved";
  const open = view.findings.filter((f) => f.status === "open").length;
  if (open) return "open";
  if (view.findings.some((f) => f.status === "failed")) return "failed";
  if (view.findings.some((f) => f.status === "fixed")) return "fixed";
  if (view.versions.length > 1 && view.head !== view.versions[0]?.id) return "edited";
  return qcRan ? "clean" : "unchecked";
}

export function statusText(status: ViewStatus, view: View): string {
  const count = (s: Finding["status"]) => view.findings.filter((f) => f.status === s).length;
  switch (status) {
    case "approved":
      return "Approved";
    case "open":
      return `${count("open")} to check`;
    case "failed":
      return "Needs review";
    case "fixed":
      return `${count("fixed")} fixed`;
    case "edited":
      return "Edited";
    case "clean":
      return "No issues";
    case "unchecked":
      return "Not checked";
  }
}

interface MetricRow {
  label: string;
  value: string;
  /** true = within limits, false = outside, null = informational */
  ok: boolean | null;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Turns verify.py metrics into readable rows, pairing before/after values. */
export function metricRows(m: Record<string, number | string | null>): MetricRow[] {
  const rows: MetricRow[] = [];
  const used = new Set<string>();
  const take = (...keys: string[]) => keys.forEach((k) => used.add(k));

  const outside = num(m.outside_max_delta);
  if (outside != null) {
    take("outside_max_delta");
    // verify.py OUTSIDE_TOL = 1 (0..255 scale)
    rows.push({ label: "Change outside mask", value: `${outside.toFixed(0)} / 255`, ok: outside <= 1 });
  }
  const evT = num(m.ev_target);
  const evM = num(m.ev_measured);
  if (evT != null && evM != null) {
    take("ev_target", "ev_measured");
    const sign = (v: number) => (v > 0 ? "+" : "") + v.toFixed(2);
    rows.push({ label: "Exposure", value: `${sign(evM)} EV of ${sign(evT)}`, ok: Math.abs(evM - evT) <= 0.15 + 0.5 * Math.abs(evT) });
  }
  const deB = num(m.delta_e_before);
  const deA = num(m.delta_e_after);
  if (deA != null) {
    take("delta_e_before", "delta_e_after");
    rows.push({ label: "ΔE to target", value: deB != null ? `${deB.toFixed(1)} → ${deA.toFixed(1)}` : deA.toFixed(1), ok: null });
  }
  const sB = num(m.region_ssim_before);
  const sA = num(m.region_ssim_to_reference);
  if (sA != null) {
    take("region_ssim_before", "region_ssim_to_reference");
    rows.push({
      label: "Match to brackets",
      value: sB != null ? `${sB.toFixed(2)} → ${sA.toFixed(2)}` : sA.toFixed(2),
      ok: sB != null ? sA + 0.02 >= sB : null,
    });
  }
  const area = num(m.mask_area);
  if (area != null) {
    take("mask_area");
    rows.push({ label: "Mask area", value: pct(area), ok: null });
  }
  for (const [k, v] of Object.entries(m)) {
    if (used.has(k) || v == null) continue;
    rows.push({ label: k.replace(/_/g, " "), value: typeof v === "number" ? +v.toFixed(3) + "" : String(v), ok: null });
  }
  return rows;
}

/** Compact one-line summary of a tool's input for the trace. */
export function inputSummary(input: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(input)) {
    if (k === "view" || v == null) continue;
    let s: string;
    if (Array.isArray(v)) {
      s = v.every((x) => typeof x === "number") ? `[${v.map((x) => +(x as number).toFixed(2)).join(", ")}]` : `${v.length} items`;
    } else if (typeof v === "object") s = "{…}";
    else if (typeof v === "string") s = v.length > 40 ? `“${v.slice(0, 38)}…”` : `“${v}”`;
    else s = String(v);
    parts.push(`${k} ${s}`);
  }
  return parts.join(" · ");
}
