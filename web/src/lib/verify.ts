// In-browser check of a published photo's edit record (src/shootagent/compliance/provenance.py): every entry's
// hash is SHA-256 of its text, every entry links to the one before it, and the published files hash to what the
// record says. WebCrypto needs a secure context (https or localhost).

import type { ChainEntry, PublicPhoto } from "../api/types";

const hex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

export async function sha256Bytes(data: ArrayBuffer): Promise<string> {
  return hex(await crypto.subtle.digest("SHA-256", data));
}

export async function sha256Text(text: string): Promise<string> {
  return sha256Bytes(new TextEncoder().encode(text).buffer as ArrayBuffer);
}

export interface VerifyResult {
  ok: boolean;
  entries: number;
  images: number;
  problems: string[];
}

export async function verifyChain(chain: ChainEntry[]): Promise<{ ok: boolean; problems: string[] }> {
  const problems: string[] = [];
  let prev: string | null = null;
  for (const [i, e] of chain.entries()) {
    if ((await sha256Text(e.body)) !== e.hash) problems.push(`Record ${i + 1} does not match its fingerprint.`);
    let body: { prev?: string | null };
    try {
      body = JSON.parse(e.body);
    } catch {
      problems.push(`Record ${i + 1} is not readable.`);
      continue;
    }
    if ((body.prev ?? null) !== prev) problems.push(`Record ${i + 1} does not follow the record before it.`);
    prev = e.hash;
  }
  return { ok: !problems.length, problems };
}

/** Chain plus the published listing photo and original, fetched in full and hashed. */
export async function verifyPhoto(photo: PublicPhoto, fileUrl: (blob: string) => string): Promise<VerifyResult> {
  if (typeof crypto === "undefined" || !crypto.subtle) {
    return { ok: false, entries: photo.chain.length, images: 0, problems: ["This browser can only check records on a secure (https) page."] };
  }
  const chain = await verifyChain(photo.chain);
  const problems = [...chain.problems];
  const last = photo.chain[photo.chain.length - 1];
  let recorded: { final?: { sha256?: string }; original?: { sha256?: string } | null } = {};
  try {
    recorded = last ? JSON.parse(last.body) : {};
  } catch {
    /* reported above */
  }
  let images = 0;
  for (const [name, img, want] of [
    ["listing photo", photo.final, recorded.final?.sha256],
    ["original", photo.original, recorded.original?.sha256],
  ] as const) {
    if (!img) continue;
    if (want && want !== img.sha256) problems.push(`The ${name} listed here is not the one in the record.`);
    try {
      const res = await fetch(fileUrl(img.blob));
      if (!res.ok) throw new Error(String(res.status));
      const got = await sha256Bytes(await res.arrayBuffer());
      images += 1;
      if (got !== img.sha256) problems.push(`The ${name} file has changed since it was published.`);
    } catch {
      problems.push(`The ${name} file could not be downloaded to check it.`);
    }
  }
  return { ok: !problems.length, entries: photo.chain.length, images, problems };
}
