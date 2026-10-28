// Mock shoots built from the AutoHDR defect cases copied into public/mock by scripts/make_mock_assets.py.

import manifestJson from "../../../public/mock/manifest.json";
import type { Bracket, Finding, Shoot, Version, View } from "../types";

interface CaseInfo {
  case: string;
  size: [number, number];
  brackets: number;
  ev: number[];
  title: string | null;
  family: string | null;
  box: number[] | null;
  description: string | null;
}

export const manifest = manifestJson as unknown as Record<string, CaseInfo>;

export const VIEW_PLAN: { id: string; case: string; room: string; label: string }[] = [
  { id: "v01", case: "0001", room: "living_room", label: "Living room" },
  { id: "v02", case: "0030", room: "kitchen", label: "Kitchen" },
  { id: "v03", case: "0015", room: "bedroom", label: "Nursery" },
  { id: "v04", case: "0013", room: "laundry", label: "Laundry" },
  { id: "v05", case: "0004", room: "exterior", label: "Garage and drive" },
  { id: "v06", case: "0039", room: "bathroom", label: "Primary bath" },
  { id: "v07", case: "0018", room: "living_room", label: "Great room" },
];

export function caseOf(viewId: string): CaseInfo {
  const plan = VIEW_PLAN.find((v) => v.id === viewId);
  if (!plan) throw new Error(`mock: no view ${viewId}`);
  return manifest[plan.case]!;
}

const nowIso = () => new Date().toISOString();

function ingest(viewId: string, blob: string): Version {
  return {
    id: `${viewId}.0`,
    parent: null,
    blob,
    op: "ingest",
    params: {},
    mask: null,
    note: "AutoHDR finished photo",
    run: null,
    created_at: nowIso(),
    check: null,
  };
}

export function makeShoot(id: string, title: string): Shoot {
  const views: View[] = VIEW_PLAN.map((p) => {
    const info = manifest[p.case]!;
    const brackets: Bracket[] = Array.from({ length: info.brackets }, (_, i) => ({
      blob: `${p.case}_bracket${i}.jpg`,
      index: i,
      ev: info.ev[i] ?? null,
      name: `bracket_${String(i).padStart(2, "0")}.jpg`,
    }));
    const finished = `${p.case}_finished.jpg`;
    return {
      id: p.id,
      label: p.label,
      room: p.room,
      brackets,
      finished,
      reference: `${p.case}_reference.jpg`,
      groups: null,
      derived_from: null,
      versions: [ingest(p.id, finished)],
      head: `${p.id}.0`,
      findings: [],
      approved: false,
      meta: { case: info.case, width: info.size[0], height: info.size[1] },
    };
  });
  return {
    schema: 1,
    id,
    title,
    created_at: nowIso(),
    source: { kind: "demo", dataset: "autohdr/defect" },
    listing: { address: title, price: null, beds: null, baths: null, sqft: null, headline: null },
    views,
    masks: {},
    runs: [],
  };
}

export function finding(viewId: string, id: string, type: string, explanation: string, confidence: number): Finding {
  return {
    id,
    type,
    explanation,
    region: caseOf(viewId).box,
    confidence,
    status: "open",
    evidence: {},
  };
}
