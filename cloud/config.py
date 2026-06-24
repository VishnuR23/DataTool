"""Panel configuration (spec §"Repository layout"). Separate from the agent's
``datatool.config.Settings``: different env prefix, different database."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class CloudSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATATOOL_CLOUD_", env_file=".env", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://datatool:datatool@localhost:5432/datatool_cloud"
    session_ttl_hours: int = 720  # 30 days
    cookie_secure: bool = False  # set True when served over HTTPS in production
    # DECISION: the SSE stream closes after this many seconds with no live event,
    # and the browser EventSource reconnects (re-running the backfill). A finite
    # generator is what makes the stream terminate cleanly under Starlette's
    # TestClient; the console dedups backfill by source_id, so reconnects never
    # duplicate lines. 25s keeps the connection fresh under common 30-60s proxy
    # idle timeouts without churning; override via DATATOOL_CLOUD_SSE_IDLE_CLOSE_SECONDS.
    sse_idle_close_seconds: int = 25


@lru_cache
def get_cloud_settings() -> CloudSettings:
    return CloudSettings()
