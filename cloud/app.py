"""Panel FastAPI app factory (spec §"Architecture").

Mirrors ``datatool.api.app.create_app``: built from a session factory + config so a
test can drive it with an in-process SQLite database via ``TestClient``. Holds the
``LiveChannels`` instance on app state so ingest (publish) and the SSE endpoint
(subscribe) share one fan-out.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from cloud.api.auth_routes import register_auth_routes
from cloud.api.console_routes import register_console_routes
from cloud.api.ingest_routes import register_ingest_routes
from cloud.api.token_routes import register_token_routes
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels

_STATIC_DIR = Path(__file__).parent / "console" / "static"


def create_app(
    session_factory: sessionmaker[Session],
    *,
    channels: LiveChannels,
    settings: CloudSettings,
) -> FastAPI:
    app = FastAPI(title="DataTool console", version="0.1.0")
    app.state.session_factory = session_factory
    app.state.channels = channels
    app.state.settings = settings

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    register_auth_routes(app)
    register_token_routes(app)
    register_ingest_routes(app)
    register_console_routes(app)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    return app
