"""Delivery routes: disclosures and publication, the client review link, the public disclosure page and the
listing kit (docs/api.md, "Delivery"). Wiring only; the logic lives in compliance/.

Photographer routes sit under /api/shoots/{id} behind the passcode. Two families carry their own token instead:
/api/public/{token} (the read-only disclosure page buyers reach from a QR code) and /api/review/{token} (the
client review link, which can approve photos and start agent runs within the link's request budget).
"""

from __future__ import annotations

import asyncio
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Callable, Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..compliance.disclosure import ComplianceReport, build_report
from ..compliance.fairhousing import lint_dict
from ..compliance.kit import build_kit, latest_kit
from ..compliance.policy import classify_request
from ..compliance.publish import listing_views, publish, published_blobs
from ..config import Settings, anthropic_client_kwargs
from ..store import ClientReview, Comment, Publication, Share, Shoot, ShootStore
from ..trace import read_events
from .app import BLOB_RE, ID_RE, IMMUTABLE, ApiError, _resized


@dataclass
class Ctx:
    settings: Settings
    store: ShootStore
    manager: Any
    client: Any
    load: Callable[[str], Shoot]
    view_of: Callable
    dump: Callable
    start_trace: Callable
    run_stream: Callable


class ShareBody(BaseModel):
    label: str = Field("", max_length=80)


class KitBody(BaseModel):
    include_reel: bool = True


class TextBody(BaseModel):
    text: str = Field(max_length=8000)


class VerdictBody(BaseModel):
    status: Literal["approved", "changes_requested"] | None


class CommentBody(BaseModel):
    view: str
    text: str = Field(min_length=1, max_length=1000)
    point: list[float] | None = Field(None, description="[x, y] normalised, where the client tapped.")


def publication_summary(shoot: Shoot, pub: Publication | None) -> dict | None:
    if pub is None:
        return None
    return {
        "token": pub.token,
        "version": pub.version,
        "created_at": pub.created_at,
        "root": pub.root,
        "photos": [
            {
                "view": p.view,
                "label": p.label,
                "order": p.order,
                "status": p.status,
                "disclosure": p.disclosure,
                "head": p.head,
                "final": p.final.model_dump(),
                "original": p.original.model_dump() if p.original else None,
                "original_kind": p.original_kind,
            }
            for p in pub.photos
        ],
    }


