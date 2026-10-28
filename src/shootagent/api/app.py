"""FastAPI app: wires HTTP (docs/api.md) to the store, the agent runs and the tools. No editing logic here."""

from __future__ import annotations

import asyncio
import io
import os
import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..agent import loop
from ..config import Settings, get_settings
from ..store import Listing, ShootStore
from ..trace import read_events

ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
BLOB_RE = re.compile(r"^[0-9a-f]{8,64}\.[a-z0-9]{2,5}$")
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
DEV_ORIGINS = [f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in (5173, 4173)]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


# ---- request bodies -------------------------------------------------------------------------------------


class DemoBody(BaseModel):
    preset: str = Field(description="Demo preset, e.g. 'qc-tour' or 'chat'.")


class HeadBody(BaseModel):
    version: str


class ApproveBody(BaseModel):
    approved: bool


class MaskBody(BaseModel):
    points: list[list[float]] = Field(
        default_factory=list, description="[[x, y, label]] normalised; label 1 = include, 0 = exclude."
    )
    box: list[float] | None = None


class QcBody(BaseModel):
    views: list[str] | None = None


class ChatBody(BaseModel):
    message: str
    views: list[str] | None = None
    mask: str | None = None


def create_app(
    settings: Settings | None = None,
    store: ShootStore | None = None,
    client: Any = None,
    manager: loop.RunManager | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    store = store or ShootStore(settings.shoots_dir)
    manager = manager or loop.MANAGER
    app = FastAPI(title="ShootAgent", version="0.1.0")
    app.state.settings, app.state.store, app.state.manager = settings, store, manager

    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    @app.middleware("http")
    async def passcode(request: Request, call_next):
        code = settings.demo_passcode
        # The public disclosure page and the client review link carry their own token in the path; the token
        # is the credential, so the people they are sent to never need the photographer's passcode.
        tokened = request.url.path.startswith(("/api/public/", "/api/review/"))
        if code and request.url.path.startswith("/api") and request.method != "OPTIONS" and not tokened:
            # EventSource and <img> cannot send headers, so a query parameter or cookie also works.
            given = (
                request.headers.get("x-demo-passcode")
                or request.query_params.get("passcode")
                or request.cookies.get("demo_passcode")
            )
            if given != code:
                return _err(401, "passcode_required", "Send the demo passcode in the x-demo-passcode header.")
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return _err(e.status, e.code, e.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, e: StarletteHTTPException):
        return _err(e.status_code, "http_error", str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        return _err(422, "invalid_request", f"Fix the request body: {msg}")

    # ---- helpers ----------------------------------------------------------------------------------------
    def load(shoot_id: str):
        if not ID_RE.match(shoot_id):
            raise ApiError(400, "bad_id", "Shoot ids contain only letters, digits, '.', '_' and '-'.")
        try:
            return store.load(shoot_id)
        except KeyError:
            raise ApiError(
                404, "shoot_not_found", f"No shoot {shoot_id!r}. List shoots at /api/shoots."
            ) from None

    def view_of(shoot, view_id: str):
        try:
            return shoot.view(view_id)
        except KeyError:
            raise ApiError(404, "view_not_found", f"Shoot {shoot.id} has no view {view_id!r}.") from None

    def dump(model) -> dict:
        return model.model_dump(mode="json", by_alias=True)

    def start(shoot_id: str, mode: str, **kw) -> dict:
        trace = start_trace(shoot_id, mode, **kw)
        return {"run_id": trace.id}

    def start_trace(shoot_id: str, mode: str, **kw):
        try:
            trace = loop.start_run(
                store, shoot_id, mode, settings=settings, client=client, manager=manager, **kw
            )
        except ValueError as e:
            raise ApiError(400, "bad_run_request", str(e)) from None
        except RuntimeError as e:
            raise ApiError(503, "agent_unavailable", str(e)) from None
        return trace

    # ---- shoots -----------------------------------------------------------------------------------------
    @app.get("/api/shoots")
    def list_shoots():
        out = []
        for sid in store.list_ids():
            try:
                s = store.load(sid)
            except Exception:  # a half-written or foreign folder must not break the list
                continue
            thumb = next((v.head_version.blob for v in s.views if v.head), None)
            out.append(
                {
                    "id": s.id,
                    "title": s.title,
                    "created_at": s.created_at,
                    "view_count": len(s.views),
                    "thumb": thumb,
                }
            )
        return sorted(out, key=lambda x: x["created_at"], reverse=True)

    @app.post("/api/shoots")
    async def upload_shoot(request: Request):
        form = await request.form()
        title = str(form.get("title") or "Untitled shoot")
        pat = re.compile(r"^view(\d+)_(bracket(\d+)|finished)$")
        views: dict[int, dict[str, Any]] = {}
        with tempfile.TemporaryDirectory() as tmp:
            for key, value in form.multi_items():
                m = pat.match(key)
                if not m or not hasattr(value, "read"):
                    continue
                n = int(m.group(1))
                suffix = Path(value.filename or "x.jpg").suffix.lower() or ".jpg"
                path = Path(tmp) / f"{key}{suffix}"
                path.write_bytes(await value.read())
                slot = views.setdefault(n, {"brackets": {}, "finished": None})
                if m.group(3) is not None:
                    slot["brackets"][int(m.group(3))] = path
                else:
                    slot["finished"] = path
            if not views:
                raise ApiError(400, "no_files", "Attach files named view<N>_bracket<M> and view<N>_finished.")
            spec = [
                {"brackets": [b[k] for k in sorted(b)], "finished": v["finished"]}
                for _, v in sorted(views.items())
                for b in [v["brackets"]]
            ]
            shoot = await asyncio.to_thread(_ingest_upload, store, title, spec)
        return dump(shoot)

    @app.post("/api/shoots/demo")
    def demo_shoot(body: DemoBody):
        try:
            from .. import ingest
        except ImportError as e:
            raise ApiError(503, "ingest_unavailable", f"Demo presets need shootagent.ingest ({e}).") from None
        try:
            shoot = ingest.build_demo_shoot(store, body.preset)
        except (KeyError, ValueError) as e:
            raise ApiError(400, "bad_preset", str(e)) from None
        return dump(store.load(shoot.id) if hasattr(shoot, "id") else store.load(str(shoot)))

    @app.get("/api/shoots/{shoot_id}")
    def get_shoot(shoot_id: str):
        return dump(load(shoot_id))

    @app.patch("/api/shoots/{shoot_id}/listing")
    def patch_listing(shoot_id: str, body: Listing):
        load(shoot_id)
        fields = body.model_dump(exclude_unset=True)

        def apply(s):
            for k, v in fields.items():
                setattr(s.listing, k, v)

        return dump(store.mutate(shoot_id, apply))

    @app.get("/api/shoots/{shoot_id}/blobs/{blob}")
    def get_blob(shoot_id: str, blob: str, w: int | None = None):
        load(shoot_id)
        if not BLOB_RE.match(blob):
            raise ApiError(400, "bad_blob", "Blob names are a content hash plus an extension.")
        path = store.blob_path(shoot_id, blob)
        if not path.exists():
            raise ApiError(404, "blob_not_found", f"No blob {blob} in shoot {shoot_id}.")
        if w is not None:
            path = _resized(store.shoot_dir(shoot_id) / "cache", path, max(16, min(4096, w)))
        return FileResponse(path, headers=IMMUTABLE)

    # ---- views ------------------------------------------------------------------------------------------
    @app.post("/api/shoots/{shoot_id}/views/{view_id}/head")
    def set_head(shoot_id: str, view_id: str, body: HeadBody):
        view = view_of(load(shoot_id), view_id)
        try:
            view.version(body.version)
        except KeyError:
            raise ApiError(
                404, "version_not_found", f"View {view_id} has no version {body.version!r}."
            ) from None
        store.set_head(shoot_id, view_id, body.version)
        return dump(store.load(shoot_id).view(view_id))

    @app.post("/api/shoots/{shoot_id}/views/{view_id}/approve")
    def approve(shoot_id: str, view_id: str, body: ApproveBody):
        view_of(load(shoot_id), view_id)

        def apply(s):
            s.view(view_id).approved = body.approved

        return dump(store.mutate(shoot_id, apply).view(view_id))

    @app.post("/api/shoots/{shoot_id}/views/{view_id}/masks")
    def make_mask(shoot_id: str, view_id: str, body: MaskBody):
        from ..tools import _ops
        from ..tools._common import ToolError

        view = view_of(load(shoot_id), view_id)
        if not body.points and not body.box:
            raise ApiError(400, "no_prompt", "Send at least one point or a box.")
        img = store.get_image(shoot_id, view.head_version.blob)
        try:
            m = _ops.fit_mask(_ops.segment_points(img, body.points, body.box), img.shape[:2])
        except ToolError as e:
            raise ApiError(503, "segmentation_unavailable", str(e)) from None
        if not m.any():
            raise ApiError(
                422, "empty_mask", "Nothing was selected; click inside the object or draw a box around it."
            )
        return dump(store.add_mask(shoot_id, view_id, m, source="user"))

    # ---- runs -------------------------------------------------------------------------------------------
    @app.post("/api/shoots/{shoot_id}/qc")
    def qc(shoot_id: str, body: QcBody | None = None):
        load(shoot_id)
        return start(shoot_id, "qc", views=(body.views if body else None))

    @app.post("/api/shoots/{shoot_id}/chat")
    def chat(shoot_id: str, body: ChatBody):
        load(shoot_id)
        return start(shoot_id, "chat", request=body.message, views=body.views, mask=body.mask)

    @app.post("/api/shoots/{shoot_id}/reel")
    def reel(shoot_id: str):
        load(shoot_id)
        return start(shoot_id, "reel")

    @app.post("/api/shoots/{shoot_id}/copy")
    def copy(shoot_id: str):
        load(shoot_id)
        return start(shoot_id, "copy")

    @app.get("/api/shoots/{shoot_id}/reel/latest.mp4")
    def reel_latest(shoot_id: str):
        load(shoot_id)
        path = store.shoot_dir(shoot_id) / "reel" / "latest.mp4"
        if not path.exists():
            raise ApiError(404, "no_reel", "No reel has been rendered for this shoot yet.")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/api/shoots/{shoot_id}/runs")
    def list_runs(shoot_id: str):
        shoot = load(shoot_id)
        out = []
        for run_id in shoot.runs:
            events = read_events(store.shoot_dir(shoot_id) / "runs" / f"{run_id}.jsonl")
            start_ev = next((e for e in events if e["kind"] == "run_start"), None)
            end_ev = next((e for e in reversed(events) if e["kind"] == "run_end"), None)
            handle = manager.get(run_id)
            usd = (
                end_ev["data"].get("usd")
                if end_ev
                else sum(float(e["data"].get("usd") or 0) for e in events if e["kind"] == "usage")
            )
            status = handle.status if handle else ("done" if end_ev else "unfinished")
            out.append(
                {
                    "id": run_id,
                    "mode": (start_ev or {}).get("data", {}).get("mode")
                    or (handle.agent.mode if handle else None),
                    "request": (start_ev or {}).get("data", {}).get("request")
                    or (handle.agent.request if handle else None),
                    "started": start_ev["t"] if start_ev else None,
                    "done": end_ev is not None,
                    "status": status,
                    "usd": round(float(usd or 0), 4),
                    "summary": end_ev["data"].get("summary") if end_ev else None,
                }
            )
        return out

    def run_stream(shoot_id: str, run_id: str, request: Request) -> EventSourceResponse:
        if not ID_RE.match(run_id):
            raise ApiError(400, "bad_id", "Bad run id.")
        path = store.shoot_dir(shoot_id) / "runs" / f"{run_id}.jsonl"
        handle = manager.get(run_id)
        if handle is None and not path.exists():
            raise ApiError(404, "run_not_found", f"No run {run_id!r} on shoot {shoot_id}.")
        last = request.headers.get("last-event-id")
        after = int(last) if last and last.isdigit() else 0

        def sse(ev: dict) -> dict:
            return {
                "event": "trace",
                "id": str(ev.get("seq", "")),
                "data": json.dumps(ev, ensure_ascii=False, default=str),
            }

        async def live():
            q = handle.trace.subscribe()
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=30)
                    except TimeoutError:
                        if await request.is_disconnected():
                            return
                        continue
                    if ev.get("seq", 0) > after:
                        yield sse(ev)
                    if ev["kind"] == "run_end":
                        return
            finally:
                handle.trace.unsubscribe(q)

        async def replay():
            events = read_events(path)
            for ev in events:
                if ev.get("seq", 0) > after:
                    yield sse(ev)
            if not any(e["kind"] == "run_end" for e in events):
                # The process that ran it is gone; end the stream so EventSource does not reconnect forever.
                seq = (events[-1]["seq"] + 1) if events else 1
                yield sse(
                    {
                        "run": run_id,
                        "seq": seq,
                        "t": events[-1]["t"] if events else None,
                        "kind": "run_end",
                        "view": None,
                        "data": {
                            "ok": False,
                            "summary": "The run did not finish (server restarted).",
                            "ms": 0,
                            "usd": 0,
                            "counts": {},
                        },
                    }
                )

        return EventSourceResponse(live() if handle is not None else replay(), ping=15)

    @app.get("/api/shoots/{shoot_id}/runs/{run_id}/events")
    async def run_events(shoot_id: str, run_id: str, request: Request):
        load(shoot_id)
        return run_stream(shoot_id, run_id, request)

    @app.post("/api/shoots/{shoot_id}/runs/{run_id}/cancel")
    def cancel(shoot_id: str, run_id: str):
        load(shoot_id)
        return {"ok": manager.cancel(run_id)}

    # ---- delivery: disclosures, publication, client review, listing kit ---------------------------------------
    from .delivery import Ctx, register

    register(app, Ctx(settings=settings, store=store, manager=manager, client=client, load=load, view_of=view_of,
                      dump=dump, start_trace=start_trace, run_stream=run_stream))

    # ---- web UI -----------------------------------------------------------------------------------------
    if WEB_DIST.is_dir():

        @app.get("/{path:path}", include_in_schema=False)
        def web(path: str):
            if path.startswith("api/"):
                raise ApiError(404, "not_found", f"No API route /{path}.")
            target = (WEB_DIST / path).resolve()
            if path and target.is_file() and WEB_DIST.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(WEB_DIST / "index.html")

    return app


def _resized(cache_dir: Path, path: Path, w: int) -> Path:
    """Copy of `path` with long edge `w` (never upscaled), cached by name; masks stay PNG."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as im:
        is_mask = im.mode in ("L", "1")
        if max(im.size) <= w:
            return path
        out = cache_dir / f"{path.stem}.w{w}.{'png' if is_mask else 'jpg'}"
        if out.exists():
            return out
        if not is_mask:
            im = ImageOps.exif_transpose(im)  # match store.get_image, which serves upright pixels
        im.thumbnail((w, w), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        if is_mask:
            im.convert("L").save(buf, "PNG")
        else:
            im.convert("RGB").save(buf, "JPEG", quality=88)
    # Unique temp name: two requests for the same uncached size used to share one and 500 on Windows.
    tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_bytes(buf.getvalue())
    try:
        os.replace(tmp, out)
    except PermissionError:  # Windows: another request published it and is reading it
        tmp.unlink(missing_ok=True)
        if not out.exists():
            raise
    return out


def _ingest_upload(store: ShootStore, title: str, spec: list[dict]):
    """Create a shoot from uploaded files. Uses shootagent.ingest when it offers an upload entry point."""
    try:
        from .. import ingest

        fn = getattr(ingest, "create_shoot_from_files", None)
    except ImportError:
        fn = None
    if fn is not None:
        shoot = fn(store, title, spec)
        return store.load(shoot.id if hasattr(shoot, "id") else str(shoot))
    from ..imaging.io import load_rgb
    from ..store import Bracket, View

    shoot = store.create(title, source={"kind": "upload"})
    views = []
    for i, v in enumerate(spec, start=1):
        brackets = [Bracket(blob=store.put_file(shoot.id, p), index=j) for j, p in enumerate(v["brackets"])]
        finished = store.put_file(shoot.id, v["finished"]) if v["finished"] else None
        views.append(View(id=f"v{i:02d}", brackets=brackets, finished=finished))
    store.mutate(shoot.id, lambda s: s.views.extend(views))
    for i, v in enumerate(spec, start=1):
        src = v["finished"] or (v["brackets"][len(v["brackets"]) // 2] if v["brackets"] else None)
        if src is not None:
            store.add_version(shoot.id, f"v{i:02d}", load_rgb(src), op="ingest", note="uploaded")
    return store.load(shoot.id)
