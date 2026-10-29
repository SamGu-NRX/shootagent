"""Agent trace: an append-only event log per run, streamed to the UI over SSE.

The trace is the product surface that shows the agent's reasoning, so every event carries enough
data for the UI to render it without another request. Event kinds:

    run_start    {mode: "qc"|"chat"|"reel"|"film", request?, views, languages?, media?}
    plan         {steps: [{view?, intent, tool?}], text}
    thought      {text}                         model text between tool calls
    tool_call    {call_id, tool, input}
    tool_result  {call_id, tool, ok, summary, version?, mask?, images?: [blob], ms, error?}
    check        {call_id?, version, passed, metrics: {...}, judge?: {verdict, reason, model}}
    retry        {attempt, reason, next}        the agent is trying a different approach
    finding      {finding}                      a located defect (store.Finding as dict)
    media        {shot?, language?, fingerprint, backend, cached, seconds}   film: a clip or narration made or reused
    shot         {shot, source, fell_back, clips, note}                     film: what a shot will play
    view_result  {results: [{request, status, reason}]}  chat outcome for one view
    message      {text}                         the agent's reply to the user
    usage        {model, input_tokens, output_tokens, usd}
    run_end      {ok, summary, ms, usd, counts}
    error        {message}

Images are referenced by blob name; the API serves them at /api/shoots/<id>/blobs/<blob>.
"""
from __future__ import annotations

import asyncio
import json
import math
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .store import now_iso


class TraceRun:
    """Writes events to runs/<run_id>.jsonl and fans them out to live subscribers.

    `emit` is thread-safe and may be called from worker threads (tools run in a thread pool);
    subscribers are asyncio queues bound to the server's event loop.
    """

    def __init__(self, shoot_dir: Path, mode: str, run_id: str | None = None):
        self.id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.mode = mode
        self.path = Path(shoot_dir) / "runs" / f"{self.id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._lock = threading.Lock()
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self.started = time.perf_counter()
        self.usd = 0.0
        self.done = False

    def emit(self, kind: str, view: str | None = None, **data: Any) -> dict:
        data = _finite(data)
        with self._lock:
            self._seq += 1
            event = {"run": self.id, "seq": self._seq, "t": now_iso(), "kind": kind, "view": view, "data": data}
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            if kind == "usage":
                self.usd += float(data.get("usd") or 0)
            if kind == "run_end":
                self.done = True
            # Queue for subscribers while still holding the lock, so two threads emitting at once cannot
            # deliver seq N+1 before seq N (the UI reducer drops events older than the last one it saw).
            for loop, q in self._subs:
                loop.call_soon_threadsafe(q.put_nowait, event)
        return event

    def subscribe(self) -> asyncio.Queue:
        """Queue that first receives every past event, then live ones. Call from the event loop."""
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            for event in read_events(self.path):
                q.put_nowait(event)
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(lp, x) for lp, x in self._subs if x is not q]

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


def _finite(value: Any) -> Any:
    """Replace NaN and infinity with None. They are not valid JSON, and the browser's JSON.parse rejects them."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class NullTrace(TraceRun):
    """Trace for scripts and tests that only want the jsonl file or nothing at all."""

    def __init__(self):  # noqa: D401 - no file, no subscribers
        self.id = "null"
        self.mode = "none"
        self.usd = 0.0
        self.done = False
        self.started = time.perf_counter()
        self.events: list[dict] = []

    def emit(self, kind: str, view: str | None = None, **data: Any) -> dict:
        event = {"kind": kind, "view": view, "data": _finite(data)}
        self.events.append(event)
        if kind == "usage":
            self.usd += float(data.get("usd") or 0)
        return event
