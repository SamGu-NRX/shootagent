// In-browser stand-in for the film and listing endpoints, for ?api=mock. The film fixture follows the
// manifest of run 20261028-185544-ddefff in shape (and in its rejected-clip reasons), on the mock photos.
// Demo data has no rendered film, so video URLs are empty and players show the cover photo.

import { ApiError } from "../client";
import type {
  AskResult,
  Disclosure,
  FilmState,
  Inbox,
  Lead,
  ListingApi,
  ListingState,
  ManifestShot,
  PublicListing,
} from "../listing";
import type { Listing } from "../types";
import { makeShoot, VIEW_PLAN } from "./data";

export const MOCK_SLUG = "demo-listing";

const caseOf = (view: string) => VIEW_PLAN.find((v) => v.id === view)!.case;
const photo = (view: string) => `${caseOf(view)}_finished.jpg`;

interface PlanRow {
  view: string;
  en: string;
  es: string;
  planned: ManifestShot["planned_motion"];
  source: ManifestShot["motion_source"];
  en_s: number;
  es_s: number;
  attempts?: ManifestShot["attempts"];
}

const PLAN: PlanRow[] = [
  { view: "v05", en: "Garage and drive", es: "Cochera y entrada", planned: "depth", source: "depth", en_s: 3.8, es_s: 4.6 },
  {
    view: "v07", en: "Great room", es: "Sala principal", planned: "generated", source: "generated", en_s: 6.2, es_s: 7.1,
    attempts: [{ passed: true, reasons: [], judge: { pass: true, changed: false, changes: [], confidence: "high" } }],
  },
  {
    view: "v02", en: "Kitchen", es: "Cocina", planned: "generated", source: "depth", en_s: 5.4, es_s: 6.3,
    attempts: [
      {
        passed: false,
        reasons: [
          "motion no rigid camera explains: 7.1 px from t=5.75s in cell [0.8, 0.811, 0.867, 0.913]",
          "structure not in the first frame: 3.49% of the frame from t=2.0s at [0.382, 0.0, 0.641, 0.334]",
          "a straight edge bends by 98.04 px from t=5.75s at [-0.038, 0.608, 0.83, 0.947]",
        ],
        judge: {
          pass: false,
          changed: true,
          changes: [{ object: "pendant lamp cluster (large funnel-shaped pendant lights)", change: "appeared", first_frame: "t=1.2s", where: "top center-left, above the island" }],
          confidence: "high",
        },
      },
      { passed: false, reasons: ["no recorded clip for this request (fingerprint 87cc7b7c). Replay serves only what was recorded."] },
    ],
  },
  { view: "v01", en: "Living room", es: "Sala", planned: "depth", source: "depth", en_s: 5.0, es_s: 5.9 },
  { view: "v06", en: "Primary bath", es: "Baño principal", planned: "still", source: "still", en_s: 4.1, es_s: 4.4 },
  { view: "v03", en: "Nursery", es: "Cuarto del bebé", planned: "depth", source: "depth", en_s: 4.6, es_s: 5.2 },
];

function filmFixture(): FilmState {
  let en = 0;
  let es = 0;
  const shots: ManifestShot[] = PLAN.map((p, index) => {
    const times = { en: [en, en + p.en_s] as [number, number], es: [es, es + p.es_s] as [number, number] };
    en += p.en_s;
    es += p.es_s;
    const attempts = p.attempts ?? [];
    const last = attempts[attempts.length - 1];
    return {
      index,
      labels: { en: p.en, es: p.es },
      source_photos: [{ view: p.view, version: `${p.view}.0`, blob: photo(p.view), room: null }],
      planned_motion: p.planned,
      motion_source: p.source,
      fell_back: p.planned !== p.source,
      ai_label: p.source === "generated",
      check: last ? { passed: last.passed, reasons: last.reasons, judge: last.judge ?? null } : null,
      attempts,
      note: "",
      times,
    };
  });
  const variant = (lang: "en" | "es", d: number) => ({ film: `${lang}/film.mp4`, cover: null, duration_s: d, captions: true, finisher: "reelengine" as const, notes: [] });
  return {
    spec: { languages: ["en", "es"] },
    manifest: {
      shoot: "mock",
      created: new Date().toISOString(),
      media_mode: "replay",
      disclosure: { law: "California AB 723", label: { en: "AI-generated motion", es: "Movimiento generado por IA" }, rule: "" },
      shots,
      languages: { en: variant("en", en), es: variant("es", es) },
    },
    variants: {
      en: { url: "", cover: null, duration_s: en, captions: true },
      es: { url: "", cover: null, duration_s: es, captions: true },
    },
  };
}

