"""HTTP routes for the creative lab (docs/api.md, "Creative lab"): start, list, read, cancel, resume and
re-simulate labs, and serve their reels. creative/lab.py runs the lab; this module only wires HTTP to it.

app.py includes the router with one line inside create_app, so the routes share its error shape (ApiError), its
passcode middleware and the client it was given (tests pass a fake one).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import Settings
from ..creative import lab
from ..creative.schema import LabOptions
from ..store import ShootStore
from .app import ID_RE, ApiError


class SimulateBody(BaseModel):
    """Launch parameters; a field left out keeps the lab's current value."""

    followers: int | None = Field(None, ge=0, le=10_000_000)
    hours: int | None = Field(None, ge=1, le=720)
    seed: int | None = None


def creative_router(store: ShootStore, settings: Settings, client: Any = None) -> APIRouter:
    router = APIRouter(prefix="/api/shoots/{shoot_id}/creative")

    def shoot(shoot_id: str) -> None:
        if not ID_RE.match(shoot_id):
            raise ApiError(400, "bad_id", "Shoot ids contain only letters, digits, '.', '_' and '-'.")
        try:
            store.load(shoot_id)
        except KeyError:
            raise ApiError(404, "shoot_not_found", f"No shoot {shoot_id!r}. List shoots at /api/shoots.") from None

    def state(shoot_id: str, lab_id: str):
        shoot(shoot_id)
        try:
            return lab.load_lab(store, shoot_id, lab_id)
        except KeyError:
            raise ApiError(404, "lab_not_found",
                           f"No lab {lab_id!r} on shoot {shoot_id}. List labs at /api/shoots/{shoot_id}/creative.") from None

    def start(shoot_id: str, options: LabOptions, resume: str | None = None) -> dict:
        try:
            lab_id = lab.start_lab(store, shoot_id, options, settings=settings, client=client, resume=resume)
        except lab.LabConflict as e:
            raise ApiError(409, "lab_conflict", str(e)) from None
        except ValueError as e:
            raise ApiError(400, "bad_options", f"Fix the lab options: {e}") from None
        except RuntimeError as e:  # no Claude credentials
            raise ApiError(503, "agent_unavailable", str(e)) from None
        return {"lab_id": lab_id}

    @router.post("")
    def start_lab(shoot_id: str, body: LabOptions | None = None):
        shoot(shoot_id)
        return start(shoot_id, body or LabOptions())

    @router.get("")
    def list_labs(shoot_id: str):
        shoot(shoot_id)
        return lab.list_labs(store, shoot_id)

    @router.get("/{lab_id}")
    def get_lab(shoot_id: str, lab_id: str):
        return state(shoot_id, lab_id).model_dump(mode="json")

    @router.post("/{lab_id}/cancel")
    def cancel_lab(shoot_id: str, lab_id: str):
        state(shoot_id, lab_id)
        return {"ok": lab.cancel_lab(store, shoot_id, lab_id)}

    @router.post("/{lab_id}/resume")
    def resume_lab(shoot_id: str, lab_id: str):
        current = state(shoot_id, lab_id)
        return start(shoot_id, current.options, resume=lab_id)

    @router.post("/{lab_id}/simulate")
    def simulate(shoot_id: str, lab_id: str, body: SimulateBody | None = None):
        opts = state(shoot_id, lab_id).options
        body = body or SimulateBody()
        try:
            sims = lab.resimulate(store, shoot_id, lab_id,
                                  followers=opts.followers if body.followers is None else body.followers,
                                  hours=opts.hours if body.hours is None else body.hours,
                                  seed=opts.seed if body.seed is None else body.seed)
        except lab.LabConflict as e:
            raise ApiError(409, "lab_conflict", str(e)) from None
        except ValueError as e:
            raise ApiError(400, "bad_simulation", str(e)) from None
        return [s.model_dump(mode="json") for s in sims]

    @router.get("/{lab_id}/files/{path:path}")
    def lab_file(shoot_id: str, lab_id: str, path: str):
        """A file under the lab directory (reels/B.mp4, reels/B.jpg, ...). Range requests work, so <video> seeks."""
        state(shoot_id, lab_id)
        parts = path.split("/")
        if not path or "\\" in path or "\x00" in path or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            raise ApiError(400, "bad_path", "File paths are relative to the lab folder, e.g. reels/B.mp4.")
        root = lab.lab_dir(store, shoot_id, lab_id).resolve()
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            raise ApiError(404, "file_not_found", f"Lab {lab_id} has no file {path!r}.")
        # A resumed lab can re-render a reel under the same name, so caches must revalidate (ETag, Last-Modified).
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    return router
