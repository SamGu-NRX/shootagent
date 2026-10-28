// Demo-data versions of the delivery features (src/shootagent/compliance/): the listing photo policy, the
// disclosure report, publication with SHA-256 hash chains, and the fair-housing check. The live server is the
// source of truth; these follow its rules closely enough to demonstrate them without a backend.

import type {
  ChainEntry,
  Change,
  ComplianceReport,
  LintFlag,
  LintResult,
  PhotoReport,
  PhotoStatus,
  PolicyItem,
  PolicyResult,
  PolicyVerdict,
  Publication,
  PublishedImage,
  Shoot,
  Version,
  View,
} from "../types";
import { caseOf } from "./data";

// ---- policy (compliance/policy.py) ------------------------------------------------------------------------

const TRUE_PICTURE = "Listing photos must show the property as it is (NAR Code of Ethics, Article 12; MLS photo rules).";

export const REASONS: Record<string, string> = {
  surroundings: `Power lines, poles, towers, roads and neighbouring buildings are part of the property's surroundings; removing them misrepresents the property. ${TRUE_PICTURE}`,
  condition: `Hiding damage or wear (cracks, stains, leaks, mould, rot) misrepresents the property's condition. ${TRUE_PICTURE}`,
  view: `Changing or inventing the view misrepresents the property's location. ${TRUE_PICTURE}`,
  feature: `Adding a feature the property does not have (a pool, a room, a fireplace) misrepresents it. ${TRUE_PICTURE}`,
  structure: `Removing walls, windows, doors or built-ins changes the property's structure in the photo. ${TRUE_PICTURE}`,
  proportions: `Stretching or enlarging a space misrepresents its size. ${TRUE_PICTURE}`,
  gear: "The photographer's equipment is not part of the property; removing it is not an alteration.",
  overlay: "Text and graphics laid over the photo (watermarks, lot lines) are not alterations of the property; lot lines should be marked approximate.",
  sky: "A replaced or enhanced sky changes the scene; the photo is marked digitally altered (AB 723).",
  lawn: "Greening or filling a lawn changes how the property looks; the photo is marked digitally altered (AB 723).",
  fire: "A fire added to a real fireplace is a common enhancement; the photo is marked digitally altered (AB 723).",
  screen: "Replacing what a TV or monitor shows is a minor change; the photo is still marked digitally altered (AB 723).",
  staging: "Virtual staging must be disclosed: the photo is marked virtually staged (AB 723).",
  colour: "Changing paint, flooring or cabinet colour changes physical features; the photo is marked digitally altered (AB 723).",
  removal: "Removing objects from the scene is a digital alteration under AB 723; the photo is marked as altered and paired with the unaltered original.",
  addition: "Adding objects to the scene is a digital alteration under AB 723; the photo is marked as altered and paired with the unaltered original.",
  change: "Changing what the scene shows is a digital alteration under AB 723; the photo is marked as altered.",
  adjustment: "Exposure, colour correction, straightening, cropping and sharpening are not alterations under AB 723; no disclosure is needed.",
  unclear: "Not recognised as a specific edit; the agent decides from the photo and discloses any alteration.",
};

