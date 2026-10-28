"""Runtime settings, read from the environment and `.env`.

Every model endpoint is configurable so the same code runs against a local gateway on the demo laptop
and against vendor APIs when deployed.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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

    # OpenAI-compatible image edit endpoint for generative fills.
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    image_edit_model: str = "gpt-image-2"

    # Local models. "cuda", "mps" or "cpu"; empty picks the best available.
    torch_device: str = ""
    grounding_model: str = "IDEA-Research/grounding-dino-base"
    sam_model: str = "facebook/sam2.1-hiera-large"
    lama_url: str = "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt"

    # Agent limits.
    max_agent_steps: int = 40
    max_fix_attempts: int = 3

    # Unzipped AutoHDR hackathon datasets, for ingest and demo presets.
    autohdr_defect_dir: Path | None = None
    autohdr_customer_dir: Path | None = None
    autohdr_editor_requests: Path | None = None

    # Optional shared passcode for a public deployment. Empty disables the check.
    demo_passcode: str = ""

    @property
    def shoots_dir(self) -> Path:
        return self.data_dir / "shoots"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


@lru_cache
def get_settings() -> Settings:
    return Settings()
