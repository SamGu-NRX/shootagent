"""Anthropic client built from settings: a gateway URL with a bearer token, or a plain API key."""

from __future__ import annotations

from functools import lru_cache

import anthropic

from ..config import Settings, get_settings


def make_client(settings: Settings | None = None) -> anthropic.Anthropic:
    s = settings or get_settings()
    if not (s.anthropic_auth_token or s.anthropic_api_key):
        raise RuntimeError("Set ANTHROPIC_API_KEY, or ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN, in .env.")
    # The SDK accepts only one credential; a gateway token wins over a key picked up from the environment.
    cred = (
        {"auth_token": s.anthropic_auth_token} if s.anthropic_auth_token else {"api_key": s.anthropic_api_key}
    )
    return anthropic.Anthropic(base_url=s.anthropic_base_url or None, timeout=120.0, max_retries=3, **cred)


@lru_cache
def default_client() -> anthropic.Anthropic:
    return make_client()
