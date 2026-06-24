"""Token-authenticated ingest + a connect-check endpoint (spec §"Live streaming").

The agent presents its enrollment token as a bearer credential. The token resolves
to exactly one org; every stored event and every live-channel publish is scoped to
that org (isolation hard gate). Newly-inserted events are published to the org's
live channel so open consoles update in real time.

``/agent/connect`` only confirms a token resolves — it returns the org name and
nothing else. It is deliberately NOT a downward control path (spec invariant 2).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from cloud.accounts.tokens import resolve_token
from cloud.ingest.service import UnsupportedSchemaError, ingest_batch
from cloud.persistence import models as m
from cloud.persistence.db import session_scope
from datatool.telemetry.events import TelemetryBatch


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    return header[7:].strip()


def register_ingest_routes(app: FastAPI) -> None:
    factory = app.state.session_factory
    channels = app.state.channels

    @app.post("/ingest")
    async def ingest(request: Request) -> dict:
        token = _bearer(request)
        body = await request.json()
        batch = TelemetryBatch.model_validate(body)
        with session_scope(factory) as session:
            org = resolve_token(session, token)
            if org is None:
                raise HTTPException(401, "invalid or revoked token")
            org_id = org.id
            try:
                new_events = ingest_batch(session, org_id, batch)
            except UnsupportedSchemaError as exc:
                raise HTTPException(422, str(exc)) from exc
            # Serialize inside the session scope; publish after commit-safe data is built.
            payloads = [e.model_dump(mode="json") for e in new_events]
        for payload in payloads:
            channels.publish(org_id, payload)
        return {"accepted": len(payloads)}

    @app.post("/agent/connect")
    def agent_connect(request: Request) -> dict:
        token = _bearer(request)
        with session_scope(factory) as session:
            org = resolve_token(session, token)
            if org is None:
                raise HTTPException(401, "invalid or revoked token")
            return {"org": org.name}