const DEMO_LISTING: Listing = {
  address: "412 Maple Court, Austin, TX",
  price: "$685,000",
  beds: 3,
  baths: 2,
  sqft: 1840,
  headline: "Bright single-story with a great room and a two-car garage",
};

const CANNED: [RegExp, string, string][] = [
  [/garage|cochera|park/i, "The garage and drive photo shows a two-car garage with a paved driveway.", "La foto de la cochera muestra una cochera para dos autos con entrada pavimentada."],
  [/kitchen|cocina|island|isla/i, "The kitchen photo shows an island with pendant lights and white cabinets.", "La foto de la cocina muestra una isla con lámparas colgantes y gabinetes blancos."],
  [/bath|tub|tina|baño/i, "The primary bath photo shows a double vanity and a walk-in shower.", "La foto del baño principal muestra un lavabo doble y una regadera."],
  [/bed|recámara|cuarto/i, "The listing says 3 bedrooms; the photos show the nursery and the living spaces.", "El anuncio dice 3 recámaras; las fotos muestran el cuarto del bebé y las áreas comunes."],
];
const FOLLOW = {
  en: "The photos don't show that. I've noted your question for the listing agent. Book a tour so they can get back to you.",
  es: "Las fotos no lo muestran. Anoté tu pregunta para el agente. Agenda una visita para que pueda responderte.",
};

const wait = <T>(v: T, ms = 150) => new Promise<T>((r) => setTimeout(() => r(structuredClone(v)), ms));