const RULES: [RegExp, PolicyVerdict, string][] = [
  [/\b(?:power|utility|telephone|overhead|electric(?:al)?)\s+(?:lines?|wires?|cables?|poles?)\b|\bneighbou?r(?:'s|s)?\s+(?:house|home|building|garage|roof)s?\b|\b(?:cell|radio|transmission)\s+towers?\b|\b(?:highway|freeway|railroad|train\s+tracks?|billboards?)\b/i, "decline", "surroundings"],
  [/\bcrack(?:s|ed)?\b|\bwater\s+(?:stains?|damage)\b|\bmou?ld\b|\bpeeling\b|\bholes?\s+in\b|\b(?:ceiling|wall)\s+stains?\b|\bstains?\s+on\s+the\s+(?:ceiling|walls?)\b|\brot(?:ten)?\b|\bleak(?:s|ing)?\b/i, "decline", "condition"],
  [/\b(?:ocean|sea|lake|mountain|city|beach|golf)\s+views?\b|\b(?:replace|swap|change)\s+(?:the\s+)?view\b/i, "decline", "view"],
  [/\b(?:add|put|build|insert)\s+(?:a\s+|an\s+)?(?:swimming\s+)?(?:pool|hot\s+tub|deck|fireplace|balcony|room|window|garage)\b(?!\s+(?:deck|furniture|chairs?))/i, "decline", "feature"],
  [/\b(?:remove|erase|delete)\s+(?:the\s+)?(?:walls?|windows?|doors?|stairs?|staircase|columns?|beams?)\b(?!\s+(?:art|mats?|decor|clocks?|stickers?))/i, "decline", "structure"],
  [/\b(?:look|seem|appear)\s+(?:much\s+)?(?:bigger|larger|more\s+spacious|wider)\b/i, "decline", "proportions"],
  [/\b(?:tripod|camera|photographer|light\s+stand)s?\b/i, "exempt", "gear"],
  [/\b(?:watermarks?|logos?|lot\s+lines?|property\s+lines?|captions?)\b/i, "overlay", "overlay"],
  [/\bvirtual(?:ly)?\s+stag\w*|\bstag(?:e|ing)\b|\bfurnish\w*/i, "disclose", "staging"],
  [/\b(?:sky|skies|clouds?)\b/i, "disclose", "sky"],
  [/\b(?:lawn|grass)\b/i, "disclose", "lawn"],
  [/\b(?:fire|flames?)\b(?!place)/i, "disclose", "fire"],
  [/\b(?:tv|television|screen)\b/i, "disclose", "screen"],
  [/#[0-9a-f]{3,6}\b|\bcolou?r\b|\bpaint\w*|\brecolou?r\w*/i, "disclose", "colour"],
  [/\b(?:remove|erase|delete|take\s+out|get\s+rid\s+of|declutter|clear)\b/i, "disclose", "removal"],
  [/\b(?:bright\w*|dark\w*|lighten|exposure|warm\w*|cool\w*|white\s+balance|straighten\w*|sharpen\w*|contrast)\b/i, "exempt", "adjustment"],
  [/\b(?:add|insert|place|put)\b/i, "disclose", "addition"],
  [/\b(?:change|replace|swap|make|fix|repair)\b/i, "disclose", "change"],
];

const SEVERITY: Record<PolicyVerdict, number> = { decline: 4, disclose: 3, overlay: 2, exempt: 1, unclear: 0 };

function clauses(text: string): string[] {
  const out: string[] = [];
  for (const sentence of text.split(/[.;!?\n]+/)) {
    let verb = "";
    for (const raw of sentence.split(/,\s*(?:and\s+|then\s+)?|\s+(?:and|then|also|but)\s+/i)) {
      const p = raw.trim();
      if (p.length < 2) continue;
      const m = p.match(/\b(remove|erase|delete|add|put|place|replace|change|make|fix|repair|brighten|darken|paint|stage|declutter|clear)\b/i);
      if (m) {
        verb = m[1]!;
        out.push(p);
      } else if (verb && p.split(/\s+/).length <= 6 && !/^(?:in|on|at|behind|beside|near|by|of|for|with|from|to)\b/i.test(p)) {
        out.push(`${verb} ${p}`);
      } else out.push(p);
    }
  }
  return out;
}

export function classifyClause(c: string): PolicyItem {
  const editing = /\b(remove|erase|delete|hide|cover|fix|repair|replace|change|swap|add|put|build|insert|make|clean)\b/i.test(c);
  for (const [re, verdict, category] of RULES) {
    if (!re.test(c)) continue;
    if (verdict === "decline" && (category === "surroundings" || category === "condition" || category === "view") && !editing) continue;
    return { text: c, verdict, category, reason: REASONS[category]! };
  }
  return { text: c, verdict: "unclear", category: "unclear", reason: REASONS.unclear! };
}

export function classifyRequest(text: string): PolicyResult {
  const items = clauses(text).map(classifyClause);
  if (!items.length) return { verdict: "unclear", items: [] };
  const verdict = items.reduce((w, i) => (SEVERITY[i.verdict] > SEVERITY[w] ? i.verdict : w), "unclear" as PolicyVerdict);
  return { verdict, items };
}

// ---- disclosure report (compliance/disclosure.py) ----------------------------------------------------------

export interface RunInfo {
  mode?: string;
  request?: string | null;
  origin?: string | null;
}

export function headChain(view: View): Version[] {
  const byId = new Map(view.versions.map((v) => [v.id, v]));
  const chain: Version[] = [];
  let cur = view.head ? byId.get(view.head) : undefined;
  const seen = new Set<string>();
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    chain.push(cur);
    cur = cur.parent ? byId.get(cur.parent) : undefined;
  }
  return chain.reverse();
}

function classifyVersion(shoot: Shoot, v: Version, run: RunInfo): Change | null {
  const p = v.params ?? {};
  const requested_by = run.origin === "client" ? "client" : run.mode === "chat" ? "customer" : run.mode === "qc" ? "qc" : "unknown";
  const base = { by: "shootagent" as const, requested_by: requested_by as Change["requested_by"], version: v.id, detail: v.note, request: run.request ?? null };
  switch (v.op) {
    case "ingest":
    case "merge":
      return null;
    case "regroup":
      return { ...base, kind: "restoration", what: "re-merged from this camera position's own brackets", category: "restoration", requested_by: "qc" };
    case "restore_from_brackets":
      return { ...base, kind: "restoration", what: "restored to what the camera captured", category: "restoration" };
    case "adjust_exposure": {
      const ev = typeof p.ev === "number" ? p.ev : null;
      return { ...base, kind: "adjustment", what: ev != null ? `exposure ${ev >= 0 ? "+" : ""}${ev.toFixed(2)} EV` : "exposure adjusted", category: "adjustment" };
    }
    case "adjust_white_balance":
      return { ...base, kind: "adjustment", what: "white balance adjusted", category: "adjustment" };
    case "recolor_region": {
      const hex = String(p.hex ?? "");
      const mask = shoot.masks[String(p.mask ?? p.object_mask ?? "")];
      const surface = mask?.query || "surface";
      return run.mode === "qc"
        ? { ...base, kind: "restoration", what: `${surface} colour returned to the camera's ${hex}`, category: "restoration" }
        : { ...base, kind: "alteration", what: `${surface} colour changed to ${hex}`, category: "colour" };
    }
    case "remove_object": {
      const what = String(p.what ?? "object");
      if (classifyClause(`remove ${what}`).category === "gear") return { ...base, kind: "adjustment", what: `photographer's ${what} removed`, category: "gear" };
      return { ...base, kind: "alteration", what: `${what} removed`, category: "removal" };
    }
    case "generative_fill":
      return { ...base, kind: "alteration", what: `area redrawn: ${String(p.prompt ?? "").slice(0, 60)}`, category: "addition" };
    case "place_on_screen":
      return { ...base, kind: "alteration", what: "TV screen picture replaced", category: "screen" };
    case "add_watermark":
      return { ...base, kind: "overlay", what: `watermark “${String(p.text ?? "")}”`, category: "overlay" };
    case "draw_lot_lines":
      return { ...base, kind: "overlay", what: "approximate lot lines drawn", category: "overlay" };
    default:
      return { ...base, kind: "alteration", what: `edited (${v.op.replace(/_/g, " ")})`, category: "change" };
  }
}

const EDITOR: Record<string, [string, string]> = {
  window_sky: ["sky replaced", "sky"],
  twilight: ["converted to a twilight scene", "twilight"],
  grass: ["lawn colour enhanced", "lawn"],
  declutter: ["items removed", "removal"],
  staging: ["virtually staged", "staging"],
  fireplace: ["fire added to the fireplace", "fire"],
  tv_art: ["TV screen picture replaced", "screen"],
};
const DISCLOSED: Record<string, string> = {
  camera_person_removal: "object removed",
  paint_color: "paint colour changed",
  wood_floor_color: "floor colour changed",
};
const ERASED = /\b(?:erased?|missing|removed|lost|absent|gone|translucent|smear\w*|disappear\w*|lacks?)\b/i;

/** compliance/disclosure.py `_may_be_alteration`: unreviewed candidates count only when the detector is confident. */
function mayAlter(type: string, status: string, explanation: string, note: string, confidence: number | null): boolean {
  const erased = type === "camera_person_removal" && ERASED.test(`${explanation} ${note}`);
  if (!(EDITOR[type] || type === "paint_color" || type === "wood_floor_color" || erased)) return false;
  if (status === "failed" || note) return true;
  return (confidence ?? 0) >= (erased ? 0.4 : 0.6);
}

function findingChanges(view: View): Change[] {
  const out: Change[] = [];
  for (const f of view.findings) {
    const note = String(f.evidence.agent_note ?? "");
    const verdict = f.evidence.photographer;
    if (f.status === "dismissed" && verdict === "disclose") {
      out.push({ kind: "alteration", what: DISCLOSED[f.type] ?? EDITOR[f.type]?.[0] ?? "scene edited", detail: f.explanation, by: "editor", requested_by: "order", category: f.type, finding: f.id });
    } else if (f.status === "dismissed" && verdict === "not_alteration") {
      continue;
    } else if (f.status === "dismissed" && EDITOR[f.type]) {
      const [what, category] = EDITOR[f.type]!;
      out.push({ kind: "alteration", what, detail: note || f.explanation, by: "editor", requested_by: "order", category, finding: f.id });
    } else if ((f.status === "open" || f.status === "failed") && mayAlter(f.type, f.status, f.explanation, note, f.confidence)) {
      out.push({ kind: "possible_alteration", what: f.explanation, detail: `QC finding ${f.status === "open" ? "unresolved" : "could not be repaired"}`, by: "editor", requested_by: "qc", category: f.type, finding: f.id });
    }
  }
  return out;
}

export function disclosureText(changes: Change[]): string | null {
  const alts = changes.filter((c) => c.kind === "alteration");
  if (!alts.length) return null;
  const staged = alts.some((c) => c.category === "staging");
  const whats: string[] = [];
  for (const c of alts) {
    const w = (staged && c.category === "staging" ? "" : c.what).trim().replace(/[.;:,]+$/, "");
    if (w && !whats.includes(w)) whats.push(w);
  }
  const head = staged ? "Virtually staged" : "Digitally altered";
  if (!whats.length) return `${head}.`;
  const shown = whats.length > 4 ? [...whats.slice(0, 4), `${whats.length - 4} more changes`] : whats;
  return `${head}: ${shown.join(", ")}.`;
}

const ROOM_ORDER = ["exterior", "front", "entry", "living", "family", "great room", "kitchen", "dining", "office", "primary bedroom", "bedroom", "nursery", "bath", "laundry", "garage", "patio", "backyard"];
const roomRank = (room: string | null | undefined) => {
  const r = (room ?? "").toLowerCase().replace(/_/g, " ");
  const i = ROOM_ORDER.findIndex((k) => r.includes(k));
  return i >= 0 ? i : ROOM_ORDER.indexOf("patio");
};

export function photoReport(shoot: Shoot, view: View, runInfo: (run: string | null) => RunInfo, qcDone: boolean): PhotoReport {
  const chain = headChain(view);
  const changes = [...findingChanges(view), ...chain.map((v) => classifyVersion(shoot, v, runInfo(v.run))).filter((c): c is Change => c != null)];
  const qcChecked = qcDone || view.derived_from != null || !!view.meta.qc_at;
  const hasBrackets = view.brackets.length > 0;
  let status: PhotoStatus;
  const notes: string[] = [];
  const split = view.meta.split_into as string[] | undefined;
  if (split?.length) {
    status = "superseded";
    notes.push(`Replaced by ${split.join(", ")} after regrouping its brackets.`);
  } else if (changes.some((c) => c.kind === "possible_alteration")) status = "review";
  else if (changes.some((c) => c.kind === "alteration")) status = "altered";
  else if (hasBrackets && !qcChecked) status = "unverified";
  else status = "unaltered";
  if (status !== "superseded" && hasBrackets && !qcChecked) notes.push("The editor's own changes have not been checked against the brackets; run QC.");
  const head = view.versions.find((v) => v.id === view.head);
  return {
    view: view.id,
    label: view.label || view.id,
    room: view.room,
    status,
    changes,
    disclosure: status === "altered" || status === "review" ? disclosureText(changes) : null,
    head: view.head,
    final_blob: head?.blob ?? null,
    delivered_blob: view.finished ?? view.versions[0]?.blob ?? null,
    has_brackets: hasBrackets,
    qc_checked: qcChecked,
    deliver: status !== "superseded",
    notes,
  };
}

export function listingViews(shoot: Shoot, photos: PhotoReport[]): PhotoReport[] {
  const live = photos.filter((p) => p.deliver && p.final_blob);
  const approved = live.filter((p) => shoot.views.find((v) => v.id === p.view)?.approved);
  const chosen = approved.length ? approved : live;
  const index = new Map(shoot.views.map((v, i) => [v.id, i]));
  return [...chosen].sort((a, b) => roomRank(a.room ?? a.label) - roomRank(b.room ?? b.label) || (index.get(a.view) ?? 0) - (index.get(b.view) ?? 0));
}

export function complianceReport(shoot: Shoot, runInfo: (run: string | null) => RunInfo, qcDone: boolean): ComplianceReport {
  const photos = shoot.views.filter((v) => v.head).map((v) => photoReport(shoot, v, runInfo, qcDone));
  const live = listingViews(shoot, photos);
  const counts: Record<string, number> = {};
  for (const s of ["altered", "unaltered", "unverified", "review", "superseded"]) counts[s] = photos.filter((p) => p.status === s).length;
  const issues: string[] = [];
  const review = live.filter((p) => p.status === "review");
  if (review.length) issues.push(`${review.length} photo${review.length > 1 ? "s" : ""} in the listing need${review.length > 1 ? "" : "s"} a decision on a QC finding before publishing: ${review.map((p) => p.label).join(", ")}.`);
  const unverified = live.filter((p) => p.has_brackets && !p.qc_checked);
  if (unverified.length) issues.push(`${unverified.length} photo${unverified.length > 1 ? "s were" : " was"} not checked against the brackets, so changes the editor made may be missing from the disclosures. Run QC first.`);
  const pub = shoot.publication ?? null;
  const heads = Object.fromEntries(live.map((p) => [p.view, p.head ?? ""]));
  const stale = !pub || JSON.stringify(heads) !== JSON.stringify(pub.heads);
  return {
    shoot: shoot.id,
    generated_at: new Date().toISOString(),
    photos,
    counts,
    ready: !review.length,
    issues,
    listing: live.map((p) => p.view),
    stale,
    publication: pub ? { token: pub.token, version: pub.version, created_at: pub.created_at, root: pub.root, photos: pub.photos } : null,
    public_token: shoot.public_token ?? null,
  };
}

// ---- publication with real SHA-256 over the demo images (compliance/publish.py) ----------------------------

const hex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

export async function sha256Text(text: string): Promise<string> {
  return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

export async function sha256Url(url: string): Promise<string | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return hex(await crypto.subtle.digest("SHA-256", await res.arrayBuffer()));
  } catch {
    return null;
  }
}

/** Sorted keys, no spaces: the text each chain entry's hash is taken over. */
export function canonical(x: unknown): string {
  if (x === null || typeof x !== "object") return JSON.stringify(x ?? null);
  if (Array.isArray(x)) return `[${x.map(canonical).join(",")}]`;
  const o = x as Record<string, unknown>;
  return `{${Object.keys(o)
    .filter((k) => o[k] !== undefined)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${canonical(o[k])}`)
    .join(",")}}`;
}

async function chainOf(bodies: Record<string, unknown>[]): Promise<ChainEntry[]> {
  const out: ChainEntry[] = [];
  for (const b of bodies) {
    const body = canonical({ ...b, prev: out.length ? out[out.length - 1]!.hash : null });
    out.push({ hash: await sha256Text(body), body });
  }
  return out;
}

const SIZE = (blob: string, view: View): [number, number] => {
  try {
    const [w, h] = caseOf(view.id).size;
    return [w, h];
  } catch {
    return blob ? [1600, 1067] : [0, 0];
  }
};

export async function publishMock(shoot: Shoot, report: ComplianceReport, fileUrl: (blob: string) => string): Promise<Publication> {
  const digests = new Map<string, string | null>();
  const digest = async (blob: string | null | undefined) => {
    if (!blob) return null;
    if (!digests.has(blob)) digests.set(blob, await sha256Url(fileUrl(blob)));
    return digests.get(blob) ?? null;
  };
  const image = async (blob: string, view: View): Promise<PublishedImage> => {
    const [width, height] = SIZE(blob, view);
    return { blob, sha256: (await digest(blob)) ?? "", width, height };
  };
  const byView = new Map(report.photos.map((p) => [p.view, p]));
  const photos: Publication["photos"] = [];
  let order = 0;
  for (const viewId of report.listing) {
    const rep = byView.get(viewId)!;
    const view = shoot.views.find((v) => v.id === viewId)!;
    const final = await image(rep.final_blob!, view);
    const captureBlob = view.reference;
    const capture = captureBlob ? await image(captureBlob, view) : null;
    let original: PublishedImage | null = null;
    let original_kind: "capture" | "delivered" | null = null;
    if (rep.status === "altered" || rep.status === "review") {
      const editorAltered = rep.changes.some((c) => c.by === "editor" && (c.kind === "alteration" || c.kind === "possible_alteration"));
      if (rep.qc_checked && !editorAltered && view.versions[0]) {
        original_kind = "delivered";
        original = await image(view.versions[0].blob, view);
      } else if (capture) {
        original_kind = "capture";
        original = capture;
      }
    }
    const chain = headChain(view);
    const blobs = new Map(view.versions.map((v) => [v.id, v.blob]));
    const byVersion = new Map(rep.changes.filter((c) => c.version).map((c) => [c.version!, c]));
    const bodies: Record<string, unknown>[] = [];
    if (view.brackets.length) {
      bodies.push({ step: "captured", view: view.id, brackets: await Promise.all(view.brackets.map(async (b) => ({ index: b.index, ev: b.ev, blob: b.blob, sha256: await digest(b.blob) }))) });
    }
    const root = chain[0];
    if (root) bodies.push({ step: "delivered", by: "editor", version: root.id, blob: root.blob, sha256: await digest(root.blob) });
    for (const v of chain.slice(1)) {
      const change = byVersion.get(v.id);
      const parentBlob = v.parent ? blobs.get(v.parent) : undefined;
      bodies.push({
        step: "edit",
        version: v.id,
        parent: v.parent,
        op: v.op,
        kind: change?.kind ?? null,
        what: change?.what ?? null,
        requested_by: change?.requested_by ?? null,
        blob: v.blob,
        sha256: await digest(v.blob),
        parent_blob: parentBlob ?? null,
        parent_sha256: await digest(parentBlob),
        check: v.check ? { passed: v.check.passed, outside_max_delta: v.check.metrics?.outside_max_delta ?? null, judge: v.check.judge?.verdict ?? null } : null,
        run: v.run,
        at: v.created_at,
      });
    }
    bodies.push({
      step: "published",
      view: view.id,
      label: rep.label,
      head: view.head,
      status: rep.status,
      disclosure: rep.disclosure,
      editor_changes: rep.changes.filter((c) => c.by === "editor").map((c) => ({ kind: c.kind, what: c.what, finding: c.finding ?? null })),
      final,
      original,
      original_kind,
      capture,
      at: new Date().toISOString(),
    });
    order += 1;
    photos.push({
      view: view.id,
      label: rep.label,
      room: rep.room,
      order,
      status: rep.status,
      disclosure: rep.disclosure,
      head: view.head ?? "",
      final,
      original,
      original_kind,
      capture,
      changes: rep.changes,
      chain: await chainOf(bodies),
    });
  }
  const prev = shoot.publication;
  return {
    token: shoot.public_token ?? `demo-${shoot.id}`,
    version: (prev?.version ?? 0) + 1,
    created_at: new Date().toISOString(),
    root: await sha256Text(photos.map((p) => p.chain[p.chain.length - 1]!.hash).join("\n")),
    heads: Object.fromEntries(photos.map((p) => [p.view, p.head])),
    photos,
    manifest_blob: null,
  };
}

// ---- fair-housing wording check (compliance/fairhousing.py, abridged) ---------------------------------------

const LINT: [RegExp, LintFlag["severity"], string, string][] = [
  [/\b(?:perfect|ideal|great|made)\s+for\s+(?:a\s+)?(?:young\s+)?(?:families|family|kids|children|couples?|singles?|retirees|seniors|students|professionals|newlyweds|empty[- ]nesters)\b/gi, "avoid", "Describe the space instead (\"three bedrooms near the park\").", "Says who should live there (familial status, age)."],
  [/\bfamily[- ]friendly\b|\bkid[- ]friendly\b/gi, "avoid", "Name the feature (\"fenced yard\", \"cul-de-sac\").", "Signals a preference for families with children."],
  [/\badults?\s+only\b|\bno\s+(?:kids|children)\b|\bempty[- ]nesters?\b|\bbachelor\s+pad\b/gi, "avoid", "Remove it.", "Excludes people by familial status or age."],
  [/\bnear\s+(?:the\s+|a\s+)?(?:church|synagogue|mosque|temple)\b/gi, "avoid", "Leave places of worship out.", "Implies a religious preference."],
  [/\b(?:exclusive|restricted)\s+(?:community|neighbou?rhood|area)\b/gi, "review", "\"Gated community\" or \"quiet street\", if true.", "Can read as excluding people."],
  [/\bsafe\s+(?:neighbou?rhood|area|community|street)\b/gi, "review", "Describe specific features (\"quiet cul-de-sac\").", "Subjective safety claims can read as steering."],
  [/\bmaster\s+(?:bed(?:room)?|suite|bath(?:room)?|closet)s?\b/gi, "review", "\"Primary bedroom\", \"primary suite\".", "Many MLSs now ask for \"primary\" instead of \"master\"."],
  [/\bman\s+cave\b/gi, "review", "\"Bonus room\", \"den\".", "Gendered wording; describe the room."],
  [/\bmother[- ]in[- ]law\s+(?:suite|unit|apartment)\b/gi, "review", "\"Guest suite\", \"accessory dwelling unit\".", "Familial wording; describe the space."],
  [/\bhandicap(?:ped)?\b|\bwheelchair[- ]bound\b/gi, "review", "\"Accessible\", \"step-free entry\".", "Describe accessibility features, not people."],
];

export function lintListing(text: string): LintResult {
  const flags: LintFlag[] = [];
  for (const [re, severity, suggestion, why] of LINT) {
    for (const m of text.matchAll(re)) flags.push({ phrase: m[0], start: m.index ?? 0, end: (m.index ?? 0) + m[0].length, severity, suggestion, why });
  }
  flags.sort((a, b) => a.start - b.start);
  return { ok: !flags.some((f) => f.severity === "avoid"), flags, note: "A wording check against common fair-housing guidance, not legal advice." };
}
