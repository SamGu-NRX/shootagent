"""Limits on the public endpoints: per-IP rate limits, a daily Q&A spend ceiling, and input cleaning."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from starlette.requests import Request

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏ -‮⁦-⁩]")


def client_ip(request: Request) -> str:
    """The caller's IP. Behind the Cloudflare tunnel (scripts/serve-public.sh) every request arrives from
    loopback, so the CF-Connecting-IP header Cloudflare sets is used then, and only then: a caller reaching
    the app directly could put anything in that header."""
    host = request.client.host if request.client else "unknown"
    if host in LOOPBACK:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if forwarded:
            return forwarded[:64]
    return host


class RateLimiter:
    """Sliding-window counters in memory, keyed by (bucket, ip). A restart forgets them, which is acceptable for
    a single-process demo server; the daily spend ceiling below is persisted and is the hard cost bound."""

    def __init__(self, clock=time.monotonic):
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def allow(self, bucket: str, key: str, limits: tuple[tuple[int, float], ...]) -> float | None:
        """Record a hit unless any (count, seconds) limit is reached. Returns None when allowed, otherwise the
        seconds until the oldest hit in the full window expires."""
        now = self._clock()
        longest = max(seconds for _, seconds in limits)
        with self._lock:
            hits = self._hits.setdefault((bucket, key), deque())
            while hits and now - hits[0] > longest:
                hits.popleft()
            for count, seconds in limits:
                recent = [t for t in hits if now - t <= seconds]
                if len(recent) >= count:
                    return max(1.0, seconds - (now - recent[0]))
            hits.append(now)
            if len(self._hits) > 50_000:  # bound memory under a spray of distinct IPs
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > longest][:25_000]:
                    self._hits.pop(k, None)
        return None


class SpendLedger:
    """USD spent on Q&A per UTC day, persisted so a restart does not reset the ceiling."""

    def __init__(self, path: Path, daily_usd: float):
        self.path = Path(path)
        self.daily_usd = daily_usd
        self._lock = threading.Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> dict[str, float]:
        if not self.path.is_file():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def spent_today(self) -> float:
        with self._lock:
            return float(self._load().get(self._today(), 0.0))

    def can_spend(self, estimate: float) -> bool:
        return self.spent_today() + estimate <= self.daily_usd

    def add(self, usd: float) -> None:
        with self._lock:
            data = self._load()
            day = self._today()
            data[day] = round(data.get(day, 0.0) + usd, 6)
            data = dict(sorted(data.items())[-31:])  # a month of history is plenty
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)


def clean_text(value: str, limit: int) -> str:
    """Single paragraph of visible text: control and bidi-override characters removed, whitespace collapsed.
    Raises ValueError past `limit` characters rather than truncating what the buyer wrote."""
    text = " ".join(_CONTROL.sub("", value).split())
    if len(text) > limit:
        raise ValueError(f"Keep it under {limit} characters.")
    return text
