"""Console pages + the SSE live stream (spec §"The terminal console").

``/console/stream`` first backfills the most recent stored events (so a freshly
opened console is not blank) and then forwards live events from the org's channel.
The browser's built-in ``EventSource`` reconnect handles transient drops and
periodic idle closes (configurable via ``sse_idle_close_seconds``), picking up
fresh backfill on each reconnect. The stream is org-scoped via ``current_user``;
it never sees another org's channel.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from cloud.api.auth_routes import current_user
from cloud.persistence import models as m
from cloud.persistence.db import session_scope
from cloud.persistence.repositories import EventRepository

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent.parent / "console" / "templates"))


def _event_to_payload(event: m.Event) -> dict[str, Any]:
    return {
        "source": event.source,
        "source_id": event.source_id,
        "kind": event.kind,
        "summary": event.summary,
        "experiment_id": event.experiment_id,
        "surface": event.surface,
        "occurred_at": event.occurred_at.isoformat(),
    }


def register_console_routes(app: FastAPI) -> None:
    factory = app.state.session_factory
    channels = app.state.channels
    settings = app.state.settings

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return _TEMPLATES.TemplateResponse(request, "login.html", {})

    @app.get("/")
    def console_page(request: Request):
        try:
            user = current_user(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return _TEMPLATES.TemplateResponse(request, "console.html", {"org_id": str(user.org_id)})

    @app.get("/console/stream")
    def stream(user: m.User = Depends(current_user)) -> StreamingResponse:
        org_id = user.org_id
        queue = channels.subscribe(org_id)
        idle_close = settings.sse_idle_close_seconds

        with session_scope(factory) as session:
            recent = EventRepository(session).list_recent(org_id, limit=200)
            backfill = [_event_to_payload(e) for e in reversed(recent)]

        async def gen():
            try:
                for payload in backfill:
                    yield f"data: {json.dumps(payload)}\n\n"
                # Forward live events until idle. The browser EventSource reconnects
                # automatically and receives fresh backfill, so closing on idle is safe.
                while True:
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=idle_close)
                        yield f"data: {json.dumps(payload)}\n\n"
                    except TimeoutError:
                        break  # idle close — browser reconnects
            finally:
                channels.unsubscribe(org_id, queue)

        return StreamingResponse(gen(), media_type="text/event-stream")
