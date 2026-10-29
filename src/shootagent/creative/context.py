"""What every creative-lab phase receives: the shoot, settings, a Claude client, and callbacks into the lab state.

lab.py builds one LabContext per run and persists what the callbacks report. `make_context` builds a standalone
one (printing instead of persisting) so a module can be tried on its own:

    ctx = make_context("64170ca0a0")
    brief = room.write_brief(ctx)
"""
from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, anthropic_client_kwargs, get_settings
from ..models.vision import usage_cost
from ..store import ShootStore
from .schema import LabOptions, RoomKind, RoomMessage, Usage


class Cancelled(Exception):
    pass


class UsageMeter:
    """Thread-safe running total of model spend, by agent role."""

    def __init__(self, usage: Usage | None = None):
        self.usage = usage or Usage()
        self._lock = threading.Lock()

    def add(self, role: str, model: str, response_usage: Any) -> float:
        u = response_usage
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        try:
            usd = usage_cost(model, u.input_tokens, u.output_tokens, cache_read, cache_write)
        except KeyError:
            usd = 0.0
        with self._lock:
            self.usage.usd += usd
            self.usage.input_tokens += u.input_tokens + cache_read + cache_write
            self.usage.output_tokens += u.output_tokens
            self.usage.calls += 1
            self.usage.by_role[role] = self.usage.by_role.get(role, 0.0) + usd
        return usd

    def add_usd(self, role: str, usd: float) -> None:
        """Spend outside Claude (text to speech, transcription)."""
        with self._lock:
            self.usage.usd += usd
            self.usage.by_role[role] = self.usage.by_role.get(role, 0.0) + usd


@dataclass
class LabContext:
    store: ShootStore
    shoot_id: str
    settings: Settings
    lab_dir: Path  # data/shoots/<shoot>/creative/<lab id>/
    options: LabOptions
    client: Any  # anthropic.Anthropic, shared by every thread
    emit_fn: Callable[[RoomMessage], None]  # lab.py appends to LabState.feed and saves
    progress_fn: Callable[[str, str], None]  # (phase key, detail)
    # (kind, items) with kind "concepts" | "critiques" | "finalists": work published mid-phase, so the dashboard
    # shows concept cards while the room is still talking and a cancelled run keeps them. None = not collected.
    publish_fn: Callable[[str, list[Any]], None] | None = None
    usage: UsageMeter = field(default_factory=UsageMeter)
    cancel: threading.Event = field(default_factory=threading.Event)
    t0: float = field(default_factory=time.monotonic)
    _ids: Any = field(default_factory=lambda: itertools.count(1))
    _id_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def elapsed(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def emit(self, agent: str, kind: RoomKind, text: str, concept: str | None = None, refs: list[str] | tuple = ()) -> RoomMessage:
        """Post a message to the writers' room feed (what the dashboard shows live)."""
        with self._id_lock:
            n = next(self._ids)
        msg = RoomMessage(id=f"m{n:04d}", t=self.elapsed, agent=agent, kind=kind, text=text.strip(), concept=concept, refs=list(refs))
        self.emit_fn(msg)
        return msg

    def progress(self, phase: str, detail: str) -> None:
        self.progress_fn(phase, detail)

    def publish(self, kind: str, items: list[Any]) -> None:
        """Hand finished work to the lab before the phase returns: "concepts" (ReelConcepts, upserted by id),
        "critiques" (Critiques, upserted by critic and concept) or "finalists" (concept ids, replacing the list)."""
        if self.publish_fn is not None and items is not None:
            self.publish_fn(kind, list(items))


def default_client(settings: Settings | None = None):
    import anthropic

    s = settings or get_settings()
    return anthropic.Anthropic(max_retries=3, timeout=600, **anthropic_client_kwargs(s))


def make_context(shoot_id: str, lab_dir: Path | None = None, options: LabOptions | None = None, client: Any = None,
                 settings: Settings | None = None, quiet: bool = False) -> LabContext:
    """A standalone context for trying one module: feed messages and progress are printed, not saved."""
    s = settings or get_settings()
    store = ShootStore(s.shoots_dir)
    lab_dir = lab_dir or store.shoot_dir(shoot_id) / "creative" / "scratch"
    lab_dir.mkdir(parents=True, exist_ok=True)

    def emit(m: RoomMessage) -> None:
        if not quiet:
            print(f"[{m.t:7.1f}s] {m.agent:>12} {m.kind:<10} {m.text[:160]}")

    def progress(phase: str, detail: str) -> None:
        if not quiet:
            print(f"[progress] {phase}: {detail}")

    return LabContext(store=store, shoot_id=shoot_id, settings=s, lab_dir=lab_dir, options=options or LabOptions(),
                      client=client if client is not None else default_client(s), emit_fn=emit, progress_fn=progress)
