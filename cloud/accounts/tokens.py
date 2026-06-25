"""Enrollment tokens (spec § Data flow → Enrollment).

A token is a high-entropy secret shown to the customer exactly once; the panel
stores only its SHA-256 hash and resolves a presented token by re-hashing and
looking up the active row. Tokens are org-scoped and revocable.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from cloud.persistence import models as m
from cloud.persistence.repositories import EnrollmentTokenRepository, OrgRepository


def hash_token(plaintext: str) -> str:
    """Hash a token plaintext to its SHA-256 hex representation for storage."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def issue_token(
    session: Session, *, org_id: uuid.UUID, label: str = ""
) -> tuple[str, m.EnrollmentToken]:
    """Issue a new enrollment token for an org.

    Returns the plaintext token (shown to the customer exactly once) and the
    stored row (with hashed token). The plaintext is never stored.
    """
    plaintext = secrets.token_urlsafe(32)
    row = EnrollmentTokenRepository(session).add(org_id, hash_token(plaintext), label)
    return plaintext, row


def resolve_token(session: Session, plaintext: str) -> m.Org | None:
    """Resolve a presented token to its owning org, or None if invalid/revoked.

    Re-hashes the plaintext and looks up the active (non-revoked) row.
    """
    row = EnrollmentTokenRepository(session).get_active_by_hash(hash_token(plaintext))
    if row is None:
        return None
    return OrgRepository(session).get(row.org_id)


def revoke_token(session: Session, token_id: uuid.UUID, *, now: datetime) -> None:
    """Revoke a token, marking it inactive for future resolution."""
    EnrollmentTokenRepository(session).revoke(token_id, now)


def list_tokens(session: Session, org_id: uuid.UUID) -> list[m.EnrollmentToken]:
    """List all tokens (active and revoked) for an org, newest first."""
    return EnrollmentTokenRepository(session).list_for(org_id)
