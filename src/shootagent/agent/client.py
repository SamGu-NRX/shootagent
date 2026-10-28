"""Anthropic client built from settings: a gateway URL with a bearer token, or a plain API key."""

from __future__ import annotations

from functools import lru_cache

import anthropic

from ..config import Settings, anthropic_client_kwargs, get_settings


def make_client(settings: Settings | None = None) -> anthropic.Anthropic:
    return anthropic.Anthropic(timeout=120.0, max_retries=3, **anthropic_client_kwargs(settings or get_settings()))


@lru_cache
def default_client() -> anthropic.Anthropic:
    return make_client()
