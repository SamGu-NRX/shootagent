"""HTTP routes for listing launch (docs/api.md, "Listing").

Behind the passcode (the app's middleware guards every /api path except /api/public/):
publish state, film review, leads, and a disclosure preview. Public, for the buyer page: the listing, its
photos and film, the disclosure, Q&A and tour booking. Public routes take untrusted input, so each one is
rate-limited per IP, bodies are capped, text is cleaned and length-checked, and Q&A spend has a daily ceiling.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

from ..config import Settings, anthropic_client_kwargs
from ..store import ShootStore
from . import disclosure as disclosure_mod
from . import notify, qa
from .film import film_file, read_film
from .guard import RateLimiter, SpendLedger, clean_text, client_ip
from .settings import ListingSettings
from .state import QA, Lead, ListingState, ListingStore, slug_of
from .views import display_names, listed_views

log = logging.getLogger(__name__)
BLOB_RE = re.compile(r"^[0-9a-f]{8,64}\.[a-z0-9]{2,5}$")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
SESSION_RE = re.compile(r"^[0-9a-f]{24}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
MAX_BODY = 4096
QA_ESTIMATE_USD = 0.03  # upper bound for one uncached question: a cache write of ~7k photo tokens plus the answer
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}

LIMITS = {
    "read": ((240, 60.0),),
    "ask": ((6, 60.0), (40, 3600.0)),
    "book": ((3, 600.0), (8, 86400.0)),
}


class ListingError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def error_response(e: ListingError) -> JSONResponse:
    return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status, headers=e.headers)


class PublishBody(BaseModel):
    published: bool
    origin: str | None = Field(None, max_length=200, description="Origin the realtor is using, e.g. https://host")


class ReviewBody(BaseModel):
    film_approved: bool | None = None
    note: str | None = Field(None, max_length=1000)


class AskBody(BaseModel):
    question: str = Field(max_length=2000)
    language: str = "en"
    session: str | None = None


class BookBody(BaseModel):
    name: str = Field(max_length=500)
    contact: str = Field(max_length=500)
    preferred_time: str = Field(max_length=500)
    language: str = "en"
    session: str | None = None


def build_router(
    store: ShootStore,
    settings: Settings,
    lsettings: ListingSettings | None = None,
    qa_client: Any = None,
    notifier: Callable[..., tuple[bool, str | None]] = notify.send,
    limiter: RateLimiter | None = None,
) -> APIRouter:
    lsettings = lsettings or ListingSettings()
    listings = ListingStore(store)
    limiter = limiter or RateLimiter()
    ledger = SpendLedger(settings.data_dir / "listing" / "qa-spend.json", lsettings.listing_qa_daily_usd)
    photos = qa.PhotoCache()
    client_box: dict[str, Any] = {"client": qa_client}
    router = APIRouter()

    # ---- helpers ----------------------------------------------------------------------------------------
    def load(shoot_id: str):
        if not ID_RE.match(shoot_id):
            raise ListingError(400, "bad_id", "Shoot ids contain only letters, digits, '.', '_' and '-'.")
        try:
            return store.load(shoot_id)
        except KeyError:
            raise ListingError(404, "shoot_not_found", f"No shoot {shoot_id!r}.") from None

    def public(slug: str, request: Request, bucket: str = "read"):
        wait = limiter.allow(bucket, client_ip(request), LIMITS[bucket])
        if wait is not None:
            raise ListingError(429, "rate_limited", f"Too many requests. Try again in {int(wait)} seconds.",
                               {"Retry-After": str(int(wait))})
        found = listings.resolve(slug) if len(slug) <= 120 else None
        if found is None:
            raise ListingError(404, "listing_not_found", "This listing isn't published.")
        shoot_id, state = found
        return store.load(shoot_id), state

    async def body_of(request: Request, model: type[BaseModel]):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise ListingError(413, "too_large", "The request is too large.")
        try:
            return model.model_validate_json(raw or b"{}")
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(x) for x in first["loc"]) or "body"
            raise ListingError(422, "invalid_request", f"Check {field}: {first['msg']}.") from None

    def lang_of(value: str) -> str:
        return "es" if value.lower().startswith("es") else "en"

    def state_json(shoot, state: ListingState) -> dict:
        leads = listings.leads(shoot.id)
        return {
            **state.model_dump(),
            "slug": slug_of(shoot.id, state),
            "public_base_url": lsettings.public_base_url or None,
            "notifications": bool(lsettings.ntfy_topic),
            "leads": {"total": len(leads), "new": sum(1 for x in leads if x.status == "new")},
        }

    def allowed_blobs(shoot) -> set[str]:
        out: set[str] = set()
        for v in listed_views(shoot):
            out.add(v.head_version.blob)
            original, _ = disclosure_mod.original_of(v)
            if original:
                out.add(original)
        return out

    def get_client():
        if client_box["client"] is None:
            import anthropic

            client_box["client"] = anthropic.Anthropic(timeout=60.0, max_retries=2, **anthropic_client_kwargs(settings))
        return client_box["client"]

    # ---- behind the passcode ----------------------------------------------------------------------------
    @router.get("/api/shoots/{shoot_id}/listing/state")
    def get_state(shoot_id: str):
        shoot = load(shoot_id)
        return state_json(shoot, listings.state(shoot_id))

    @router.post("/api/shoots/{shoot_id}/listing/publish")
    def publish(shoot_id: str, body: PublishBody):
        shoot = load(shoot_id)
        if body.published:
            if not (shoot.listing.address or "").strip():
                raise ListingError(400, "needs_address", "Add the street address first; the buyer page leads with it.")
            if not any(v.head for v in shoot.views):
                raise ListingError(400, "no_photos", "This shoot has no photos to publish.")
        origin = body.origin if body.origin and re.match(r"^https?://[^/\s]+$", body.origin) else None
        return state_json(shoot, listings.publish(shoot_id, body.published, origin))

    @router.post("/api/shoots/{shoot_id}/listing/review")
    def review(shoot_id: str, body: ReviewBody):
        from ..store import now_iso

        shoot = load(shoot_id)

        def apply(st: ListingState) -> None:
            if body.film_approved is not None:
                st.film_approved = body.film_approved
            if body.note is not None:
                st.note = body.note.strip()
            st.reviewed_at = now_iso()

        return state_json(shoot, listings.update_state(shoot_id, apply))

    @router.get("/api/shoots/{shoot_id}/listing/disclosure")
    def private_disclosure(shoot_id: str):
        return disclosure_mod.build(load(shoot_id), store)

    @router.get("/api/leads")
    def all_leads():
        rows, cards = [], []
        for sid in store.list_ids():
            try:
                shoot = store.load(sid)
            except Exception:  # a half-written or foreign folder must not break the inbox
                continue
            state = listings.state(sid)
            leads = listings.leads(sid)
            if not (state.token or leads):
                continue
            film = read_film(store, sid)
            cards.append({
                "shoot_id": sid,
                "title": shoot.title,
                "address": shoot.listing.address,
                "slug": slug_of(sid, state),
                "published": state.published,
                "film_approved": state.film_approved,
                "note": state.note,
                "film_languages": film["languages"] if film else [],
                "thumb": next((v.head_version.blob for v in listed_views(shoot)), None),
                "leads_new": sum(1 for x in leads if x.status == "new"),
            })
            rows += [{**x.model_dump(), "shoot_id": sid, "address": shoot.listing.address or shoot.title} for x in leads]
        rows.sort(key=lambda x: x["created_at"], reverse=True)
        return {"listings": cards, "leads": rows, "notifications": bool(lsettings.ntfy_topic)}

    @router.post("/api/shoots/{shoot_id}/leads/{lead_id}/seen")
    def lead_seen(shoot_id: str, lead_id: str):
        load(shoot_id)
        lead = listings.update_lead(shoot_id, lead_id, lambda x: setattr(x, "status", "seen"))
        if lead is None:
            raise ListingError(404, "lead_not_found", f"No lead {lead_id!r} on this listing.")
        return lead.model_dump()

    # ---- public -----------------------------------------------------------------------------------------
    @router.get("/api/public/listings/{slug}")
    def public_listing(slug: str, request: Request):
        shoot, state = public(slug, request)
        film = read_film(store, shoot.id)
        dis = disclosure_mod.build(shoot, store)
        views = listed_views(shoot)
        names = display_names(views)
        return {
            "slug": slug,
            "listing": shoot.listing.model_dump(),
            "rooms": [
                {"view": v.id, "name": names[v.id], "photo": v.head_version.blob} for v in views
            ],
            "film": None if not film or not film["variants"] else {
                "languages": film["languages"],
                "variants": film["variants"],
                "shots": [
                    {k: s[k] for k in ("index", "view", "end_view", "labels", "source", "ai_label", "times")}
                    for s in film["shots"]
                ],
                "disclosure_label": film["disclosure_label"],
            },
            "disclosure": {
                "altered_photos": sum(1 for p in dis["photos"] if p["altered"]),
                "ai_clips": len(dis["film"]["ai_clips"]) if dis["film"] else 0,
            },
        }

    @router.get("/api/public/listings/{slug}/photos/{blob}")
    def public_photo(slug: str, blob: str, request: Request, w: int | None = None):
        from ..api.app import _resized

        shoot, _ = public(slug, request)
        if not BLOB_RE.match(blob) or blob not in allowed_blobs(shoot):
            raise ListingError(404, "photo_not_found", "No such photo on this listing.")
        path = store.blob_path(shoot.id, blob)
        if w is not None:
            path = _resized(store.shoot_dir(shoot.id) / "cache", path, max(64, min(2048, w)))
        return FileResponse(path, headers=IMMUTABLE)

    @router.get("/api/public/listings/{slug}/film/{lang}.mp4")
    def public_film(slug: str, lang: str, request: Request):
        shoot, _ = public(slug, request)
        path = film_file(store, shoot.id, lang, "film.mp4")
        if path is None or not path.is_file():
            raise ListingError(404, "no_film", f"No {lang} film for this listing.")
        return FileResponse(path, media_type="video/mp4")

    @router.get("/api/public/listings/{slug}/film/{lang}/cover.png")
    def public_cover(slug: str, lang: str, request: Request, w: int | None = None):
        """The film's cover. It is the buyer page's first image (the video poster) and a 1080x1920 PNG of about
        1.2 MB, so `?w=` serves a cached JPEG at that long edge instead."""
        from ..api.app import _resized

        shoot, _ = public(slug, request)
        path = film_file(store, shoot.id, lang, "cover.png")
        if path is None or not path.is_file():
            raise ListingError(404, "no_cover", f"No {lang} cover for this listing.")
        if w is None:
            return FileResponse(path, media_type="image/png")
        cache = store.shoot_dir(shoot.id) / "cache" / f"cover-{lang}"  # _resized names copies by stem: one dir per language
        out = _resized(cache, path, max(320, min(1920, w)))
        return FileResponse(out, media_type="image/jpeg" if out.suffix == ".jpg" else "image/png")

    @router.get("/api/public/listings/{slug}/disclosure")
    def public_disclosure(slug: str, request: Request):
        shoot, _ = public(slug, request)
        return {"address": shoot.listing.address, **disclosure_mod.build(shoot, store)}

    @router.post("/api/public/listings/{slug}/ask")
    async def ask(slug: str, request: Request):
        shoot, _ = public(slug, request, "ask")
        body = await body_of(request, AskBody)
        language = lang_of(body.language)
        try:
            question = clean_text(body.question, qa.MAX_QUESTION)
        except ValueError as e:
            raise ListingError(422, "question_too_long", str(e)) from None
        if len(question) < 3:
            raise ListingError(422, "question_empty", "Type a question about the home.")
        session = body.session if body.session and SESSION_RE.match(body.session) else secrets.token_hex(12)
        key = qa.cache_key(qa.context_fingerprint(shoot), language, question)
        hit = listings.cached_answer(shoot.id, key)
        limited = False
        if hit:
            answer = qa.Answer(hit["status"], hit["text"], hit.get("rooms", []), cached=True)
        elif not ledger.can_spend(QA_ESTIMATE_USD):
            limited = True
            answer = qa.Answer("follow_up", "", [])
        else:
            answer = await asyncio.to_thread(run_qa, shoot, question, language)
            if answer.usd:
                ledger.add(answer.usd)
            if answer.usd or answer.status == "answered":  # a model answer, not an outage message
                listings.cache_answer(shoot.id, key, {"status": answer.status, "text": answer.text, "rooms": answer.rooms})
        listings.add_session_question(
            shoot.id, session, QA(question=question, answer=answer.text, status=answer.status, language=language)
        )
        return {"answer": answer.for_buyer(language), "status": answer.status, "rooms": answer.rooms,
                "cached": answer.cached, "limited": limited, "session": session}

    def run_qa(shoot, question: str, language: str) -> qa.Answer:
        model = lsettings.listing_qa_model
        try:
            client = get_client()
            req = qa.build_request(store, shoot, question, language, model, lsettings.listing_qa_photo_px, photos)
            resp = client.messages.create(**req)
        except Exception as e:  # any model or credential failure becomes the follow-up answer
            log.warning("listing Q&A unavailable for %s: %s", shoot.id, type(e).__name__)
            return qa.unavailable(language)
        return qa.parse(resp, model, language)

    @router.post("/api/public/listings/{slug}/book", status_code=201)
    async def book(slug: str, request: Request, background: BackgroundTasks):
        shoot, state = public(slug, request, "book")
        body = await body_of(request, BookBody)
        try:
            name = clean_text(body.name, 80)
            contact = clean_text(body.contact, 120)
            when = clean_text(body.preferred_time, 120)
        except ValueError as e:
            raise ListingError(422, "too_long", str(e)) from None
        if not name:
            raise ListingError(422, "name_missing", "Add your name.")
        if not (EMAIL_RE.match(contact) or len(re.sub(r"\D", "", contact)) >= 7):
            raise ListingError(422, "contact_invalid", "Add an email address or a phone number.")
        if not when:
            raise ListingError(422, "time_missing", "Add a time that works for you.")
        session = body.session if body.session and SESSION_RE.match(body.session) else None
        lead = Lead(name=name, contact=contact, preferred_time=when, language=lang_of(body.language),
                    questions=listings.session_questions(shoot.id, session) if session else [])
        listings.add_lead(shoot.id, lead)
        topic = lsettings.ntfy_topic.strip()
        if topic:
            origin = (lsettings.public_base_url or state.public_origin or str(request.base_url)).rstrip("/")
            click = f"{origin}/#/r/{shoot.id}/{lead.id}"
            title, text = notify.lead_message(lead, shoot.listing.address or shoot.title)
            background.add_task(deliver, shoot.id, lead.id, topic, title, text, click)
        return {"lead": lead.id, "notifying": bool(topic)}

    def deliver(shoot_id: str, lead_id: str, topic: str, title: str, text: str, click: str) -> None:
        from ..store import now_iso

        sent, error = notifier(topic, title, text, click, server=lsettings.ntfy_server)

        def apply(lead: Lead) -> None:
            lead.notified.sent, lead.notified.error, lead.notified.at = sent, error, now_iso()

        listings.update_lead(shoot_id, lead_id, apply)

    return router
