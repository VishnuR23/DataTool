"""Runtime configuration (ARCHITECTURE.md §15).

All controller settings come from ``DATATOOL_``-prefixed environment variables
(optionally a ``.env`` file), loaded once via pydantic-settings. Adapter-specific
secrets that are *not* DataTool's own (``POSTHOG_API_KEY``, ``SLACK_WEBHOOK_URL``,
``ANTHROPIC_API_KEY``, …) are read by their adapters directly from the environment,
keeping this object to the control plane's own configuration.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Control-plane configuration, populated from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="DATATOOL_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://datatool:datatool@localhost:5432/datatool"
    api_key: str | None = None
    log_level: str = "info"
    # Loopback by default: GET endpoints are open unless require_auth is set, so
    # exposing the API beyond this host is an explicit choice (the image sets 0.0.0.0).
    api_host: str = "127.0.0.1"
    tick_interval_seconds: int = 60
    config_dir: str = "./config"
    experiments_dir: str = "./experiments"
    llm_default_model: str = "claude-opus-5-5"
    # Re-run a refused Anthropic request on Anthropic's recommended fallback model,
    # server-side, for models that support it. Off = strict model pinning.
    llm_refusal_fallback: bool = True
    run_live_llm_tests: bool = False
    require_auth: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once and cached."""
    return Settings()
