"""Settings for the public listing endpoints, read from the environment and `.env`.

Kept apart from shootagent.config so the listing module can be configured (and merged) on its own.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ListingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # ntfy topic for the realtor's phone. Anyone who knows the topic can read its notifications, so it is a
    # secret: set it in .env.local, never commit it, and never log it. Empty disables the notification.
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"

    # Public origin of the deployment, e.g. https://shootagent.sillion.app. Builds the QR code target and the
    # notification's click URL. Empty: the origin the realtor published from, as the browser reported it.
    public_base_url: str = ""

    # Buyer Q&A. The model only sees the listing's photos and fields; it has no tools.
    listing_qa_model: str = "claude-sonnet-5-5"
    # Hard ceiling on Q&A spend per UTC day across all listings. A public page can be scripted; past the
    # ceiling every question gets the "your agent will follow up" answer instead of a model call. Six questions on
    # the 7-photo house cost $0.0038-0.0043 each with the photo prefix cached (2026-10-28), so $1 is about 250
    # questions a day. The ceiling itself is a guess: there is no buyer traffic data yet.
    listing_qa_daily_usd: float = 1.0
    # Long edge of the photos the Q&A model sees. 1024 px keeps 7 photos near 7k input tokens.
    listing_qa_photo_px: int = 1024
