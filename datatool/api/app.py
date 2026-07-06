"""FastAPI application factory (ARCHITECTURE.md §13).

``create_app`` builds the read-only JSON API, the admin POST endpoints (behind an
API key), and the Prometheus ``/metrics`` exposition — the daemon's programmatic and
ops surface. There is no browser dashboard; the terminal console (``datatool``) is the
human interface. The app is constructed from a session factory and auth config so it
can be created in a test with an in-process database and driven via FastAPI's TestClient.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from datatool.api.routes import register_api_routes


def create_app(
    session_factory: sessionmaker[Session],
    *,
    api_key: str | None = None,
    require_auth: bool = False,
) -> FastAPI:
    """Build the DataTool HTTP app.

    ``api_key`` (from ``DATATOOL_API_KEY``) guards the admin POST endpoints; without
    it they return 503. ``require_auth`` additionally locks the GET endpoints behind
    the same key (``DATATOOL_REQUIRE_AUTH``).
    """
    app = FastAPI(title="DataTool", version="0.1.0")
    app.state.session_factory = session_factory
    app.state.api_key = api_key
    app.state.require_auth = require_auth

    register_api_routes(app)
    return app
