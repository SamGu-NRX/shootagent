"""Runtime settings, read from the environment and `.env`.

Every model endpoint is configurable so the same code runs against a local gateway on the demo laptop
and against vendor APIs when deployed.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # Where shoots, blobs and run traces live.
    data_dir: Path = Path("data")

    # Claude: the agent loop and the vision self-check.
    anthropic_api_key: str | None = None
    anthropic_auth_token: str | None = None
    anthropic_base_url: str | None = None
    agent_model: str = "claude-sonnet-5-5"
    judge_model: str = "claude-sonnet-5-5"
    # Effort for agent turns. "medium" keeps a whole-shoot run near the 2-minute demo target; untuned.
    agent_effort: str = "medium"

    # OpenAI-compatible image edit endpoint for generative fills.
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    image_edit_model: str = "gpt-image-2"

    # Local models. "cuda", "mps" or "cpu"; empty picks the best available.
    torch_device: str = ""
    grounding_model: str = "IDEA-Research/grounding-dino-base"
    sam_model: str = "facebook/sam2.1-hiera-large"
    lama_url: str = "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt"

    # Memory budget. Defaults suit a 32 GB PC with an 8 GB GPU. On a laptop with unified memory (Apple
    # silicon) a QC run with these defaults peaked near 10 GiB, which a memory guard on the demo MacBook
    # killed; lower values trade speed for a smaller peak.
    tool_workers: int = 4  # views edited in parallel by one agent run
    qc_workers: int = 2  # bracket analyses run in the background at once
    fusion_long_edge: int = 4096  # long edge of the bracket fusion used for restores and split views

    # Agent limits.
    max_agent_steps: int = 40
    max_fix_attempts: int = 3

    # Unzipped AutoHDR hackathon datasets, for ingest and demo presets.
    autohdr_defect_dir: Path | None = None
    autohdr_customer_dir: Path | None = None
    autohdr_editor_requests: Path | None = None

    # Optional shared passcode for a public deployment. Empty disables the check.
    demo_passcode: str = ""

    # Listing film (film/). "replay" serves clips and narration from `film_replay_dir` only, so a machine
    # without Grok or a Gemini key still runs the whole pass; "live" generates with the grok CLI and Gemini
    # TTS. Both read the fingerprint cache first, so nothing is generated twice.
    film_media: Literal["replay", "live"] = "replay"
    film_replay_dir: Path | None = None  # default <data_dir>/replay
    film_workers: int = 2  # shots prepared at once (checks and depth renders); Grok calls stay one at a time
    grok_bin: str = "grok"
    # Gemini key, filled from the Keychain at launch on the demo Mac. Never logged.
    gemini_api_key: str | None = None
    tts_model: str = "gemini-3.8-flash-tts"
    tts_voice: str = "Sulafat"  # the lab's pick: natural in English and Spanish (lab/REPORT.md on explore/media-tests)
    # Depth parallax in output px. Each depth shot first tries the stronger move, checked like a generated clip,
    # and falls back to the safe one unchecked. In the experiment 24 px passed the checker on 6 of 6 clips and
    # 40 px on 5 of 10, the failures on photos with the dataset's ghosting, which QC now fixes first.
    depth_parallax_px: float = 24.0
    depth_strong_px: float = 40.0  # 0 turns the stronger move off
    # Reel Studio's engine (Python 3.11, its own venv) finishes the film: captions, music mix, cover, verify.
    reelengine_python: Path | None = None
    # Reel Studio's animation templates (reel-studio-app/templates/animations, with `npm ci` run there) render
    # the film's title and stat cards headless through Playwright's Chromium. Unset: cards are drawn with PIL.
    reelstudio_templates: Path | None = None
    node_bin: str = "node"
    # faster-whisper models (small.en; `small` for Spanish). Default <data_dir>/models/speech: git-ignored and
    # outside the OS temp folder, which Windows clears.
    reelengine_models: Path | None = None

    @property
    def shoots_dir(self) -> Path:
        return self.data_dir / "shoots"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def speech_models_dir(self) -> Path:
        return self.reelengine_models or self.models_dir / "speech"

    @property
    def media_cache_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def replay_dir(self) -> Path:
        return self.film_replay_dir or self.data_dir / "replay"


def anthropic_client_kwargs(s: Settings) -> dict:
    """Credential for anthropic.Anthropic.

    - A gateway token goes only to its gateway: ANTHROPIC_AUTH_TOKEN is used when ANTHROPIC_BASE_URL is set.
    - Otherwise ANTHROPIC_API_KEY, sent to ANTHROPIC_BASE_URL if set (a proxy) and to Anthropic's API if not.
    - A gateway token with no gateway URL is refused rather than sent to Anthropic's API.
    An empty base URL means unset, so a deployment can blank the gateway URL from the environment. The URL is
    always passed explicitly: with base_url=None the SDK falls back to the ANTHROPIC_BASE_URL environment
    variable, and a blanked one yields an empty URL."""
    gateway = (s.anthropic_base_url or "").strip()
    if s.anthropic_auth_token and gateway:
        return {"auth_token": s.anthropic_auth_token, "base_url": gateway}
    if s.anthropic_api_key:
        return {"api_key": s.anthropic_api_key, "base_url": gateway or "https://api.anthropic.com"}
    if s.anthropic_auth_token:
        raise RuntimeError(
            "ANTHROPIC_AUTH_TOKEN is set without ANTHROPIC_BASE_URL. Set the gateway URL with the token, or use "
            "ANTHROPIC_API_KEY for Anthropic's API."
        )
    raise RuntimeError("Set ANTHROPIC_API_KEY, or ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN, in .env.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
