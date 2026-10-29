"""Per-shoot listing records, stored beside shoot.json:

    <shoot>/listing/state.json     published flag, public token, film review
    <shoot>/listing/leads.json     tour requests, newest last
    <shoot>/listing/sessions.json  buyer questions per page visit, so a booking carries the questions asked
    <shoot>/listing/answers.json   Q&A cache

Writes happen under the store's per-shoot lock and replace the file atomically, like shoot.json.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..store import ShootStore, now_iso

MAX_SESSION_QUESTIONS = 20


class ListingState(BaseModel):
    published: bool = False
    # Second half of the public slug "<shoot id>-<token>". Kept when the listing is unpublished, so a printed
    # QR code works again after republishing; unpublished, the slug answers 404.
    token: str | None = None
    published_at: str | None = None
    public_origin: str | None = None  # where the realtor published from; see ListingSettings.public_base_url
    film_approved: bool = False
    note: str = ""
    reviewed_at: str | None = None


class QA(BaseModel):
    question: str
    answer: str
    status: Literal["answered", "follow_up"]
    language: str
    at: str = Field(default_factory=now_iso)


class Notified(BaseModel):
    sent: bool = False
    error: str | None = None  # plain reason; never contains the topic
    at: str | None = None


class Lead(BaseModel):
    id: str = Field(default_factory=lambda: "l_" + uuid.uuid4().hex[:10])
    created_at: str = Field(default_factory=now_iso)
    name: str
    contact: str
    preferred_time: str
    language: str = "en"
    questions: list[QA] = Field(default_factory=list)
    status: Literal["new", "seen"] = "new"
    notified: Notified = Field(default_factory=Notified)


def slug_of(shoot_id: str, state: ListingState) -> str | None:
    return f"{shoot_id}-{state.token}" if state.token else None


def split_slug(slug: str) -> tuple[str, str] | None:
    shoot_id, sep, token = slug.rpartition("-")
    return (shoot_id, token) if sep and shoot_id and token else None


class ListingStore:
    """Reads and writes the files above. Every mutation goes through `_update` under the shoot lock."""

    def __init__(self, store: ShootStore):
        self.store = store

    def _path(self, shoot_id: str, name: str) -> Path:
        return self.store.shoot_dir(shoot_id) / "listing" / name

    def _read(self, shoot_id: str, name: str, default: Any) -> Any:
        path = self._path(shoot_id, name)
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, shoot_id: str, name: str, data: Any) -> None:
        path = self._path(shoot_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _update(self, shoot_id: str, name: str, default: Any, fn) -> Any:
        with self.store.lock(shoot_id):
            data = self._read(shoot_id, name, default)
            out = fn(data)
            self._write(shoot_id, name, data)
            return out

    # ---- state -------------------------------------------------------------------------------------------
    def state(self, shoot_id: str) -> ListingState:
        return ListingState.model_validate(self._read(shoot_id, "state.json", {}))

    def update_state(self, shoot_id: str, fn) -> ListingState:
        def apply(data: dict) -> ListingState:
            st = ListingState.model_validate(data)
            fn(st)
            data.clear()
            data.update(st.model_dump())
            return st

        return self._update(shoot_id, "state.json", {}, apply)

    def publish(self, shoot_id: str, published: bool, origin: str | None) -> ListingState:
        def apply(st: ListingState) -> None:
            if published and not st.token:
                st.token = secrets.token_hex(6)  # hex: never contains the "-" that splits the slug
            if published and not st.published:
                st.published_at = now_iso()
            if origin:
                st.public_origin = origin
            st.published = published

        return self.update_state(shoot_id, apply)

    def resolve(self, slug: str) -> tuple[str, ListingState] | None:
        """Shoot id and state for a public slug, or None when it is unknown or unpublished."""
        parts = split_slug(slug)
        if not parts:
            return None
        shoot_id, token = parts
        if not (self.store.shoot_dir(shoot_id) / "shoot.json").is_file():
            return None
        st = self.state(shoot_id)
        if not (st.published and st.token and secrets.compare_digest(st.token, token)):
            return None
        return shoot_id, st

    # ---- leads -------------------------------------------------------------------------------------------
    def leads(self, shoot_id: str) -> list[Lead]:
        return [Lead.model_validate(x) for x in self._read(shoot_id, "leads.json", [])]

    def add_lead(self, shoot_id: str, lead: Lead) -> Lead:
        self._update(shoot_id, "leads.json", [], lambda data: data.append(lead.model_dump()))
        return lead

    def update_lead(self, shoot_id: str, lead_id: str, fn) -> Lead | None:
        def apply(data: list) -> Lead | None:
            for i, raw in enumerate(data):
                if raw.get("id") == lead_id:
                    lead = Lead.model_validate(raw)
                    fn(lead)
                    data[i] = lead.model_dump()
                    return lead
            return None

        return self._update(shoot_id, "leads.json", [], apply)

    # ---- buyer sessions ------------------------------------------------------------------------------------
    def session_questions(self, shoot_id: str, session: str) -> list[QA]:
        return [QA.model_validate(x) for x in self._read(shoot_id, "sessions.json", {}).get(session, [])]

    def add_session_question(self, shoot_id: str, session: str, qa: QA) -> None:
        def apply(data: dict) -> None:
            rows = data.setdefault(session, [])
            rows.append(qa.model_dump())
            del rows[:-MAX_SESSION_QUESTIONS]

        self._update(shoot_id, "sessions.json", {}, apply)

    # ---- answer cache --------------------------------------------------------------------------------------
    def cached_answer(self, shoot_id: str, key: str) -> dict | None:
        return self._read(shoot_id, "answers.json", {}).get(key)

    def cache_answer(self, shoot_id: str, key: str, value: dict) -> None:
        self._update(shoot_id, "answers.json", {}, lambda data: data.__setitem__(key, value))
