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
    tick_interval_seconds: int = 60
    config_dir: str = "./config"
    experiments_dir: str = "./experiments"
    llm_default_model: str = "claude-opus-4-7"
    run_live_llm_tests: bool = False
    require_auth: bool = False
    cloud_url: str | None = None
    cloud_token: str | None = None
    cloud_report_interval_seconds: int = 10


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once and cached."""
    return Settings()