export function createMockListingApi(): ListingApi {
  const shoot = makeShoot("demo-listing", DEMO_LISTING.address!);
  shoot.listing = { ...DEMO_LISTING };
  const film = filmFixture();
  const states = new Map<string, ListingState>();
  const listings = new Map<string, Partial<Listing>>();
  const leads: Lead[] = [];
  const sessions = new Map<string, AskResult[]>();
  const questions = new Map<string, { question: string; answer: string; status: AskResult["status"]; language: string; at: string }[]>();

  const state = (id: string): ListingState => {
    const s = states.get(id) ?? {
      published: false, token: null, published_at: null, public_origin: null, film_approved: false, note: "",
      reviewed_at: null, slug: null, public_base_url: null, notifications: true, leads: { total: 0, new: 0 },
    };
    s.leads = { total: leads.length, new: leads.filter((l) => l.status === "new").length };
    states.set(id, s);
    return s;
  };

  return {
    getFilm: async () => wait(film),
    makeFilm: async () => {
      throw new ApiError(501, "mock_film", "Demo data has a film already. Making a new one needs the live server.");
    },
    filmUrl: () => "",
    coverUrl: () => `/mock/${photo(PLAN[0]!.view)}`,
    getState: async (id) => wait(state(id)),
    saveListing: async (id, listing) => {
      listings.set(id, { ...listings.get(id), ...listing });
      Object.assign(shoot.listing, listing);
      await wait(null, 200);
    },
    publish: async (id, published) => {
      const s = state(id);
      s.published = published;
      s.token ??= "mock";
      s.slug = MOCK_SLUG;
      s.published_at ??= new Date().toISOString();
      return wait(s, 350);
    },
    review: async (id, body) => {
      const s = state(id);
      if (body.film_approved !== undefined) s.film_approved = body.film_approved;
      if (body.note !== undefined) s.note = body.note;
      s.reviewed_at = new Date().toISOString();
      return wait(s);
    },
    disclosure: async () => wait(disclosureFixture(shoot.views.map((v) => v.id))),
    inbox: async (): Promise<Inbox> => {
      const cards = [...states.entries()]
        .filter(([, s]) => s.token)
        .map(([id, s]) => ({
          shoot_id: id, title: shoot.title, address: shoot.listing.address, slug: s.slug, published: s.published,
          film_approved: s.film_approved, note: s.note, film_languages: ["en", "es"], thumb: photo("v07"),
          leads_new: leads.filter((l) => l.status === "new").length,
        }));
      return wait({ listings: cards, leads: [...leads].reverse(), notifications: true });
    },
    markSeen: async (_id, leadId) => {
      const lead = leads.find((l) => l.id === leadId);
      if (lead) lead.status = "seen";
      await wait(null, 80);
    },
    publicListing: async (slug): Promise<PublicListing> => {
      if (slug !== MOCK_SLUG) throw new ApiError(404, "listing_not_found", "This listing isn't published.");
      return wait({
        slug,
        listing: shoot.listing,
        rooms: shoot.views.map((v) => ({ view: v.id, name: v.label, photo: photo(v.id) })),
        film: {
          languages: ["en", "es"],
          variants: { en: { duration_s: film.variants.en!.duration_s, captions: true, cover: false }, es: { duration_s: film.variants.es!.duration_s, captions: true, cover: false } },
          shots: film.manifest!.shots.map((s) => ({
            index: s.index, view: s.source_photos[0]!.view, end_view: null, labels: s.labels, source: s.motion_source, ai_label: s.ai_label, times: s.times,
          })),
          disclosure_label: film.manifest!.disclosure.label,
        },
        disclosure: { altered_photos: shoot.views.length, ai_clips: 1 },
      });
    },
    publicDisclosure: async () => wait({ address: shoot.listing.address, ...disclosureFixture(shoot.views.map((v) => v.id)) }),
    ask: async (_slug, question, language, session) => {
      const lang = language === "es" ? "es" : "en";
      const hit = CANNED.find(([re]) => re.test(question));
      const res: AskResult = {
        answer: hit ? (lang === "es" ? hit[2] : hit[1]) : FOLLOW[lang],
        status: hit ? "answered" : "follow_up",
        rooms: [],
        cached: false,
        limited: false,
        session: session ?? Math.random().toString(16).slice(2).padEnd(24, "0").slice(0, 24),
      };
      sessions.set(res.session, [...(sessions.get(res.session) ?? []), res]);
      questions.set(res.session, [...(questions.get(res.session) ?? []), { question, answer: res.answer, status: res.status, language: lang, at: new Date().toISOString() }]);
      return wait(res, 900);
    },
    book: async (_slug, body) => {
      const lead: Lead = {
        id: "l_" + Math.random().toString(16).slice(2, 12),
        created_at: new Date().toISOString(),
        name: body.name,
        contact: body.contact,
        preferred_time: body.preferred_time,
        language: body.language,
        questions: body.session ? (questions.get(body.session) ?? []) : [],
        status: "new",
        notified: { sent: true, error: null, at: new Date().toISOString() },
        shoot_id: [...states.keys()][0] ?? "demo",
        address: shoot.listing.address ?? shoot.title,
      };
      leads.push(lead);
      return wait({ lead: lead.id }, 500);
    },
    publicPhoto: (_slug, blob, w) => (w && w <= 400 ? `/mock/${blob.replace(".jpg", "_w360.jpg")}` : `/mock/${blob}`),
    publicFilm: () => "",
    publicCover: () => `/mock/${photo(PLAN[0]!.view)}`,
  };
}

function disclosureFixture(views: string[]): Disclosure {
  const order = [...PLAN.map((p) => p.view), ...views.filter((v) => !PLAN.some((p) => p.view === v))];
  return {
    law: "California AB 723",
    photos: order.map((view) => {
      const plan = VIEW_PLAN.find((v) => v.id === view)!;
      return {
        view,
        room: plan.label,
        published: photo(view),
        original: `${plan.case}_bracket1.jpg`,
        original_kind: "exposure" as const,
        altered: true,
        changes: [
          { kind: "hdr" as const, exposures: 3 },
          ...(view === "v01" ? [{ kind: "edit" as const, op: "restore_from_brackets", note: "the floor lamp the edit had erased" }] : []),
        ],
      };
    }),
    film: {
      ai_clips: [{ shot: 2, label: "Great room", view: "v07", end_view: null }],
      rejected_clips: [{ shot: 3, label: "Kitchen", reasons: PLAN[2]!.attempts![0]!.reasons, changes: PLAN[2]!.attempts![0]!.judge!.changes! }],
      label: { en: "AI-generated motion", es: "Movimiento generado por IA" },
      complete: true,
    },
  };
}
