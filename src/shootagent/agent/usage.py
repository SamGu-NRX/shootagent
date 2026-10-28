"""Token pricing for the `usage` events. USD per million tokens, from Anthropic's published list prices
(October 2026). Cache writes are billed at 1.25x input for the 5-minute TTL and 2x for the 1-hour TTL;
cache reads at 0.1x input."""

from __future__ import annotations

from typing import Any

PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-fable-5-1": (10.0, 50.0),
}


def _get(u: Any, key: str) -> int:
    v = u.get(key) if isinstance(u, dict) else getattr(u, key, None)
    return int(v or 0)


def usage_event(model: str, usage: Any) -> dict[str, Any]:
    """Fields for a trace `usage` event from an Anthropic `Usage` object (or a dict with the same keys).
    Unknown models are priced at 0 and flagged so the total is not silently wrong."""
    inp, out = _get(usage, "input_tokens"), _get(usage, "output_tokens")
    cread, cwrite = _get(usage, "cache_read_input_tokens"), _get(usage, "cache_creation_input_tokens")
    cc = usage.get("cache_creation") if isinstance(usage, dict) else getattr(usage, "cache_creation", None)
    w1h = _get(cc, "ephemeral_1h_input_tokens") if cc is not None else 0
    w5m = cwrite - w1h if cc is not None else cwrite
    price = PRICES.get(model)
    data: dict[str, Any] = {
        "model": model,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cread,
        "cache_creation_input_tokens": cwrite,
    }
    if price is None:
        data.update(usd=0.0, unpriced=True)
        return data
    pin, pout = price
    usd = (inp * pin + out * pout + cread * pin * 0.1 + w5m * pin * 1.25 + w1h * pin * 2.0) / 1e6
    data["usd"] = round(usd, 6)
    return data
