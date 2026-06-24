"""Session-cookie lifecycle (spec §"Security and trust").

A session token is a high-entropy random secret stored as the row PK. ``now`` is
injected so expiry is deterministic in tests. Expiry is checked on resolve; expired
rows simply fail to resolve (a sweep job is deferred past Milestone 1).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from cloud.persistence import models as m
from cloud.persistence.repositories import SessionRepository, UserRepository


def create_session(
    session: Session, user_id: uuid.UUID, *, now: datetime, ttl_hours: int
) -> str:
    """Create a new session for a user with the given TTL.

    Args:
        session: SQLAlchemy session for persistence.
        user_id: The user ID to associate with this session.
        now: The current time (injected for deterministic testing).
        ttl_hours: Time-to-live in hours.

    Returns:
        The opaque session token.
    """
    token = secrets.token_urlsafe(32)
    SessionRepository(session).add(token, user_id, now + timedelta(hours=ttl_hours))
    return token


def resolve_session(session: Session, token: str, *, now: datetime) -> m.User | None:
    """Resolve a session token to a user, or None if missing or expired.

    Expiry is checked against the injected ``now`` time. An expired session
    (expires_at <= now) returns None.

    Args:
        session: SQLAlchemy session for persistence.
        token: The session token to resolve.
        now: The current time (injected for deterministic testing).

    Returns:
        The User model if the session is valid, None otherwise.
    """
    row = SessionRepository(session).get(token)
    if row is None:
        return None
    # Ensure expires_at is timezone-aware for comparison (SQLite stores naive; Postgres aware).
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        return None
    return UserRepository(session).get(row.user_id)


def destroy_session(session: Session, token: str) -> None:
    """Destroy a session immediately.

    Args:
        session: SQLAlchemy session for persistence.
        token: The session token to destroy.
    """
    SessionRepository(session).delete(token)
