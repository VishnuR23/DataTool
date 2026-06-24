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


@lru_cache
def get_cloud_settings() -> CloudSettings:
    return CloudSettings()
