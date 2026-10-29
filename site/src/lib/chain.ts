// The edit record behind one published photo, as ShootAgent writes it (src/shootagent/compliance/provenance.py in
// the product): each entry is canonical JSON whose hash is SHA-256 of its text, and whose "prev" is the entry before.
// The figure hashes the real image files this page serves, so the fingerprints on screen are the files' own.

export type Tamper = "none" | "photo" | "record";

export interface Entry {
  key: string;
  label: string;
  detail: string;
  body: string;
  hash: string;
  /** the image this entry vouches for, when it names one */
  file?: string;
  fileHash?: string;
}

export interface Check {
  entry: Entry;
  hashOk: boolean;
  linkOk: boolean;
  /** null when the entry names no file */
  fileOk: boolean | null;
}

const hex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

export async function sha256(data: ArrayBuffer | string): Promise<string> {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : new Uint8Array(data);
  return hex(await crypto.subtle.digest("SHA-256", bytes));
}

/** Sorted keys, no spaces: the text each entry's hash is taken over. */
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

export const FILES = {
  captured: "/img/street-captured.webp",
  delivered: "/img/street-delivered.webp",
  fixed: "/img/street-fixed.webp",
};

/** The image bytes, and a copy with one byte changed (what "edit the photo" does). */
export async function loadFiles(): Promise<Record<keyof typeof FILES, ArrayBuffer>> {
  const entries = await Promise.all(
    Object.entries(FILES).map(async ([k, url]) => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Could not load ${url}`);
      return [k, await res.arrayBuffer()] as const;
    }),
  );
  return Object.fromEntries(entries) as Record<keyof typeof FILES, ArrayBuffer>;
}

export function flipOneByte(buf: ArrayBuffer): ArrayBuffer {
  const copy = buf.slice(0);
  const view = new Uint8Array(copy);
  const i = Math.floor(view.length * 0.61);
  view[i] = (view[i] ?? 0) ^ 0x01;
  return copy;
}

/**
 * Build the record as published, then apply a tamper to what a reader would receive:
 * "photo" changes one byte of the delivered photo but leaves the record alone; "record" also rewrites the
 * delivered entry so its fingerprint matches the changed photo, which breaks the link to the next entry.
 */
export async function buildRecord(files: Record<keyof typeof FILES, ArrayBuffer>, tamper: Tamper) {
  const real = {
    captured: await sha256(files.captured),
    delivered: await sha256(files.delivered),
    fixed: await sha256(files.fixed),
  };
  const served = { ...real, delivered: tamper === "none" ? real.delivered : await sha256(flipOneByte(files.delivered)) };
  const bodies: { key: string; label: string; detail: string; body: Record<string, unknown>; file?: keyof typeof FILES }[] = [
    { key: "captured", label: "Captured", detail: "The camera's brackets, fused", body: { step: "captured", sha256: real.captured }, file: "captured" },
    { key: "delivered", label: "Delivered", detail: "The editor's photo", body: { step: "delivered", by: "editor", sha256: real.delivered }, file: "delivered" },
    {
      key: "edit",
      label: "Edit",
      detail: "Stop sign restored from the brackets",
      body: { step: "edit", op: "restore_from_brackets", kind: "restoration", check: { passed: true, outside_max_delta: 0 }, sha256: real.fixed },
      file: "fixed",
    },
    { key: "published", label: "Published", detail: "The listing photo", body: { step: "published", disclosure: null, final: real.fixed } },
  ];
  const entries: Entry[] = [];
  let prev: string | null = null;
  for (const b of bodies) {
    const body = canonical({ ...b.body, prev });
    const hash = await sha256(body);
    entries.push({ key: b.key, label: b.label, detail: b.detail, body, hash, file: b.file, fileHash: b.file ? real[b.file] : undefined });
    prev = hash;
  }
  if (tamper === "record") {
    // Someone rewrites the delivered entry to match the changed photo and re-hashes it.
    const i = 1;
    const e = entries[i]!;
    const parsed = JSON.parse(e.body) as Record<string, unknown>;
    const body = canonical({ ...parsed, sha256: served.delivered });
    entries[i] = { ...e, body, hash: await sha256(body), fileHash: served.delivered };
  }
  return { entries, served };
}

/** Check every entry: its fingerprint, its link to the entry before, and the file it names. */
export async function verify(entries: Entry[], served: Record<string, string>): Promise<Check[]> {
  const out: Check[] = [];
  let prev: string | null = null;
  for (const entry of entries) {
    const hashOk = (await sha256(entry.body)) === entry.hash;
    const parsed = JSON.parse(entry.body) as { prev?: string | null; sha256?: string };
    const linkOk = (parsed.prev ?? null) === prev;
    const fileOk = entry.file ? parsed.sha256 === served[entry.file] : null;
    out.push({ entry, hashOk, linkOk, fileOk });
    prev = entry.hash;
  }
  return out;
}
