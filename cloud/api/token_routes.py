"""Enrollment-token management for a signed-in admin (spec §"Data flow").

All endpoints are scoped to ``current_user.org_id``: an admin can only create,
list, or revoke tokens for their own org. The plaintext token is returned exactly
once, at creation; it is never retrievable again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Form, HTTPException

from cloud.accounts.tokens import issue_token, list_tokens, revoke_token
from cloud.api.auth_routes import current_user
from cloud.persistence import models as m
from cloud.persistence.db import session_scope


def register_token_routes(app: FastAPI) -> None:
    factory = app.state.session_factory

    @app.post("/tokens", status_code=201)
    def create_token(label: str = Form(""), user: m.User = Depends(current_user)) -> dict:
        with session_scope(factory) as session:
            plaintext, row = issue_token(session, org_id=user.org_id, label=label)
            return {"token": plaintext, "id": str(row.id)}

    @app.get("/tokens")
    def get_tokens(user: m.User = Depends(current_user)) -> dict:
        with session_scope(factory) as session:
            rows = list_tokens(session, user.org_id)
            return {
                "tokens": [
                    {
                        "id": str(r.id),
                        "label": r.label,
                        "created_at": r.created_at.isoformat(),
                        "revoked": r.revoked_at is not None,
                    }
                    for r in rows
                ]
            }

    @app.post("/tokens/{token_id}/revoke")
    def revoke(token_id: str, user: m.User = Depends(current_user)) -> dict:
        try:
            parsed_id = uuid.UUID(token_id)
        except ValueError as exc:
            # A malformed id can't name any token; treat it like a missing one.
            raise HTTPException(404, "token not found") from exc
        with session_scope(factory) as session:
            row = session.get(m.EnrollmentToken, parsed_id)
            if row is None or row.org_id != user.org_id:
                raise HTTPException(404, "token not found")
            revoke_token(session, row.id, now=datetime.now(UTC))
        return {"revoked": True}