def register(app: FastAPI, ctx: Ctx) -> None:
    store = ctx.store
    tokens: dict[str, str] = {}  # token -> shoot id, filled by scanning shoots on a miss
    tokens_lock = threading.Lock()

    def base_url(request: Request) -> str:
        return (ctx.settings.public_base_url or str(request.base_url)).rstrip("/")

    def agent_available() -> bool:
        if ctx.client is not None:
            return True
        try:
            anthropic_client_kwargs(ctx.settings)
            return True
        except RuntimeError:
            return False

    def shoot_for(token: str, kind: Literal["public", "client"]) -> tuple[Shoot, Share | None]:
        if not ID_RE.match(token):
            raise ApiError(404, "link_not_found", "This link is not valid. Ask the photographer for a new one.")

        def match(s: Shoot):
            if kind == "public":
                return (s, None) if s.public_token == token and s.publication is not None else None
            share = s.share(token)
            return (s, share) if share else None

        sid = tokens.get(token)
        if sid:
            try:
                found = match(store.load(sid))
                if found:
                    return found
            except KeyError:
                pass
        with tokens_lock:
            for sid in store.list_ids():
                try:
                    s = store.load(sid)
                except Exception:
                    continue
                for t in [s.public_token, *(x.token for x in s.shares)]:
                    if t:
                        tokens[t] = sid
                found = match(s)
                if found:
                    return found
        if kind == "public":
            raise ApiError(404, "link_not_found", "No published listing at this link. It may not be published yet.")
        raise ApiError(404, "link_not_found", "This review link is not valid or was turned off. Ask the photographer.")

    def stale_of(shoot: Shoot, report: ComplianceReport) -> bool:
        if shoot.publication is None:
            return True
        return {p.view: p.head for p in listing_views(shoot, report.photos)} != shoot.publication.heads

    # ---- comments ---------------------------------------------------------------------------------------
    def comment_view(shoot: Shoot, c: Comment) -> dict:
        state, reason, message, result_version = c.status, c.note, "", None
        steps = 0
        if c.status not in ("declined", "undone") and c.run:
            events = read_events(store.shoot_dir(shoot.id) / "runs" / f"{c.run}.jsonl")
            handle = ctx.manager.get(c.run)
            end = next((e for e in reversed(events) if e["kind"] == "run_end"), None)
            steps = sum(1 for e in events if e["kind"] == "tool_call")
            msgs = [e["data"].get("text", "") for e in events if e["kind"] == "message"]
            message = msgs[-1] if msgs else ""
            if end is None:
                state = "queued" if handle is not None and handle.status == "queued" else (
                    "working" if handle is not None else "stopped")
                reason = "" if handle is not None else "The edit stopped when the server restarted."
            else:
                results = [r for e in events if e["kind"] == "view_result" and e.get("view") == c.view
                           for r in e["data"].get("results", [])]
                statuses = {r.get("status") for r in results}
                if "applied" in statuses:
                    state = "applied"
                elif statuses and statuses <= {"declined"}:
                    state = "declined"
                elif "failed" in statuses or not end["data"].get("ok"):
                    state = "failed"
                elif statuses and statuses <= {"not_needed", "declined"}:
                    state = "not_needed"
                else:
                    state = "done"
                reason = "; ".join(r.get("reason", "") for r in results if r.get("reason")) or end["data"].get("summary", "")
                passed = [e for e in events if e["kind"] == "check" and e.get("view") == c.view and e["data"].get("passed")]
                result_version = passed[-1]["data"].get("version") if passed and state == "applied" else None
        try:
            view = shoot.view(c.view)
            blob = next((v.blob for v in view.versions if v.id == result_version), None) if result_version else None
            current = view.head
        except KeyError:
            blob, current = None, None
        return {
            **c.model_dump(mode="json"),
            "state": state,
            "reason": reason,
            "message": message,
            "steps": steps,
            "result_version": result_version,
            "result_blob": blob,
            "current": current,
        }

    def point_mask(shoot: Shoot, view_id: str, point: list[float]) -> str | None:
        """SAM mask of what the client tapped, or None when segmentation is unavailable or finds nothing."""
        from ..tools import _ops

        try:
            view = shoot.view(view_id)
            img = store.get_image(shoot.id, view.head_version.blob)
            m = _ops.fit_mask(_ops.segment_points(img, [[point[0], point[1], 1]], None), img.shape[:2])
            if not m.any() or m.mean() > 0.6:
                return None
            return store.add_mask(shoot.id, view_id, m, source="client").id
        except Exception:
            return None

    # ---- photographer: disclosures, publication, shares, kit ----------------------------------------------
    @app.get("/api/shoots/{shoot_id}/compliance")
    def compliance(shoot_id: str):
        ctx.load(shoot_id)
        report = build_report(store, shoot_id)
        shoot = store.load(shoot_id)
        listing = [p.view for p in listing_views(shoot, report.photos)]
        return {
            **report.model_dump(mode="json"),
            "listing": listing,
            "stale": stale_of(shoot, report),
            "publication": publication_summary(shoot, shoot.publication),
            "public_token": shoot.public_token,
        }

    @app.post("/api/shoots/{shoot_id}/publish")
    async def do_publish(shoot_id: str):
        ctx.load(shoot_id)
        try:
            pub = await asyncio.to_thread(publish, store, shoot_id)
        except ValueError as e:
            raise ApiError(400, "nothing_to_publish", str(e)) from None
        shoot = store.load(shoot_id)
        tokens[pub.token] = shoot_id
        return publication_summary(shoot, pub)

    @app.post("/api/shoots/{shoot_id}/shares")
    def create_share(shoot_id: str, body: ShareBody | None = None):
        ctx.load(shoot_id)
        share = Share(token=secrets.token_urlsafe(12), label=(body.label if body else "").strip())
        store.mutate(shoot_id, lambda s: s.shares.append(share))
        tokens[share.token] = shoot_id
        return share.model_dump(mode="json")

    @app.delete("/api/shoots/{shoot_id}/shares/{token}")
    def revoke_share(shoot_id: str, token: str):
        shoot = ctx.load(shoot_id)
        if not any(s.token == token for s in shoot.shares):
            raise ApiError(404, "share_not_found", f"Shoot {shoot_id} has no review link {token!r}.")

        def apply(s: Shoot):
            for sh in s.shares:
                if sh.token == token:
                    sh.revoked = True

        store.mutate(shoot_id, apply)
        return {"ok": True}

    @app.get("/api/shoots/{shoot_id}/comments")
    def list_comments(shoot_id: str):
        shoot = ctx.load(shoot_id)
        return [comment_view(shoot, c) for c in shoot.comments]

    @app.post("/api/shoots/{shoot_id}/kit")
    async def make_kit(shoot_id: str, request: Request, body: KitBody | None = None):
        ctx.load(shoot_id)
        try:
            info = await asyncio.to_thread(build_kit, store, shoot_id, base_url(request),
                                           body.include_reel if body else True)
        except ValueError as e:
            raise ApiError(400, "nothing_to_publish", str(e)) from None
        info.pop("path", None)
        shoot = store.load(shoot_id)
        if shoot.public_token:
            tokens[shoot.public_token] = shoot_id
        return {**info, "url": f"/api/shoots/{shoot_id}/kit/latest.zip"}

    @app.get("/api/shoots/{shoot_id}/kit")
    def kit_info(shoot_id: str):
        ctx.load(shoot_id)
        info = latest_kit(store, shoot_id)
        if info is None:
            raise ApiError(404, "no_kit", "No listing kit has been built for this shoot yet.")
        return {**info, "url": f"/api/shoots/{shoot_id}/kit/latest.zip"}

    @app.get("/api/shoots/{shoot_id}/kit/latest.zip")
    def kit_zip(shoot_id: str):
        ctx.load(shoot_id)
        info = latest_kit(store, shoot_id)
        if info is None:
            raise ApiError(404, "no_kit", "No listing kit has been built for this shoot yet.")
        return FileResponse(store.shoot_dir(shoot_id) / "kit" / "latest.zip", media_type="application/zip",
                            filename=info["name"])

    @app.post("/api/lint/listing")
    def lint_listing(body: TextBody):
        return lint_dict(body.text)

    @app.post("/api/lint/request")
    def lint_request(body: TextBody):
        return classify_request(body.text).to_dict()

    # ---- public disclosure page ---------------------------------------------------------------------------
    @app.get("/api/public/{token}")
    def public_page(token: str):
        shoot, _ = shoot_for(token, "public")
        pub = shoot.publication
        assert pub is not None
        public_kinds = {"alteration", "adjustment", "restoration", "overlay", "possible_alteration"}
        return {
            "token": token,
            "title": shoot.listing.address or shoot.title,
            "listing": shoot.listing.model_dump(mode="json", exclude={"description"}),
            "version": pub.version,
            "published_at": pub.created_at,
            "root": pub.root,
            "photos": [
                {
                    **p.model_dump(mode="json", exclude={"changes"}),
                    "changes": [
                        {"kind": c.kind, "what": c.what, "by": c.by, "category": c.category}
                        for c in p.changes if c.kind in public_kinds
                    ],
                }
                for p in pub.photos
            ],
        }

    @app.get("/api/public/{token}/blobs/{blob}")
    def public_blob(token: str, blob: str, w: int | None = None):
        shoot, _ = shoot_for(token, "public")
        assert shoot.publication is not None
        if not BLOB_RE.match(blob) or blob not in published_blobs(shoot.publication):
            raise ApiError(404, "blob_not_found", "That file is not part of this publication.")
        path = store.blob_path(shoot.id, blob)
        if w is not None:
            path = _resized(store.shoot_dir(shoot.id) / "cache", path, max(16, min(4096, w)))
        return FileResponse(path, headers=IMMUTABLE)

    # ---- client review link -------------------------------------------------------------------------------
    def review_payload(shoot: Shoot, share: Share) -> dict:
        report = build_report(store, shoot.id)
        by_view = {p.view: p for p in report.photos}
        used = sum(1 for c in shoot.comments if c.share == share.token and c.run)
        views = []
        for v in shoot.views:
            rep = by_view.get(v.id)
            if not v.head or rep is None or not rep.deliver:
                continue
            views.append({
                "id": v.id,
                "label": v.label or v.id,
                "room": v.room,
                "blob": v.head_version.blob,
                "original_blob": v.versions[0].blob,
                "edited": v.head != v.versions[0].id,
                "head": v.head,
                "approved": v.approved,
                "client": v.client.model_dump(mode="json") if v.client else None,
                "status": rep.status,
                "disclosure": rep.disclosure,
            })
        return {
            "token": share.token,
            "label": share.label,
            "title": shoot.listing.address or shoot.title,
            "listing": shoot.listing.model_dump(mode="json", exclude={"description"}),
            "requests_left": max(0, share.max_requests - used),
            "agent_available": agent_available(),
            "public_token": shoot.public_token if shoot.publication else None,
            "views": views,
            "comments": [comment_view(shoot, c) for c in shoot.comments if c.share == share.token],
        }

    @app.get("/api/review/{token}")
    def review(token: str):
        shoot, share = shoot_for(token, "client")
        assert share is not None
        return review_payload(shoot, share)

    @app.get("/api/review/{token}/blobs/{blob}")
    def review_blob(token: str, blob: str, w: int | None = None):
        shoot, _ = shoot_for(token, "client")
        if not BLOB_RE.match(blob):
            raise ApiError(400, "bad_blob", "Blob names are a content hash plus an extension.")
        path = store.blob_path(shoot.id, blob)
        if not path.exists():
            raise ApiError(404, "blob_not_found", "No such file in this shoot.")
        if w is not None:
            path = _resized(store.shoot_dir(shoot.id) / "cache", path, max(16, min(4096, w)))
        return FileResponse(path, headers=IMMUTABLE)

    @app.post("/api/review/{token}/views/{view_id}/verdict")
    def review_verdict(token: str, view_id: str, body: VerdictBody):
        shoot, share = shoot_for(token, "client")
        assert share is not None
        ctx.view_of(shoot, view_id)

        def apply(s: Shoot):
            s.view(view_id).client = ClientReview(status=body.status, share=token) if body.status else None

        shoot = store.mutate(shoot.id, apply)
        return review_payload(shoot, share)

    @app.post("/api/review/{token}/comments")
    async def review_comment(token: str, body: CommentBody):
        shoot, share = shoot_for(token, "client")
        assert share is not None
        view = ctx.view_of(shoot, body.view)
        if view.meta.get("split_into") or not view.head:
            raise ApiError(400, "view_not_reviewable", "That photo is not part of the listing set.")
        point = None
        if body.point is not None:
            if len(body.point) != 2 or not all(0 <= x <= 1 for x in body.point):
                raise ApiError(422, "bad_point", "A point is [x, y] with each value between 0 and 1.")
            point = [float(body.point[0]), float(body.point[1])]
        text = " ".join(body.text.split())
        policy = classify_request(text)
        comment = Comment(view=view.id, text=text, share=token, point=point, base_version=view.head,
                          policy=policy.to_dict())
        used = sum(1 for c in shoot.comments if c.share == token and c.run)
        allowed = [i for i in policy.items if i.verdict != "decline"]
        if policy.items and not allowed:
            comment.status = "declined"
            comment.note = " ".join(dict.fromkeys(i.reason for i in policy.items))
        elif used >= share.max_requests:
            comment.note = (f"This link has used its {share.max_requests} automatic edits, so your photographer will "
                            "handle this one.")
        elif not agent_available():
            comment.note = "Saved for your photographer. The editing assistant is offline right now."
        else:
            mask_id = await asyncio.to_thread(point_mask, shoot, view.id, point) if point else None
            try:
                trace = ctx.start_trace(shoot.id, "chat", request=text, views=[view.id], mask=mask_id,
                                        origin="client", comment=comment.id)
                comment.run, comment.mask, comment.status = trace.id, mask_id, "started"
            except ApiError as e:
                comment.note = ("Saved for your photographer. The editing assistant is offline right now."
                                if e.code == "agent_unavailable" else e.message)
        shoot = store.mutate(shoot.id, lambda s: s.comments.append(comment))
        return comment_view(shoot, comment)

    @app.post("/api/review/{token}/comments/{comment_id}/undo")
    def review_undo(token: str, comment_id: str):
        shoot, share = shoot_for(token, "client")
        c = next((x for x in shoot.comments if x.id == comment_id and x.share == token), None)
        if c is None:
            raise ApiError(404, "comment_not_found", "No such request on this link.")
        view = ctx.view_of(shoot, c.view)
        if c.run:
            handle = ctx.manager.get(c.run)
            if handle is not None and handle.status in ("queued", "running"):
                raise ApiError(409, "still_working", "The edit is still being made; undo it once it finishes.")
        if not c.base_version or view.head == c.base_version:
            raise ApiError(400, "nothing_to_undo", "This photo is already back to how it was before the request.")
        store.set_head(shoot.id, c.view, c.base_version)

        def apply(s: Shoot):
            for x in s.comments:
                if x.id == comment_id:
                    x.status = "undone"
                    x.note = "Undone by the client."

        shoot = store.mutate(shoot.id, apply)
        return comment_view(shoot, next(x for x in shoot.comments if x.id == comment_id))

    @app.get("/api/review/{token}/runs/{run_id}/events")
    async def review_run_events(token: str, run_id: str, request: Request):
        shoot, _ = shoot_for(token, "client")
        if not any(c.run == run_id and c.share == token for c in shoot.comments):
            raise ApiError(404, "run_not_found", "No such edit on this link.")
        return ctx.run_stream(shoot.id, run_id, request)
