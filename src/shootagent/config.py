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

    @property
    def shoots_dir(self) -> Path:
        return self.data_dir / "shoots"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


def anthropic_client_kwargs(s: Settings) -> dict:
    """Credential for anthropic.Anthropic. A gateway token (with its base URL) wins when set; otherwise an
    API key, sent to ANTHROPIC_BASE_URL if one is set and to Anthropic's API if not. An empty base URL means
    unset, so a deployment can blank the gateway URL from the environment."""
    # Explicit default: with base_url=None the SDK falls back to the ANTHROPIC_BASE_URL environment
    # variable, and a blanked one yields an empty URL.
    base_url = s.anthropic_base_url or "https://api.anthropic.com"
    if s.anthropic_auth_token:
        return {"auth_token": s.anthropic_auth_token, "base_url": base_url}
    if s.anthropic_api_key:
        return {"api_key": s.anthropic_api_key, "base_url": base_url}
    raise RuntimeError("Set ANTHROPIC_API_KEY, or ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN, in .env.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
