// Film and listing shapes and their HTTP client. Film: GET /api/shoots/{id}/film (film/ on feat/film,
// docs/api.md "Listing film"). Listing: src/shootagent/listing/routes.py (docs/api.md "Listing").

import { ApiError, passcode } from "./client";
import type { Listing } from "./types";

// ---- film -------------------------------------------------------------------------------------------------

export type MotionSource = "generated" | "depth" | "still";

/** Vision judge of a generated clip (film/check.py). */
export interface ClipJudge {
  pass?: boolean;
  changed?: boolean;
  changes?: { object: string; change: string; first_frame?: string | number | null; where?: string | null }[];
  confidence?: string;
  model?: string;
}

export interface FilmAttempt {
  fingerprint?: string | null;
  backend?: string | null;
  cached?: boolean;
  passed: boolean;
  /** why the check failed, or why generation failed */
  reasons: string[];
  judge?: ClipJudge | null;
  /** blob of the contact sheet the check looked at */
  sheet?: string | null;
}

export interface ManifestShot {
  index: number;
  labels: Record<string, string>;
  source_photos: { view: string; version: string | null; blob: string; room: string | null; case?: string | null }[];
  planned_motion: MotionSource;
  motion_source: MotionSource | null;
  fell_back: boolean;
  ai_label: boolean;
  check: { passed: boolean; reasons: string[]; judge: ClipJudge | null } | null;
  attempts: FilmAttempt[];
  note: string;
  /** [start_s, end_s] in film time, per language */
  times: Record<string, [number, number]>;
}

export interface FilmLanguage {
  film: string;
  cover: string | null;
  duration_s: number;
  captions: boolean;
  finisher: "reelengine" | "ffmpeg";
  notes: string[];
}

export interface FilmManifest {
  shoot: string;
  created: string;
  media_mode: string;
  disclosure: { law: string; label: Record<string, string>; rule: string };
  shots: ManifestShot[];
  languages: Record<string, FilmLanguage>;
}

export interface FilmVariant {
  url: string;
  cover: string | null;
  duration_s: number | null;
  captions: boolean | null;
}

export interface FilmState {
  /** film/spec.py FilmSpec; only `languages` is read here */
  spec: { languages: string[] } | null;
  manifest: FilmManifest | null;
  variants: Record<string, FilmVariant>;
}

// ---- listing (passcode side) ----------------------------------------------------------------------------------

export interface ListingState {
  published: boolean;
  token: string | null;
  published_at: string | null;
  public_origin: string | null;
  film_approved: boolean;
  note: string;
  reviewed_at: string | null;
  slug: string | null;
  /** PUBLIC_BASE_URL when the server has one; QR codes and links use it over the browser's origin */
  public_base_url: string | null;
  /** whether NTFY_TOPIC is set */
  notifications: boolean;
  leads: { total: number; new: number };
}

export interface QA {
  question: string;
  answer: string;
  status: "answered" | "follow_up";
  language: string;
  at: string;
}

export interface Lead {
  id: string;
  created_at: string;
  name: string;
  contact: string;
  preferred_time: string;
  language: string;
  questions: QA[];
  status: "new" | "seen";
  notified: { sent: boolean; error: string | null; at: string | null };
  shoot_id: string;
  address: string;
}

export interface ListingCard {
  shoot_id: string;
  title: string;
  address: string | null;
  slug: string | null;
  published: boolean;
  film_approved: boolean;
  note: string;
  film_languages: string[];
  thumb: string | null;
  leads_new: number;
}

export interface Inbox {
  listings: ListingCard[];
  leads: Lead[];
  notifications: boolean;
}

export type DisclosureChange =
  | { kind: "hdr"; exposures: number }
  | { kind: "edit"; op: string; note: string }
  | { kind: "fix"; finding: string; explanation: string };

export interface DisclosurePhoto {
  view: string;
  room: string;
  published: string;
  original: string | null;
  /** fusion: ShootAgent's fusion of the raw exposures; exposure: the camera's middle exposure; delivered: the editor's photo */
  original_kind: "fusion" | "exposure" | "delivered" | "";
  altered: boolean;
  changes: DisclosureChange[];
}

export interface Disclosure {
  address?: string | null;
  law: string;
  photos: DisclosurePhoto[];
  film: {
    ai_clips: { shot: number; label: string; view: string; end_view: string | null }[];
    rejected_clips: { shot: number; label: string; reasons: string[]; changes: NonNullable<ClipJudge["changes"]> }[];
    label: Record<string, string>;
    complete: boolean;
  } | null;
}

// ---- public (buyer page) ----------------------------------------------------------------------------------

export interface PublicShot {
  index: number;
  view: string;
  end_view: string | null;
  labels: Record<string, string>;
  source: MotionSource | null;
  ai_label: boolean;
  times: Record<string, [number, number]>;
}

