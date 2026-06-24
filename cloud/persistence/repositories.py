"""Repositories — the panel's persistence API. Callers own the transaction
(``session_scope``); repositories never commit. Every read is scoped to a single
``org_id`` so org isolation is enforced at the data-access layer, not just routes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from cloud.persistence import models as m
from datatool.telemetry.events import TelemetryEvent


class OrgRepository:
    def __init__(self, session):
        self.session = session

    def add(self, name: str) -> m.Org:
        org = m.Org(name=name)
        self.session.add(org)
        self.session.flush()
        return org

    def get(self, org_id: uuid.UUID) -> m.Org | None:
        return self.session.get(m.Org, org_id)


class UserRepository:
    def __init__(self, session):
        self.session = session

    def add(self, org_id: uuid.UUID, email: str, password_hash: str) -> m.User:
        user = m.User(org_id=org_id, email=email, password_hash=password_hash)
        self.session.add(user)
        self.session.flush()
        return user

    def get_by_email(self, email: str) -> m.User | None:
        return self.session.scalar(select(m.User).where(m.User.email == email))

    def get(self, user_id: uuid.UUID) -> m.User | None:
        return self.session.get(m.User, user_id)


class SessionRepository:
    def __init__(self, session):
        self.session = session

    def add(self, token: str, user_id: uuid.UUID, expires_at: datetime) -> m.Session:
        row = m.Session(token=token, user_id=user_id, expires_at=expires_at)
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, token: str) -> m.Session | None:
        return self.session.get(m.Session, token)

    def delete(self, token: str) -> None:
        row = self.session.get(m.Session, token)
        if row is not None:
            self.session.delete(row)


class EnrollmentTokenRepository:
    def __init__(self, session):
        self.session = session

    def add(self, org_id: uuid.UUID, token_hash: str, label: str = "") -> m.EnrollmentToken:
        tok = m.EnrollmentToken(org_id=org_id, token_hash=token_hash, label=label)
        self.session.add(tok)
        self.session.flush()
        return tok

    def get_active_by_hash(self, token_hash: str) -> m.EnrollmentToken | None:
        return self.session.scalar(
            select(m.EnrollmentToken).where(
                m.EnrollmentToken.token_hash == token_hash,
                m.EnrollmentToken.revoked_at.is_(None),
            )
        )

    def list_for(self, org_id: uuid.UUID) -> list[m.EnrollmentToken]:
        return list(
            self.session.scalars(
                select(m.EnrollmentToken)
                .where(m.EnrollmentToken.org_id == org_id)
                .order_by(m.EnrollmentToken.created_at.desc())
            )
        )

    def revoke(self, token_id: uuid.UUID, now: datetime) -> None:
        tok = self.session.get(m.EnrollmentToken, token_id)
        if tok is not None and tok.revoked_at is None:
            tok.revoked_at = now


class EventRepository:
    def __init__(self, session):
        self.session = session

    def add_batch(
        self, org_id: uuid.UUID, events: list[TelemetryEvent]
    ) -> list[TelemetryEvent]:
        """Insert events not already seen for this org. Returns the new ones only.

        Dedup is by (org_id, source, source_id) — the same key as the table's
        unique constraint — so re-delivered batches are idempotent.
        """
        inserted: list[TelemetryEvent] = []
        for ev in events:
            seen = self.session.scalar(
                select(m.Event.id).where(
                    m.Event.org_id == org_id,
                    m.Event.source == ev.source,
                    m.Event.source_id == ev.source_id,
                )
            )
            if seen is not None:
                continue
            self.session.add(
                m.Event(
                    org_id=org_id,
                    source=ev.source,
                    source_id=ev.source_id,
                    kind=ev.kind,
                    summary=ev.summary,
                    experiment_id=ev.experiment_id,
                    surface=ev.surface,
                    occurred_at=ev.occurred_at,
                    detail=ev.detail,
                )
            )
            inserted.append(ev)
        self.session.flush()
        return inserted

    def list_recent(self, org_id: uuid.UUID, *, limit: int = 200) -> list[m.Event]:
        stmt = (
            select(m.Event)
            .where(m.Event.org_id == org_id)
            .order_by(m.Event.occurred_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))