export interface PublicListing {
  slug: string;
  listing: Listing;
  rooms: { view: string; name: string; photo: string }[];
  film: {
    languages: string[];
    variants: Record<string, { duration_s: number | null; captions: boolean | null; cover: boolean }>;
    shots: PublicShot[];
    disclosure_label: Record<string, string>;
  } | null;
  disclosure: { altered_photos: number; ai_clips: number };
}

export interface AskResult {
  answer: string;
  status: "answered" | "follow_up";
  rooms: string[];
  cached: boolean;
  limited: boolean;
  session: string;
}

export interface BookBody {
  name: string;
  contact: string;
  preferred_time: string;
  language: string;
  session?: string;
}

// ---- client -----------------------------------------------------------------------------------------------------

export interface ListingApi {
  getFilm(shootId: string): Promise<FilmState>;
  makeFilm(shootId: string, languages: string[]): Promise<{ run_id: string }>;
  filmUrl(shootId: string, lang: string): string;
  coverUrl(shootId: string, lang: string): string;
  getState(shootId: string): Promise<ListingState>;
  saveListing(shootId: string, listing: Partial<Listing>): Promise<void>;
  publish(shootId: string, published: boolean): Promise<ListingState>;
  review(shootId: string, body: { film_approved?: boolean; note?: string }): Promise<ListingState>;
  disclosure(shootId: string): Promise<Disclosure>;
  inbox(): Promise<Inbox>;
  markSeen(shootId: string, leadId: string): Promise<void>;
  // public
  publicListing(slug: string): Promise<PublicListing>;
  publicDisclosure(slug: string): Promise<Disclosure>;
  ask(slug: string, question: string, language: string, session?: string): Promise<AskResult>;
  book(slug: string, body: BookBody): Promise<{ lead: string }>;
  publicPhoto(slug: string, blob: string, w?: number): string;
  publicFilm(slug: string, lang: string): string;
  publicCover(slug: string, lang: string): string;
}

async function call<T>(method: string, path: string, body?: unknown, withPasscode = true): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["content-type"] = "application/json";
  const code = passcode.get();
  if (withPasscode && code) headers["x-demo-passcode"] = code;
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "network", "Couldn't reach the server. Check your connection and try again.");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let parsed: { error?: { code?: string; message?: string } } | null = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      /* not JSON: the dev proxy or a dead server */
    }
    if (parsed?.error?.message) throw new ApiError(res.status, parsed.error.code ?? "error", parsed.error.message);
    throw new ApiError(res.status, `http_${res.status}`, `The server answered ${res.status}. Try again in a moment.`);
  }
  return (await res.json()) as T;
}

export function createHttpListingApi(): ListingApi {
  const s = (id: string) => `/api/shoots/${encodeURIComponent(id)}`;
  const p = (slug: string) => `/api/public/listings/${encodeURIComponent(slug)}`;
  return {
    getFilm: (id) => call("GET", `${s(id)}/film`),
    makeFilm: (id, languages) => call("POST", `${s(id)}/film`, { languages }),
    filmUrl: (id, lang) => `${s(id)}/film/${lang}.mp4`,
    coverUrl: (id, lang) => `${s(id)}/film/${lang}/cover.png`,
    getState: (id) => call("GET", `${s(id)}/listing/state`),
    saveListing: async (id, listing) => {
      await call("PATCH", `${s(id)}/listing`, listing);
    },
    publish: (id, published) => call("POST", `${s(id)}/listing/publish`, { published, origin: location.origin }),
    review: (id, body) => call("POST", `${s(id)}/listing/review`, body),
    disclosure: (id) => call("GET", `${s(id)}/listing/disclosure`),
    inbox: () => call("GET", "/api/leads"),
    markSeen: async (id, lead) => {
      await call("POST", `${s(id)}/leads/${encodeURIComponent(lead)}/seen`);
    },
    publicListing: (slug) => call("GET", p(slug), undefined, false),
    publicDisclosure: (slug) => call("GET", `${p(slug)}/disclosure`, undefined, false),
    ask: (slug, question, language, session) =>
      call("POST", `${p(slug)}/ask`, { question, language, ...(session ? { session } : {}) }, false),
    book: (slug, body) => call("POST", `${p(slug)}/book`, body, false),
    publicPhoto: (slug, blob, w) => `${p(slug)}/photos/${encodeURIComponent(blob)}${w ? `?w=${w}` : ""}`,
    publicFilm: (slug, lang) => `${p(slug)}/film/${lang}.mp4`,
    // 1280 px long edge: sharp on a 3x phone at 390 px wide, a fraction of the 1.2 MB PNG
    publicCover: (slug, lang) => `${p(slug)}/film/${lang}/cover.png?w=1280`,
  };
}

/** Public URL of a listing's buyer page. */
export function buyerUrl(state: Pick<ListingState, "slug" | "public_base_url">): string | null {
  if (!state.slug) return null;
  const origin = (state.public_base_url || location.origin).replace(/\/$/, "");
  return `${origin}/#/l/${state.slug}`;
}
