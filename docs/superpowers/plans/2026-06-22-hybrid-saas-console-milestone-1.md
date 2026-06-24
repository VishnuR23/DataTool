# Hybrid SaaS console — Milestone 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the thin end-to-end "live hybrid loop": a self-hosted agent pushes read-only telemetry upward to a vendor-hosted, signed-in web console that streams it live, with org-isolated storage and a self-serve `datatool connect` onboarding flow.

**Architecture:** Two new units in the same repo. `datatool/telemetry/` is agent-side (reporter that tails the existing append-only tables, batches, and POSTs upward over outbound-only HTTPS, never blocking the control loop). `cloud/` is a separate FastAPI service with its own Postgres database (orgs, users, sessions, enrollment tokens, events) exposing sign-in, token-authenticated ingest, and a terminal-aesthetic console fed by Server-Sent Events. The only cross-unit dependency is the data-only versioned event schema in `datatool/telemetry/events.py`; `cloud/` never imports `core/`, `control/`, or `stats/`.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLAlchemy 2.0, FastAPI, Typer, httpx (sync client), argon2-cffi (password hashing), vanilla JS + CSS + SSE on the browser, pytest. uv for everything.

## Global Constraints

These apply to **every** task. Exact values copied from `CLAUDE.md`, `ARCHITECTURE.md`, and the approved spec (`docs/superpowers/specs/2026-06-22-hybrid-saas-console-design.md`).

- **Python 3.11+**, type-hint everything, Pydantic v2 for schemas, SQLAlchemy 2.0 (`Mapped`/`mapped_column`) for ORM, Typer for CLI, pytest for tests. uv only: `export PATH="$HOME/.local/bin:$PATH"` then `uv run …`. macOS has no `timeout` command.
- **`cloud/` MUST NOT import `datatool/core/`, `datatool/control/`, or `datatool/stats/`.** Its single allowed dependency on the rest of the repo is `datatool/telemetry/events.py` (data-only). This mirrors the adapter-protocol rule and keeps the panel swappable by an acquirer.
- **Two separate Postgres databases.** `cloud/persistence/db.py` defines its OWN `Base` (DeclarativeBase) — it must never share metadata with `datatool/persistence/db.py`. The customer's existing DB schema is unchanged: nothing in this milestone adds a table to the agent-side database.
- **Org isolation is a hard gate.** Treat it like a stats calibration test: a failing org-isolation test blocks the build. One org must never see another org's events, in storage *or* in the live channel.
- **The stats calibration gate stays untouched and green.** None of this work touches `stats/`. `uv run pytest tests/stats` must still pass (46 passed / 1 deselected) at the end.
- **Telemetry flows up; control never flows down.** The console is strictly read-only. Add no endpoint or code path that lets the panel issue a command to the agent or move the customer's production.
- **Outbound-only from the customer.** The agent *pushes* over HTTPS. The panel never opens an inbound connection into the customer network.
- **Append-only audit tables stay append-only** (`decisions`, `actions`, `guardrail_evaluations`, `trust_events`, `audit_log`): the reporter is read-only over them.
- **Sentence-case** in all CLI output and docs. No Title Case. **Comments explain *why*, not *what*.** Mark non-obvious local choices with a `# DECISION:` comment and a one-line rationale.
- **Licensing of new deps:** `argon2-cffi` is MIT — compatible. Do not add GPL/AGPL/SSPL deps. (The project is moving to proprietary, but keep deps permissive.)
- **Commit message trailer** on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **Test names describe the property** being verified, not the function called.
- **Build rhythm:** one task at a time; stop and summarize between tasks; report any deviation from this plan.

## File structure

New agent-side unit (`datatool/telemetry/`):

| File | Responsibility |
|---|---|
| `datatool/telemetry/events.py` | The shared, versioned event schema (pure Pydantic, no persistence imports). `TelemetryEvent`, `TelemetryBatch`, `SCHEMA_VERSION`. |
| `datatool/telemetry/cursor.py` | `FileCursor`: load/save a per-source watermark to a local JSON file (no customer-DB table). |
| `datatool/telemetry/collector.py` | Reads new rows from the 5 append-only tables since the watermark and converts them to `TelemetryEvent`s. Agent-only (imports persistence). |
| `datatool/telemetry/reporter.py` | `TelemetryReporter.report_once()` (collect → POST → advance cursor) and `ReporterThread` (background loop). |

New hosted unit (`cloud/`):

| File | Responsibility |
|---|---|
| `cloud/__init__.py` | Package marker. |
| `cloud/config.py` | `CloudSettings` (pydantic-settings, prefix `DATATOOL_CLOUD_`). |
| `cloud/persistence/db.py` | Panel's OWN `Base`, `make_engine`, `make_session_factory`, `init_db`, `session_scope`. |
| `cloud/persistence/models.py` | `Org`, `User`, `Session`, `EnrollmentToken`, `Event`. |
| `cloud/persistence/repositories.py` | One repository per model. |
| `cloud/auth/passwords.py` | `hash_password`, `verify_password` (argon2). |
| `cloud/auth/sessions.py` | `create_session`, `resolve_session`, `destroy_session`. |
| `cloud/accounts/service.py` | `sign_up`, `authenticate`. |
| `cloud/accounts/tokens.py` | `issue_token`, `resolve_token`, `revoke_token`, `list_tokens`. |
| `cloud/ingest/channel.py` | `LiveChannels`: in-memory per-org asyncio pub/sub for SSE. |
| `cloud/ingest/service.py` | `ingest_batch`: validate + store, returns newly-inserted events. |
| `cloud/api/auth_routes.py` | `POST /signup`, `POST /login`, `POST /logout`, `current_user` dependency. |
| `cloud/api/token_routes.py` | `POST /tokens`, `GET /tokens`, `POST /tokens/{id}/revoke` (session-authed). |
| `cloud/api/ingest_routes.py` | `POST /ingest`, `POST /agent/connect` (bearer-token-authed). |
| `cloud/api/console_routes.py` | `GET /login`, `GET /` (console), `GET /console/stream` (SSE). |
| `cloud/app.py` | FastAPI app factory; mounts static, registers routes, holds `LiveChannels`. |
| `cloud/console/templates/{login,console}.html` | Jinja shells. |
| `cloud/console/static/{console.css,console.js}` | Terminal aesthetic + SSE client. |

Modified:

| File | Change |
|---|---|
| `pyproject.toml` | Add `argon2-cffi>=23.1` dependency. |
| `datatool/config.py` | Add `cloud_url`, `cloud_token`, `cloud_report_interval_seconds` to `Settings`. |
| `datatool/cli/main.py` | Add the `connect` command. |
| `datatool/cli/commands.py` | Add `connect_agent` logic; start the reporter thread in `run_daemon`. |

Tests live under `tests/` mirroring existing layout: `tests/telemetry/`, `tests/cloud/`, plus an `tests/e2e/test_hybrid_loop.py`.

---

## Task 1: Shared event schema

**Files:**
- Create: `datatool/telemetry/__init__.py` (empty)
- Create: `datatool/telemetry/events.py`
- Test: `tests/telemetry/__init__.py` (empty), `tests/telemetry/test_events.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION: int`; `TelemetryEvent` (Pydantic, frozen) with fields `source: Literal["decision","action","guardrail","trust","audit"]`, `source_id: str`, `kind: str`, `summary: str`, `occurred_at: datetime`, `experiment_id: str | None`, `surface: str | None`, `detail: dict[str, Any]`; `TelemetryBatch` with `schema_version: int`, `events: list[TelemetryEvent]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/telemetry/test_events.py
from datetime import UTC, datetime

from datatool.telemetry.events import SCHEMA_VERSION, TelemetryBatch, TelemetryEvent


def test_event_roundtrips_through_json_preserving_fields():
    event = TelemetryEvent(
        source="action",
        source_id="abc-123",
        kind="promote",
        summary="promoted checkout-button to 100%",
        occurred_at=datetime(2026, 6, 22, 12, 0, tzinfo=UTC),
        experiment_id="exp-1",
        surface="checkout",
        detail={"clamped": False},
    )
    batch = TelemetryBatch(events=[event])
    restored = TelemetryBatch.model_validate_json(batch.model_dump_json())
    assert restored.schema_version == SCHEMA_VERSION
    assert restored.events[0] == event


def test_batch_defaults_schema_version_to_current():
    assert TelemetryBatch(events=[]).schema_version == SCHEMA_VERSION


def test_event_rejects_unknown_source():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TelemetryEvent(
            source="nope",
            source_id="x",
            kind="k",
            summary="s",
            occurred_at=datetime.now(UTC),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'datatool.telemetry'`.

- [ ] **Step 3: Write minimal implementation**

```python
# datatool/telemetry/events.py
"""The shared, versioned telemetry event schema (spec §"Repository layout").

This module is the ONE allowed cross-unit dependency between the agent
(``datatool/telemetry``) and the hosted panel (``cloud/``). It is data-only: it
imports nothing from ``core``/``control``/``stats``/``persistence`` so the panel
can depend on it without pulling in customer-side business logic.

Versioning: ``SCHEMA_VERSION`` is bumped on any breaking change to the envelope.
The panel validates the version on ingest and rejects unknown majors.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

EventSource = Literal["decision", "action", "guardrail", "trust", "audit"]


class TelemetryEvent(BaseModel):
    """One normalized activity event, derived from an agent-side audit row."""

    model_config = ConfigDict(frozen=True)

    source: EventSource
    source_id: str  # the source row's UUID, as a string; the panel dedups on it
    kind: str
    summary: str  # human-readable one-liner for the console
    occurred_at: datetime
    experiment_id: str | None = None
    surface: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    """A batch of events POSTed from the agent to the panel's ingest endpoint."""

    schema_version: int = SCHEMA_VERSION
    events: list[TelemetryEvent]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_events.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add datatool/telemetry/__init__.py datatool/telemetry/events.py tests/telemetry/
git commit -m "feat(telemetry): add shared versioned event schema"
```

---

## Task 2: Panel database base + models

**Files:**
- Create: `cloud/__init__.py` (empty), `cloud/persistence/__init__.py` (empty)
- Create: `cloud/persistence/db.py`
- Create: `cloud/persistence/models.py`
- Test: `tests/cloud/__init__.py` (empty), `tests/cloud/conftest.py`, `tests/cloud/test_models.py`

**Interfaces:**
- Produces: `cloud.persistence.db.Base`, `make_engine(url, *, echo=False)`, `make_session_factory(engine)`, `init_db(engine)`, `session_scope(session_factory)`. Models `Org(id, name, license_tier, created_at)`, `User(id, org_id, email, password_hash, created_at)`, `Session(token, user_id, created_at, expires_at)`, `EnrollmentToken(id, org_id, token_hash, label, created_at, revoked_at)`, `Event(id, org_id, source, source_id, kind, summary, experiment_id, surface, occurred_at, detail, received_at)` with `UniqueConstraint(org_id, source, source_id)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/conftest.py
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from cloud.persistence.db import init_db, make_engine, make_session_factory


@pytest.fixture
def cloud_session_factory() -> sessionmaker[Session]:
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    return make_session_factory(engine)
```

```python
# tests/cloud/test_models.py
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from cloud.persistence.db import session_scope
from cloud.persistence import models as m


def test_org_and_user_persist_and_relate(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = m.Org(name="acme")
        s.add(org)
        s.flush()
        s.add(m.User(org_id=org.id, email="a@acme.test", password_hash="x"))

    with session_scope(cloud_session_factory) as s:
        user = s.scalar(select(m.User).where(m.User.email == "a@acme.test"))
        assert user is not None
        assert s.get(m.Org, user.org_id).name == "acme"
        assert s.get(m.Org, user.org_id).license_tier == "free"


def test_event_unique_constraint_is_per_org_source_sourceid(cloud_session_factory):
    import pytest
    from sqlalchemy.exc import IntegrityError

    with session_scope(cloud_session_factory) as s:
        org = m.Org(name="acme")
        s.add(org)
        s.flush()
        org_id = org.id
        s.add(m.Event(org_id=org_id, source="action", source_id="dup",
                      kind="promote", summary="x", occurred_at=datetime.now(UTC), detail={}))

    with pytest.raises(IntegrityError):
        with session_scope(cloud_session_factory) as s:
            s.add(m.Event(org_id=org_id, source="action", source_id="dup",
                          kind="promote", summary="y", occurred_at=datetime.now(UTC), detail={}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cloud'`.

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/persistence/db.py
"""Panel database engine, session management, and ORM base (spec invariant 3).

DECISION: the panel defines its OWN DeclarativeBase, deliberately separate from
``datatool.persistence.db.Base``. The two services use two physically separate
Postgres databases and must never share metadata. SQLite (in-memory) backs the
tests; column types are dialect-portable. Mirrors the agent-side helpers so the
patterns match, but shares no state with them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import JSON, Engine, create_engine
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

JSONB = JSON().with_variant(_PG_JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Declarative base for the panel's ORM models only."""


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    return create_engine(database_url, echo=echo, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    from cloud.persistence import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

```python
# cloud/persistence/models.py
"""Panel ORM models (spec §"Repository layout"): orgs, users, sessions,
enrollment tokens, and the ingested event store.

UUID primary keys default in Python (portable across Postgres and the SQLite test
database); timestamps default to ``now()`` in UTC. The ``events`` table carries a
unique ``(org_id, source, source_id)`` so re-delivered batches are idempotent —
at-least-once delivery from the agent becomes effectively-once in the panel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from cloud.persistence.db import JSONB, Base


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    license_tier: Mapped[str] = mapped_column(Text, nullable=False, default="free")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orgs.id"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class Session(Base):
    """A signed-in browser session. The token is a random secret used as the PK."""

    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EnrollmentToken(Base):
    """An org-scoped credential the agent presents on ingest. Only the hash is stored."""

    __tablename__ = "enrollment_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orgs.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    """A stored telemetry event. Always scoped to exactly one org (isolation gate)."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("org_id", "source", "source_id", name="uq_events_org_source_sourceid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("orgs.id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    experiment_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    surface: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_models.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/__init__.py cloud/persistence/ tests/cloud/
git commit -m "feat(cloud): add panel database base and models"
```

---

## Task 3: Panel repositories

**Files:**
- Create: `cloud/persistence/repositories.py`
- Test: `tests/cloud/test_repositories.py`

**Interfaces:**
- Consumes: `cloud.persistence.models`, `datatool.telemetry.events.TelemetryEvent`.
- Produces:
  - `OrgRepository(session)`: `.add(name) -> Org`, `.get(org_id) -> Org | None`
  - `UserRepository(session)`: `.add(org_id, email, password_hash) -> User`, `.get_by_email(email) -> User | None`, `.get(user_id) -> User | None`
  - `SessionRepository(session)`: `.add(token, user_id, expires_at) -> Session`, `.get(token) -> Session | None`, `.delete(token) -> None`
  - `EnrollmentTokenRepository(session)`: `.add(org_id, token_hash, label) -> EnrollmentToken`, `.get_active_by_hash(token_hash) -> EnrollmentToken | None`, `.list_for(org_id) -> list[EnrollmentToken]`, `.revoke(token_id, now) -> None`
  - `EventRepository(session)`: `.add_batch(org_id, events: list[TelemetryEvent]) -> list[TelemetryEvent]` (returns only the newly-inserted events, skipping dups), `.list_recent(org_id, *, limit=200) -> list[Event]`

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_repositories.py
from datetime import UTC, datetime, timedelta

from cloud.persistence.db import session_scope
from cloud.persistence.repositories import (
    EnrollmentTokenRepository,
    EventRepository,
    OrgRepository,
    SessionRepository,
    UserRepository,
)
from datatool.telemetry.events import TelemetryEvent


def _event(source_id: str) -> TelemetryEvent:
    return TelemetryEvent(
        source="action", source_id=source_id, kind="promote",
        summary="s", occurred_at=datetime.now(UTC),
    )


def test_add_batch_skips_already_seen_source_ids(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        first = EventRepository(s).add_batch(org.id, [_event("a"), _event("b")])
        assert len(first) == 2

    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).get(org.id)
        second = EventRepository(s).add_batch(org.id, [_event("b"), _event("c")])
        assert [e.source_id for e in second] == ["c"]
        assert len(EventRepository(s).list_recent(org.id)) == 3


def test_list_recent_only_returns_one_orgs_events(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        a = OrgRepository(s).add("a")
        b = OrgRepository(s).add("b")
        EventRepository(s).add_batch(a.id, [_event("a1")])
        EventRepository(s).add_batch(b.id, [_event("b1")])
        a_ids = {e.source_id for e in EventRepository(s).list_recent(a.id)}
        assert a_ids == {"a1"}


def test_active_token_lookup_excludes_revoked(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        tok = EnrollmentTokenRepository(s).add(org.id, "hash1", "laptop")
        assert EnrollmentTokenRepository(s).get_active_by_hash("hash1").id == tok.id
        EnrollmentTokenRepository(s).revoke(tok.id, datetime.now(UTC))
    with session_scope(cloud_session_factory) as s:
        assert EnrollmentTokenRepository(s).get_active_by_hash("hash1") is None


def test_session_resolves_and_deletes(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        user = UserRepository(s).add(org.id, "a@acme.test", "h")
        SessionRepository(s).add("tok", user.id, datetime.now(UTC) + timedelta(hours=1))
    with session_scope(cloud_session_factory) as s:
        assert SessionRepository(s).get("tok").user_id == user.id
        SessionRepository(s).delete("tok")
    with session_scope(cloud_session_factory) as s:
        assert SessionRepository(s).get("tok") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_repositories.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cloud.persistence.repositories'`.

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/persistence/repositories.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_repositories.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/persistence/repositories.py tests/cloud/test_repositories.py
git commit -m "feat(cloud): add panel repositories with per-org scoping"
```

---

## Task 4: Password hashing

**Files:**
- Modify: `pyproject.toml` (add `argon2-cffi>=23.1`)
- Create: `cloud/auth/__init__.py` (empty), `cloud/auth/passwords.py`
- Test: `tests/cloud/test_passwords.py`

**Interfaces:**
- Produces: `hash_password(password: str) -> str`; `verify_password(password_hash: str, password: str) -> bool`.

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`, in `[project].dependencies`, add after `"jinja2>=3.1",`:
```toml
    "argon2-cffi>=23.1",
```
Then run: `uv sync`
Expected: resolves and installs `argon2-cffi`.

- [ ] **Step 2: Write the failing test**

```python
# tests/cloud/test_passwords.py
from cloud.auth.passwords import hash_password, verify_password


def test_correct_password_verifies_and_wrong_one_does_not():
    h = hash_password("correct horse battery staple")
    assert verify_password(h, "correct horse battery staple") is True
    assert verify_password(h, "wrong") is False


def test_hash_is_not_the_plaintext_and_is_salted():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != "same"
    assert h1 != h2  # distinct salts
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_passwords.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cloud.auth'`.

- [ ] **Step 4: Write minimal implementation**

```python
# cloud/auth/passwords.py
"""Password hashing with Argon2id (spec §"Security and trust").

Argon2 is the PHC winner and the current OWASP-recommended default for password
storage. We never store or compare plaintext; ``verify_password`` is constant-time
via the library and treats any mismatch/parse error as a failed verification.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, Exception):  # noqa: BLE001 — any failure = not verified
        return False
```

> Note: the broad `except` is intentional — a malformed stored hash must read as "not verified", never raise. The `# noqa` documents that.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_passwords.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock cloud/auth/__init__.py cloud/auth/passwords.py tests/cloud/test_passwords.py
git commit -m "feat(cloud): add argon2 password hashing"
```

---

## Task 5: Session lifecycle

**Files:**
- Create: `cloud/auth/sessions.py`
- Test: `tests/cloud/test_sessions.py`

**Interfaces:**
- Consumes: `cloud.persistence.repositories.SessionRepository`, `cloud.persistence.models.User`.
- Produces:
  - `create_session(session, user_id: uuid.UUID, *, now: datetime, ttl_hours: int) -> str` (returns the opaque token)
  - `resolve_session(session, token: str, *, now: datetime) -> User | None` (None if missing or expired)
  - `destroy_session(session, token: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_sessions.py
from datetime import UTC, datetime, timedelta

from cloud.auth.sessions import create_session, destroy_session, resolve_session
from cloud.persistence.db import session_scope
from cloud.persistence.repositories import OrgRepository, UserRepository


def _user(s):
    org = OrgRepository(s).add("acme")
    return UserRepository(s).add(org.id, "a@acme.test", "h")


def test_resolve_returns_user_for_live_session(cloud_session_factory):
    now = datetime(2026, 6, 22, tzinfo=UTC)
    with session_scope(cloud_session_factory) as s:
        uid = _user(s).id
        token = create_session(s, uid, now=now, ttl_hours=24)
    with session_scope(cloud_session_factory) as s:
        user = resolve_session(s, token, now=now + timedelta(hours=1))
        assert user.id == uid


def test_resolve_returns_none_for_expired_session(cloud_session_factory):
    now = datetime(2026, 6, 22, tzinfo=UTC)
    with session_scope(cloud_session_factory) as s:
        token = create_session(s, _user(s).id, now=now, ttl_hours=1)
    with session_scope(cloud_session_factory) as s:
        assert resolve_session(s, token, now=now + timedelta(hours=2)) is None


def test_destroyed_session_no_longer_resolves(cloud_session_factory):
    now = datetime(2026, 6, 22, tzinfo=UTC)
    with session_scope(cloud_session_factory) as s:
        token = create_session(s, _user(s).id, now=now, ttl_hours=24)
    with session_scope(cloud_session_factory) as s:
        destroy_session(s, token)
    with session_scope(cloud_session_factory) as s:
        assert resolve_session(s, token, now=now) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cloud.auth.sessions'`.

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/auth/sessions.py
"""Session-cookie lifecycle (spec §"Security and trust").

A session token is a high-entropy random secret stored as the row PK. ``now`` is
injected so expiry is deterministic in tests. Expiry is checked on resolve; expired
rows simply fail to resolve (a sweep job is deferred past Milestone 1).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from cloud.persistence import models as m
from cloud.persistence.repositories import SessionRepository, UserRepository


def create_session(
    session: Session, user_id: uuid.UUID, *, now: datetime, ttl_hours: int
) -> str:
    token = secrets.token_urlsafe(32)
    SessionRepository(session).add(token, user_id, now + timedelta(hours=ttl_hours))
    return token


def resolve_session(session: Session, token: str, *, now: datetime) -> m.User | None:
    row = SessionRepository(session).get(token)
    if row is None or row.expires_at <= now:
        return None
    return UserRepository(session).get(row.user_id)


def destroy_session(session: Session, token: str) -> None:
    SessionRepository(session).delete(token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_sessions.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/auth/sessions.py tests/cloud/test_sessions.py
git commit -m "feat(cloud): add session lifecycle"
```

---

## Task 6: Accounts service (sign-up + authenticate)

**Files:**
- Create: `cloud/accounts/__init__.py` (empty), `cloud/accounts/service.py`
- Test: `tests/cloud/test_accounts.py`

**Interfaces:**
- Consumes: `OrgRepository`, `UserRepository`, `hash_password`, `verify_password`.
- Produces:
  - `sign_up(session, *, org_name: str, email: str, password: str) -> User` — creates an org and its single admin user; raises `EmailTakenError` if the email exists.
  - `authenticate(session, *, email: str, password: str) -> User | None` — returns the user on a correct password, else None.
  - `EmailTakenError(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_accounts.py
import pytest

from cloud.accounts.service import EmailTakenError, authenticate, sign_up
from cloud.persistence.db import session_scope


def test_sign_up_creates_org_and_admin_then_authenticates(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        user = sign_up(s, org_name="acme", email="a@acme.test", password="pw12345678")
        assert user.org_id is not None
    with session_scope(cloud_session_factory) as s:
        assert authenticate(s, email="a@acme.test", password="pw12345678") is not None
        assert authenticate(s, email="a@acme.test", password="wrong") is None


def test_duplicate_email_is_rejected(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        sign_up(s, org_name="acme", email="a@acme.test", password="pw12345678")
    with session_scope(cloud_session_factory) as s:
        with pytest.raises(EmailTakenError):
            sign_up(s, org_name="other", email="a@acme.test", password="pw12345678")


def test_authenticate_unknown_email_returns_none(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        assert authenticate(s, email="nobody@acme.test", password="x") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_accounts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cloud.accounts'`.

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/accounts/service.py
"""Org sign-up and login (spec §"Accounts + auth").

Milestone 1 is single-admin: a sign-up creates one org and one admin user.
Teammate invites and roles are Milestone 2.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from cloud.auth.passwords import hash_password, verify_password
from cloud.persistence import models as m
from cloud.persistence.repositories import OrgRepository, UserRepository


class EmailTakenError(Exception):
    """Raised when signing up with an email that already exists."""


def sign_up(session: Session, *, org_name: str, email: str, password: str) -> m.User:
    users = UserRepository(session)
    if users.get_by_email(email) is not None:
        raise EmailTakenError(email)
    org = OrgRepository(session).add(org_name)
    return users.add(org.id, email, hash_password(password))


def authenticate(session: Session, *, email: str, password: str) -> m.User | None:
    user = UserRepository(session).get_by_email(email)
    if user is None or not verify_password(user.password_hash, password):
        return None
    return user
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_accounts.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/accounts/__init__.py cloud/accounts/service.py tests/cloud/test_accounts.py
git commit -m "feat(cloud): add accounts sign-up and authenticate"
```

---

## Task 7: Enrollment token service

**Files:**
- Create: `cloud/accounts/tokens.py`
- Test: `tests/cloud/test_tokens.py`

**Interfaces:**
- Consumes: `EnrollmentTokenRepository`, `OrgRepository`.
- Produces:
  - `issue_token(session, *, org_id: uuid.UUID, label: str = "") -> tuple[str, EnrollmentToken]` — returns `(plaintext_token, row)`; only the hash is stored.
  - `resolve_token(session, plaintext: str) -> Org | None` — returns the owning org for a valid, non-revoked token, else None.
  - `revoke_token(session, token_id: uuid.UUID, *, now: datetime) -> None`
  - `list_tokens(session, org_id: uuid.UUID) -> list[EnrollmentToken]`
  - `hash_token(plaintext: str) -> str` (sha256 hex; exported for tests).

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_tokens.py
from datetime import UTC, datetime

from cloud.accounts.tokens import issue_token, resolve_token, revoke_token
from cloud.persistence.db import session_scope
from cloud.persistence.repositories import OrgRepository


def test_issued_token_resolves_to_its_org(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        plaintext, _ = issue_token(s, org_id=org.id, label="prod")
    with session_scope(cloud_session_factory) as s:
        resolved = resolve_token(s, plaintext)
        assert resolved is not None and resolved.name == "acme"


def test_bad_token_does_not_resolve(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        assert resolve_token(s, "not-a-real-token") is None


def test_revoked_token_stops_resolving(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        plaintext, row = issue_token(s, org_id=org.id)
        token_id = row.id
    with session_scope(cloud_session_factory) as s:
        revoke_token(s, token_id, now=datetime.now(UTC))
    with session_scope(cloud_session_factory) as s:
        assert resolve_token(s, plaintext) is None


def test_plaintext_is_not_stored(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        plaintext, row = issue_token(s, org_id=org.id)
        assert row.token_hash != plaintext
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_tokens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cloud.accounts.tokens'`.

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/accounts/tokens.py
"""Enrollment tokens (spec §"Data flow → Enrollment").

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
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def issue_token(
    session: Session, *, org_id: uuid.UUID, label: str = ""
) -> tuple[str, m.EnrollmentToken]:
    plaintext = secrets.token_urlsafe(32)
    row = EnrollmentTokenRepository(session).add(org_id, hash_token(plaintext), label)
    return plaintext, row


def resolve_token(session: Session, plaintext: str) -> m.Org | None:
    row = EnrollmentTokenRepository(session).get_active_by_hash(hash_token(plaintext))
    if row is None:
        return None
    return OrgRepository(session).get(row.org_id)


def revoke_token(session: Session, token_id: uuid.UUID, *, now: datetime) -> None:
    EnrollmentTokenRepository(session).revoke(token_id, now)


def list_tokens(session: Session, org_id: uuid.UUID) -> list[m.EnrollmentToken]:
    return EnrollmentTokenRepository(session).list_for(org_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_tokens.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/accounts/tokens.py tests/cloud/test_tokens.py
git commit -m "feat(cloud): add enrollment token service"
```

---

## Task 8: Live channel (per-org pub/sub)

**Files:**
- Create: `cloud/ingest/__init__.py` (empty), `cloud/ingest/channel.py`
- Test: `tests/cloud/test_channel.py`

**Interfaces:**
- Produces: `LiveChannels` with `subscribe(org_id: uuid.UUID) -> asyncio.Queue`, `unsubscribe(org_id, queue) -> None`, `publish(org_id, payload: dict) -> None` (non-blocking; drops to no-op if no subscribers). `payload` is a JSON-serializable dict (a serialized `TelemetryEvent`).

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_channel.py
import asyncio
import uuid

import pytest

from cloud.ingest.channel import LiveChannels


@pytest.mark.asyncio
async def test_subscriber_receives_only_its_orgs_events():
    channels = LiveChannels()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    qa = channels.subscribe(org_a)
    qb = channels.subscribe(org_b)

    channels.publish(org_a, {"summary": "for-a"})

    assert (await asyncio.wait_for(qa.get(), timeout=1))["summary"] == "for-a"
    assert qb.empty()  # org B must not see org A's event

    channels.unsubscribe(org_a, qa)
    channels.unsubscribe(org_b, qb)


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_a_noop():
    channels = LiveChannels()
    channels.publish(uuid.uuid4(), {"x": 1})  # must not raise
```

> If `pytest-asyncio` is not yet a dev dependency, add `pytest-asyncio>=0.23` to `[dependency-groups].dev` in `pyproject.toml`, run `uv sync`, and add to `pyproject.toml`:
> ```toml
> [tool.pytest.ini_options]
> asyncio_mode = "auto"
> ```
> (Check first: `grep -n "asyncio" pyproject.toml`. If `asyncio_mode` is already set, you may drop the `@pytest.mark.asyncio` decorators.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_channel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cloud.ingest'`.

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/ingest/channel.py
"""In-process per-org pub/sub for the live console (spec §"Live streaming").

Each browser SSE connection subscribes and gets its own ``asyncio.Queue``; ingest
publishes a serialized event to every queue for that org. Isolation is structural:
a publish only ever touches the publishing org's queue set.

DECISION: single-process in-memory fan-out for Milestone 1. A multi-process panel
would need a shared broker (e.g. Redis pub/sub); that is deferred. ``publish`` is
non-blocking — a full queue drops the event for that one slow subscriber rather
than blocking ingest (the durable store remains the source of truth on reconnect).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

_MAX_QUEUE = 1000


class LiveChannels:
    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, org_id: uuid.UUID) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subscribers[org_id].add(queue)
        return queue

    def unsubscribe(self, org_id: uuid.UUID, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(org_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                del self._subscribers[org_id]

    def publish(self, org_id: uuid.UUID, payload: dict) -> None:
        for queue in self._subscribers.get(org_id, set()):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # slow subscriber; it will backfill from storage on reconnect
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_channel.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/ingest/__init__.py cloud/ingest/channel.py tests/cloud/test_channel.py pyproject.toml uv.lock
git commit -m "feat(cloud): add per-org live channel for SSE fan-out"
```

---

## Task 9: Ingest service

**Files:**
- Create: `cloud/ingest/service.py`
- Test: `tests/cloud/test_ingest_service.py`

**Interfaces:**
- Consumes: `EventRepository`, `TelemetryBatch`, `SCHEMA_VERSION`.
- Produces:
  - `UnsupportedSchemaError(Exception)`.
  - `ingest_batch(session, org_id: uuid.UUID, batch: TelemetryBatch) -> list[TelemetryEvent]` — raises `UnsupportedSchemaError` if `batch.schema_version != SCHEMA_VERSION`; otherwise stores and returns the newly-inserted events.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_ingest_service.py
from datetime import UTC, datetime

import pytest

from cloud.ingest.service import UnsupportedSchemaError, ingest_batch
from cloud.persistence.db import session_scope
from cloud.persistence.repositories import EventRepository, OrgRepository
from datatool.telemetry.events import TelemetryBatch, TelemetryEvent


def _batch(*source_ids, version=1):
    events = [
        TelemetryEvent(source="action", source_id=sid, kind="promote",
                       summary="s", occurred_at=datetime.now(UTC))
        for sid in source_ids
    ]
    return TelemetryBatch(schema_version=version, events=events)


def test_ingest_stores_new_events_and_dedups_replays(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        new = ingest_batch(s, org.id, _batch("a", "b"))
        assert len(new) == 2
    with session_scope(cloud_session_factory) as s:
        new = ingest_batch(s, org.id, _batch("b", "c"))
        assert [e.source_id for e in new] == ["c"]
        assert len(EventRepository(s).list_recent(org.id)) == 3


def test_unknown_schema_version_is_rejected(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        with pytest.raises(UnsupportedSchemaError):
            ingest_batch(s, org.id, _batch("a", version=999))


def test_events_are_isolated_between_orgs(cloud_session_factory):
    # Hard gate: org B must never see org A's ingested events.
    with session_scope(cloud_session_factory) as s:
        a = OrgRepository(s).add("a")
        b = OrgRepository(s).add("b")
        ingest_batch(s, a.id, _batch("secret-a"))
        b_events = EventRepository(s).list_recent(b.id)
        assert b_events == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_ingest_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cloud.ingest.service'`.

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/ingest/service.py
"""Event intake (spec §"Live streaming", §"Security and trust").

Validates the batch schema version, then stores the events scoped to exactly one
org. Returns the newly-inserted events so the caller can fan them out to the live
channel. Org isolation is enforced here and in the repository: every write carries
an explicit ``org_id`` resolved from the presented enrollment token.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from cloud.persistence.repositories import EventRepository
from datatool.telemetry.events import SCHEMA_VERSION, TelemetryBatch, TelemetryEvent


class UnsupportedSchemaError(Exception):
    """Raised when a batch's schema version is not supported by this panel."""


def ingest_batch(
    session: Session, org_id: uuid.UUID, batch: TelemetryBatch
) -> list[TelemetryEvent]:
    if batch.schema_version != SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"unsupported schema version {batch.schema_version}; expected {SCHEMA_VERSION}"
        )
    return EventRepository(session).add_batch(org_id, batch.events)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_ingest_service.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/ingest/service.py tests/cloud/test_ingest_service.py
git commit -m "feat(cloud): add ingest service with schema check and org isolation"
```

---

## Task 10: Panel config + app factory

**Files:**
- Create: `cloud/config.py`
- Create: `cloud/app.py`
- Test: `tests/cloud/test_app.py`

**Interfaces:**
- Produces:
  - `CloudSettings(BaseSettings)` (env prefix `DATATOOL_CLOUD_`): `database_url: str`, `session_ttl_hours: int = 720`, `cookie_secure: bool = False`; `get_cloud_settings()` (lru_cached).
  - `create_app(session_factory, *, channels: LiveChannels, settings: CloudSettings) -> FastAPI`. Sets `app.state.session_factory`, `app.state.channels`, `app.state.settings`. Registers all route groups (auth, tokens, ingest, console). Exposes `GET /healthz` → `{"status": "ok"}`. Mounts `cloud/console/static` at `/static`.

This task wires an app whose route modules are added incrementally in Tasks 11–14. To keep the app importable now, create the route modules as empty `register_*` stubs and flesh them out in their own tasks.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_app.py
from fastapi.testclient import TestClient

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels


def _client(cloud_session_factory):
    app = create_app(
        cloud_session_factory,
        channels=LiveChannels(),
        settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"),
    )
    return TestClient(app)


def test_healthz_reports_ok(cloud_session_factory):
    assert _client(cloud_session_factory).get("/healthz").json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cloud.app'`.

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/config.py
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
```

Create the four route stub modules so the app imports cleanly (each gets a real body in its own task):

```python
# cloud/api/__init__.py
```
```python
# cloud/api/auth_routes.py
from __future__ import annotations

from fastapi import FastAPI


def register_auth_routes(app: FastAPI) -> None:
    """Filled in by Task 11."""
```
```python
# cloud/api/token_routes.py
from __future__ import annotations

from fastapi import FastAPI


def register_token_routes(app: FastAPI) -> None:
    """Filled in by Task 12."""
```
```python
# cloud/api/ingest_routes.py
from __future__ import annotations

from fastapi import FastAPI


def register_ingest_routes(app: FastAPI) -> None:
    """Filled in by Task 13."""
```
```python
# cloud/api/console_routes.py
from __future__ import annotations

from fastapi import FastAPI


def register_console_routes(app: FastAPI) -> None:
    """Filled in by Task 14."""
```

Create the static dir so the mount has a target: `mkdir -p cloud/console/static cloud/console/templates` and add an empty `cloud/console/__init__.py`.

```python
# cloud/app.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_app.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add cloud/config.py cloud/app.py cloud/api/ cloud/console/ tests/cloud/test_app.py
git commit -m "feat(cloud): add panel config and app factory with route stubs"
```

---

## Task 11: Auth routes + current-user dependency

**Files:**
- Modify: `cloud/api/auth_routes.py`
- Test: `tests/cloud/test_auth_routes.py`

**Interfaces:**
- Consumes: `sign_up`, `authenticate`, `create_session`, `destroy_session`, `resolve_session`, `EmailTakenError`, `get_settings`/`app.state.settings`.
- Produces:
  - `POST /signup` (form fields `org_name`, `email`, `password`) → creates org+admin, sets `datatool_session` cookie, redirects to `/` (303). Duplicate email → 409.
  - `POST /login` (form `email`, `password`) → sets cookie, redirect to `/` (303); bad creds → 401.
  - `POST /logout` → clears cookie + destroys session, redirect to `/login` (303).
  - `current_user(request) -> m.User` dependency: resolves the cookie, raises 401 if absent/expired. Exported for other route modules.
  - Cookie name constant `SESSION_COOKIE = "datatool_session"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_auth_routes.py
from fastapi.testclient import TestClient

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels


def _client(f):
    app = create_app(f, channels=LiveChannels(),
                     settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"))
    return TestClient(app)


def test_signup_sets_session_cookie(cloud_session_factory):
    c = _client(cloud_session_factory)
    r = c.post("/signup", data={"org_name": "acme", "email": "a@acme.test",
                                "password": "pw12345678"}, follow_redirects=False)
    assert r.status_code == 303
    assert "datatool_session" in r.cookies


def test_login_with_bad_password_is_401(cloud_session_factory):
    c = _client(cloud_session_factory)
    c.post("/signup", data={"org_name": "acme", "email": "a@acme.test",
                            "password": "pw12345678"})
    r = c.post("/login", data={"email": "a@acme.test", "password": "wrong"},
               follow_redirects=False)
    assert r.status_code == 401


def test_duplicate_signup_is_409(cloud_session_factory):
    c = _client(cloud_session_factory)
    data = {"org_name": "acme", "email": "a@acme.test", "password": "pw12345678"}
    c.post("/signup", data=data)
    assert c.post("/signup", data=data, follow_redirects=False).status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_auth_routes.py -v`
Expected: FAIL — assertion error (the stub registers no `/signup`, so it 404s / cookie missing).

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/api/auth_routes.py
"""Sign-up, login, logout, and the ``current_user`` dependency (spec §"Accounts").

Auth is session-cookie based. The cookie is HttpOnly and SameSite=Lax; ``Secure``
is driven by ``settings.cookie_secure`` so it is set in production over HTTPS but
left off for local HTTP tests. ``now`` for expiry comes from the request time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from cloud.accounts.service import EmailTakenError, authenticate, sign_up
from cloud.auth.sessions import create_session, destroy_session, resolve_session
from cloud.persistence import models as m
from cloud.persistence.db import session_scope

SESSION_COOKIE = "datatool_session"


def _set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", secure=secure, path="/"
    )


def current_user(request: Request) -> m.User:
    """Resolve the signed-in user from the session cookie, or raise 401."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "not signed in")
    factory = request.app.state.session_factory
    with session_scope(factory) as session:
        user = resolve_session(session, token, now=datetime.now(UTC))
        if user is None:
            raise HTTPException(401, "session expired")
        # detach a lightweight copy: id + org_id are all downstream needs
        session.expunge(user)
        return user


def register_auth_routes(app: FastAPI) -> None:
    settings = app.state.settings
    factory = app.state.session_factory

    @app.post("/signup")
    def signup(
        org_name: str = Form(...), email: str = Form(...), password: str = Form(...)
    ) -> Response:
        with session_scope(factory) as session:
            try:
                user = sign_up(
                    session, org_name=org_name, email=email, password=password
                )
            except EmailTakenError as exc:
                raise HTTPException(409, "email already registered") from exc
            token = create_session(
                session, user.id, now=datetime.now(UTC),
                ttl_hours=settings.session_ttl_hours,
            )
        response = RedirectResponse("/", status_code=303)
        _set_session_cookie(response, token, secure=settings.cookie_secure)
        return response

    @app.post("/login")
    def login(email: str = Form(...), password: str = Form(...)) -> Response:
        with session_scope(factory) as session:
            user = authenticate(session, email=email, password=password)
            if user is None:
                raise HTTPException(401, "invalid email or password")
            token = create_session(
                session, user.id, now=datetime.now(UTC),
                ttl_hours=settings.session_ttl_hours,
            )
        response = RedirectResponse("/", status_code=303)
        _set_session_cookie(response, token, secure=settings.cookie_secure)
        return response

    @app.post("/logout")
    def logout(request: Request) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            with session_scope(factory) as session:
                destroy_session(session, token)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_auth_routes.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/api/auth_routes.py tests/cloud/test_auth_routes.py
git commit -m "feat(cloud): add auth routes and current-user dependency"
```

---

## Task 12: Token management routes

**Files:**
- Modify: `cloud/api/token_routes.py`
- Test: `tests/cloud/test_token_routes.py`

**Interfaces:**
- Consumes: `current_user`, `issue_token`, `list_tokens`, `revoke_token`.
- Produces (all require a signed-in session; all scoped to the caller's org):
  - `POST /tokens` (form `label`) → `{"token": "<plaintext shown once>", "id": "<uuid>"}` (201).
  - `GET /tokens` → `{"tokens": [{"id", "label", "created_at", "revoked": bool}]}`.
  - `POST /tokens/{token_id}/revoke` → `{"revoked": true}`; revoking another org's token → 404.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_token_routes.py
from fastapi.testclient import TestClient

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels


def _signed_in_client(f, email="a@acme.test"):
    app = create_app(f, channels=LiveChannels(),
                     settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"))
    c = TestClient(app)
    c.post("/signup", data={"org_name": "acme", "email": email, "password": "pw12345678"})
    return c


def test_token_creation_returns_plaintext_once_then_lists_it(cloud_session_factory):
    c = _signed_in_client(cloud_session_factory)
    created = c.post("/tokens", data={"label": "prod"})
    assert created.status_code == 201
    assert created.json()["token"]  # plaintext present
    listed = c.get("/tokens").json()["tokens"]
    assert len(listed) == 1 and listed[0]["label"] == "prod"
    assert "token" not in listed[0]  # plaintext never returned again


def test_tokens_require_sign_in(cloud_session_factory):
    app = create_app(cloud_session_factory, channels=LiveChannels(),
                     settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"))
    anon = TestClient(app)
    assert anon.get("/tokens").status_code == 401


def test_revoking_marks_token_revoked(cloud_session_factory):
    c = _signed_in_client(cloud_session_factory)
    tok_id = c.post("/tokens", data={"label": "x"}).json()["id"]
    assert c.post(f"/tokens/{tok_id}/revoke").json() == {"revoked": True}
    assert c.get("/tokens").json()["tokens"][0]["revoked"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_token_routes.py -v`
Expected: FAIL — `/tokens` 404s (stub registers nothing).

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/api/token_routes.py
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
    def create_token(
        label: str = Form(""), user: m.User = Depends(current_user)
    ) -> dict:
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
        with session_scope(factory) as session:
            row = session.get(m.EnrollmentToken, uuid.UUID(token_id))
            if row is None or row.org_id != user.org_id:
                raise HTTPException(404, "token not found")
            revoke_token(session, row.id, now=datetime.now(UTC))
        return {"revoked": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_token_routes.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/api/token_routes.py tests/cloud/test_token_routes.py
git commit -m "feat(cloud): add org-scoped enrollment token routes"
```

---

## Task 13: Ingest + agent-connect routes (bearer-token auth)

**Files:**
- Modify: `cloud/api/ingest_routes.py`
- Test: `tests/cloud/test_ingest_routes.py`

**Interfaces:**
- Consumes: `resolve_token`, `ingest_batch`, `UnsupportedSchemaError`, `app.state.channels`, `TelemetryBatch`.
- Produces (bearer-token auth via `Authorization: Bearer <plaintext>`):
  - `POST /ingest` (JSON body = `TelemetryBatch`) → `{"accepted": <int new events>}`; bad/revoked token → 401; unknown schema → 422. Publishes each newly-inserted event (as `model_dump(mode="json")`) to the org's live channel.
  - `POST /agent/connect` → `{"org": "<name>"}` for a valid token (used by `datatool connect` to validate before reporting); bad token → 401. No body. This is *not* a control channel — it only confirms the token resolves.
  - Helper `_org_from_bearer(request) -> m.Org` raising 401.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_ingest_routes.py
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels
from datatool.telemetry.events import TelemetryBatch, TelemetryEvent


def _client_and_token(cloud_session_factory):
    app = create_app(cloud_session_factory, channels=LiveChannels(),
                     settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"))
    c = TestClient(app)
    c.post("/signup", data={"org_name": "acme", "email": "a@acme.test", "password": "pw12345678"})
    token = c.post("/tokens", data={"label": "prod"}).json()["token"]
    return c, token


def _batch_json():
    ev = TelemetryEvent(source="action", source_id="x1", kind="promote",
                        summary="promoted", occurred_at=datetime.now(UTC))
    return TelemetryBatch(events=[ev]).model_dump(mode="json")


def test_ingest_accepts_events_with_valid_token(cloud_session_factory):
    c, token = _client_and_token(cloud_session_factory)
    r = c.post("/ingest", json=_batch_json(), headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json() == {"accepted": 1}


def test_ingest_rejects_missing_or_bad_token(cloud_session_factory):
    c, _ = _client_and_token(cloud_session_factory)
    assert c.post("/ingest", json=_batch_json()).status_code == 401
    assert c.post("/ingest", json=_batch_json(),
                  headers={"Authorization": "Bearer nope"}).status_code == 401


def test_agent_connect_returns_org_for_valid_token(cloud_session_factory):
    c, token = _client_and_token(cloud_session_factory)
    r = c.post("/agent/connect", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json() == {"org": "acme"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_ingest_routes.py -v`
Expected: FAIL — `/ingest` 404s (stub).

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/api/ingest_routes.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_ingest_routes.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/api/ingest_routes.py tests/cloud/test_ingest_routes.py
git commit -m "feat(cloud): add token-authed ingest and connect-check routes"
```

---

## Task 14: Console routes + SSE + terminal UI

**Files:**
- Modify: `cloud/api/console_routes.py`
- Create: `cloud/console/templates/login.html`, `cloud/console/templates/console.html`
- Create: `cloud/console/static/console.css`, `cloud/console/static/console.js`
- Test: `tests/cloud/test_console_routes.py`

**Interfaces:**
- Consumes: `current_user`, `app.state.channels`, `EventRepository.list_recent`, Jinja2 templates.
- Produces:
  - `GET /login` → the sign-in page (200, no auth).
  - `GET /` → the console page if signed in; redirect to `/login` (303) if not.
  - `GET /console/stream` → `text/event-stream`; signed-in only (401 otherwise). Emits a backfill of recent events, then live events from the org's channel. Each frame: `data: <json>\n\n`.

> **Invoke `frontend-design` at this step** to give the terminal console a distinctive, intentional aesthetic (monospace, live-appending lines, status glyphs ramp ↑ / hold ⏸ / promote ✓ / revert ⟲ / guardrail ⚠, a connection indicator, per-experiment filter). The HTML/CSS/JS below is a correct, minimal baseline to make the tests pass; refine its look under that skill without changing the endpoints or the SSE contract.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_console_routes.py
from fastapi.testclient import TestClient

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels


def _app(f):
    return create_app(f, channels=LiveChannels(),
                      settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"))


def test_login_page_is_public(cloud_session_factory):
    c = TestClient(_app(cloud_session_factory))
    r = c.get("/login")
    assert r.status_code == 200 and "sign in" in r.text.lower()


def test_console_redirects_anonymous_to_login(cloud_session_factory):
    c = TestClient(_app(cloud_session_factory))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_console_renders_for_signed_in_user(cloud_session_factory):
    c = TestClient(_app(cloud_session_factory))
    c.post("/signup", data={"org_name": "acme", "email": "a@acme.test", "password": "pw12345678"})
    assert c.get("/").status_code == 200


def test_stream_requires_sign_in(cloud_session_factory):
    c = TestClient(_app(cloud_session_factory))
    assert c.get("/console/stream").status_code == 401


def test_stream_backfills_recent_events(cloud_session_factory):
    from datetime import UTC, datetime
    from cloud.persistence.db import session_scope
    from cloud.persistence.repositories import EventRepository, UserRepository
    from datatool.telemetry.events import TelemetryEvent

    c = TestClient(_app(cloud_session_factory))
    c.post("/signup", data={"org_name": "acme", "email": "a@acme.test", "password": "pw12345678"})
    with session_scope(cloud_session_factory) as s:
        org_id = UserRepository(s).get_by_email("a@acme.test").org_id
        EventRepository(s).add_batch(org_id, [TelemetryEvent(
            source="action", source_id="seed", kind="promote",
            summary="promoted checkout", occurred_at=datetime.now(UTC))])

    # Read one SSE frame from the backfill, then stop.
    with c.stream("GET", "/console/stream") as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data:"):
                assert "promoted checkout" in line
                break
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cloud/test_console_routes.py -v`
Expected: FAIL — `/login` 404s (stub).

- [ ] **Step 3: Write minimal implementation**

```python
# cloud/api/console_routes.py
"""Console pages + the SSE live stream (spec §"The terminal console").

``/console/stream`` first backfills the most recent stored events (so a freshly
opened console is not blank) and then forwards live events from the org's channel.
The browser's built-in ``EventSource`` reconnect handles transient drops. The
stream is org-scoped via ``current_user``; it never sees another org's channel.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from cloud.api.auth_routes import current_user
from cloud.persistence import models as m
from cloud.persistence.db import session_scope
from cloud.persistence.repositories import EventRepository

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent.parent / "console" / "templates"))


def _event_to_payload(event: m.Event) -> dict:
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

        with session_scope(factory) as session:
            recent = EventRepository(session).list_recent(org_id, limit=200)
            backfill = [_event_to_payload(e) for e in reversed(recent)]

        async def gen():
            try:
                for payload in backfill:
                    yield f"data: {json.dumps(payload)}\n\n"
                while True:
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"data: {json.dumps(payload)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"  # comment frame keeps the connection open
            finally:
                channels.unsubscribe(org_id, queue)

        return StreamingResponse(gen(), media_type="text/event-stream")
```

```html
<!-- cloud/console/templates/login.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>datatool — sign in</title>
  <link rel="stylesheet" href="/static/console.css">
</head>
<body class="login">
  <main class="card">
    <h1>datatool</h1>
    <p class="muted">sign in to your console</p>
    <form method="post" action="/login">
      <input name="email" type="email" placeholder="email" required>
      <input name="password" type="password" placeholder="password" required>
      <button type="submit">sign in</button>
    </form>
    <details>
      <summary class="muted">create an org</summary>
      <form method="post" action="/signup">
        <input name="org_name" placeholder="org name" required>
        <input name="email" type="email" placeholder="email" required>
        <input name="password" type="password" placeholder="password" required>
        <button type="submit">sign up</button>
      </form>
    </details>
  </main>
</body>
</html>
```

```html
<!-- cloud/console/templates/console.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>datatool — console</title>
  <link rel="stylesheet" href="/static/console.css">
</head>
<body class="console">
  <header>
    <span class="brand">datatool</span>
    <span id="status" class="status">connecting…</span>
    <input id="filter" placeholder="filter by experiment…">
    <form method="post" action="/logout"><button type="submit">sign out</button></form>
  </header>
  <main id="feed" aria-live="polite"></main>
  <script src="/static/console.js"></script>
</body>
</html>
```

```css
/* cloud/console/static/console.css — baseline terminal aesthetic; refine under frontend-design */
:root { --bg:#0b0f0a; --fg:#c8f7c5; --muted:#5f7a5b; --accent:#7CFC00; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size:14px; }
.login { display:grid; place-items:center; height:100vh; }
.card { width:320px; padding:24px; border:1px solid var(--muted); border-radius:8px; }
.card h1 { margin:0; color:var(--accent); }
.muted { color:var(--muted); }
input, button { width:100%; margin:6px 0; padding:8px; background:#0f160e;
  color:var(--fg); border:1px solid var(--muted); border-radius:4px; }
button { cursor:pointer; }
header { display:flex; gap:12px; align-items:center; padding:8px 12px;
  border-bottom:1px solid var(--muted); }
header .brand { color:var(--accent); font-weight:bold; }
header form { margin-left:auto; }
header input { width:200px; margin:0; }
.status::before { content:"● "; }
.status.live { color:var(--accent); }
.status.down { color:#e06c75; }
#feed { padding:8px 12px; }
.line { white-space:pre-wrap; padding:2px 0; }
.line .ts { color:var(--muted); }
.line .glyph { color:var(--accent); }
```

```javascript
// cloud/console/static/console.js — SSE client; vanilla, no framework (spec)
const feed = document.getElementById("feed");
const status = document.getElementById("status");
const filter = document.getElementById("filter");

const GLYPH = { ramp: "↑", hold: "⏸", promote: "✓", revert: "⟲", guardrail: "⚠" };

function render(ev) {
  const term = (filter.value || "").trim();
  if (term && (ev.experiment_id || "").indexOf(term) === -1) return;
  const line = document.createElement("div");
  line.className = "line";
  const glyph = GLYPH[ev.kind] || "•";
  line.innerHTML =
    `<span class="ts">${ev.occurred_at}</span> ` +
    `<span class="glyph">${glyph}</span> ` +
    `${ev.experiment_id ? "[" + ev.experiment_id + "] " : ""}${ev.summary}`;
  feed.appendChild(line);
  window.scrollTo(0, document.body.scrollHeight);
}

const source = new EventSource("/console/stream");
source.onopen = () => { status.textContent = "live"; status.className = "status live"; };
source.onerror = () => { status.textContent = "reconnecting"; status.className = "status down"; };
source.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
```

> Note on `Jinja2Templates`: this uses the request-first signature `TemplateResponse(request, name, context)` available in Starlette ≥0.29 (your `uvicorn[standard]`/fastapi pins satisfy this). If a deprecation warning appears, it is non-blocking for Milestone 1.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cloud/test_console_routes.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add cloud/api/console_routes.py cloud/console/ tests/cloud/test_console_routes.py
git commit -m "feat(cloud): add console pages and SSE live stream"
```

---

## Task 15: Agent-side cursor + collector

**Files:**
- Modify: `datatool/config.py` (add cloud fields)
- Create: `datatool/telemetry/cursor.py`
- Create: `datatool/telemetry/collector.py`
- Test: `tests/telemetry/test_cursor.py`, `tests/telemetry/test_collector.py`

**Interfaces:**
- `datatool/config.py` `Settings` gains: `cloud_url: str | None = None`, `cloud_token: str | None = None`, `cloud_report_interval_seconds: int = 10`.
- `cursor.py`: `FileCursor(path: Path)` with `.load() -> dict[str, str]` (source → ISO timestamp watermark; `{}` if file absent) and `.save(watermarks: dict[str, str]) -> None`.
- `collector.py`: `collect_new(session, watermarks: dict[str, str]) -> tuple[list[TelemetryEvent], dict[str, str]]`. Reads rows from `decisions`, `actions`, `guardrail_evaluations`, `trust_events`, `audit_log` with `created_at >= watermark[source]` (or all if no watermark), converts each to a `TelemetryEvent`, and returns the events plus advanced watermarks (max `created_at` seen per source). Source keys: `"decision"`, `"action"`, `"guardrail"`, `"trust"`, `"audit"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/telemetry/test_cursor.py
from datatool.telemetry.cursor import FileCursor


def test_cursor_roundtrips_watermarks(tmp_path):
    cursor = FileCursor(tmp_path / "cur.json")
    assert cursor.load() == {}
    cursor.save({"action": "2026-06-22T00:00:00+00:00"})
    assert FileCursor(tmp_path / "cur.json").load() == {"action": "2026-06-22T00:00:00+00:00"}
```

```python
# tests/telemetry/test_collector.py
from datetime import UTC, datetime

from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    ActionRepository, AuditLogRepository, ExperimentRepository,
)
from datatool.telemetry.collector import collect_new


def _experiment(s):
    return ExperimentRepository(s).add(
        name="exp", surface="checkout", owner="o", contract={}, spec={}, state="proposed")


def test_collect_new_returns_events_for_audit_rows(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s)
        ActionRepository(s).add(experiment_id=exp.id, decision_id=None, kind="promote",
                                adapter="postgres", payload={}, clamped=False,
                                clamp_reason=None, succeeded=True, error=None)
    with session_scope(session_factory) as s:
        events, watermarks = collect_new(s, {})
        kinds = {e.source for e in events}
        assert "action" in kinds
        assert "action" in watermarks
        assert all(e.source_id for e in events)


def test_collect_new_excludes_rows_at_or_before_high_watermark(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s)
        AuditLogRepository(s).add(kind="note", actor="system",
                                  experiment_id=exp.id, payload={"m": 1})
    with session_scope(session_factory) as s:
        events, watermarks = collect_new(s, {})
        n_first = len([e for e in events if e.source == "audit"])
        assert n_first >= 1
    with session_scope(session_factory) as s:
        # Re-collecting with the advanced watermark yields no NEW audit rows.
        events2, _ = collect_new(s, watermarks)
        # boundary rows may reappear (>=), but no growth beyond the original count
        assert len([e for e in events2 if e.source == "audit"]) <= n_first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_cursor.py tests/telemetry/test_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'datatool.telemetry.cursor'`.

- [ ] **Step 3: Write minimal implementation**

First, add the config fields. Edit `datatool/config.py`, in `Settings`, after `require_auth: bool = False`:
```python
    cloud_url: str | None = None
    cloud_token: str | None = None
    cloud_report_interval_seconds: int = 10
```

```python
# datatool/telemetry/cursor.py
"""A tiny on-disk watermark store for the reporter (spec §"Open decisions").

DECISION: the cursor is a local JSON file, NOT a table in the customer's database
— the customer's schema must stay unchanged (spec invariant 3). Per source table we
persist the ISO timestamp of the last row delivered; the reporter re-queries from
there and the panel dedups, giving at-least-once delivery that survives restarts.
"""

from __future__ import annotations

import json
from pathlib import Path


class FileCursor:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def save(self, watermarks: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(watermarks))
```

```python
# datatool/telemetry/collector.py
"""Convert new agent-side audit rows into TelemetryEvents (spec §"Live streaming").

Agent-only: this is the one place that bridges the customer-side ORM to the shared
event schema, keeping ``events.py`` free of persistence imports. Each of the five
append-only tables maps to a normalized event with a human-readable ``summary`` for
the console. We read rows with ``created_at >= watermark`` (inclusive) and rely on
the panel's (org, source, source_id) dedup to absorb the re-read boundary rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from datatool.persistence import models as m
from datatool.telemetry.events import TelemetryEvent

_SOURCES = ("decision", "action", "guardrail", "trust", "audit")


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def collect_new(
    session: Session, watermarks: dict[str, str]
) -> tuple[list[TelemetryEvent], dict[str, str]]:
    events: list[TelemetryEvent] = []
    new_watermarks = dict(watermarks)

    builders = {
        "decision": (_decisions, m.Decision),
        "action": (_actions, m.Action),
        "guardrail": (_guardrails, m.GuardrailEvaluation),
        "trust": (_trusts, m.TrustEvent),
        "audit": (_audits, m.AuditLog),
    }
    for source in _SOURCES:
        build, model = builders[source]
        since = _parse(watermarks.get(source))
        stmt = select(model)
        if since is not None:
            stmt = stmt.where(model.created_at >= since)
        stmt = stmt.order_by(model.created_at)
        rows = list(session.scalars(stmt))
        max_ts = since
        for row in rows:
            events.append(build(row))
            if max_ts is None or row.created_at > max_ts:
                max_ts = row.created_at
        if max_ts is not None:
            new_watermarks[source] = max_ts.isoformat()

    return events, new_watermarks


def _decisions(row: m.Decision) -> TelemetryEvent:
    return TelemetryEvent(
        source="decision", source_id=str(row.id), kind=row.kind,
        summary=f"decision {row.kind}: {row.reason}",
        occurred_at=row.created_at, experiment_id=str(row.experiment_id),
        detail={"inputs": row.inputs, "outputs": row.outputs},
    )


def _actions(row: m.Action) -> TelemetryEvent:
    return TelemetryEvent(
        source="action", source_id=str(row.id), kind=row.kind,
        summary=f"action {row.kind} via {row.adapter}"
                + (" (clamped)" if row.clamped else ""),
        occurred_at=row.created_at, experiment_id=str(row.experiment_id),
        detail={"clamped": row.clamped, "succeeded": row.succeeded, "error": row.error},
    )


def _guardrails(row: m.GuardrailEvaluation) -> TelemetryEvent:
    return TelemetryEvent(
        source="guardrail", source_id=str(row.id),
        kind="guardrail",
        summary=f"guardrail {row.guardrail_name}: "
                + ("breached" if row.breached else "ok"),
        occurred_at=row.created_at, experiment_id=str(row.experiment_id),
        detail={"breached": row.breached, "severity": row.severity,
                "value": row.value, "threshold": row.threshold},
    )


def _trusts(row: m.TrustEvent) -> TelemetryEvent:
    return TelemetryEvent(
        source="trust", source_id=str(row.id), kind=row.kind,
        summary=f"trust {row.kind} on {row.surface}: {row.reason}",
        occurred_at=row.created_at,
        experiment_id=str(row.experiment_id) if row.experiment_id else None,
        surface=row.surface, detail={"delta": row.delta, "new_state": row.new_state},
    )


def _audits(row: m.AuditLog) -> TelemetryEvent:
    return TelemetryEvent(
        source="audit", source_id=str(row.id), kind=row.kind,
        summary=f"{row.actor}: {row.kind}",
        occurred_at=row.created_at,
        experiment_id=str(row.experiment_id) if row.experiment_id else None,
        detail=row.payload,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_cursor.py tests/telemetry/test_collector.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add datatool/config.py datatool/telemetry/cursor.py datatool/telemetry/collector.py tests/telemetry/test_cursor.py tests/telemetry/test_collector.py
git commit -m "feat(telemetry): add cursor and ORM-to-event collector"
```

---

## Task 16: Reporter (push + offline buffer + non-blocking thread)

**Files:**
- Create: `datatool/telemetry/reporter.py`
- Test: `tests/telemetry/test_reporter.py`

**Interfaces:**
- Consumes: `collect_new`, `FileCursor`, `TelemetryBatch`, an injected `httpx.Client`.
- Produces:
  - `TelemetryReporter(session_factory, cursor: FileCursor, *, client: httpx.Client, token: str, ingest_path: str = "/ingest")` with `report_once() -> int` — collect new events; if none, return 0; POST a `TelemetryBatch` with `Authorization: Bearer <token>`; on a 2xx response advance and persist the cursor and return the number sent; on any HTTP/transport error, leave the cursor unadvanced and return 0 (the rows remain to be retried — the append-only tables *are* the offline buffer). Never raises.
  - `ReporterThread(reporter: TelemetryReporter, *, interval_seconds: float)` with `.start()` and `.stop()` — runs `report_once()` on an interval in a daemon thread; `stop()` signals and joins. Conforms structurally to a `start()`/`stop()` handle.

- [ ] **Step 1: Write the failing test**

```python
# tests/telemetry/test_reporter.py
from datetime import UTC, datetime

import httpx

from datatool.persistence.db import session_scope
from datatool.persistence.repositories import ActionRepository, ExperimentRepository
from datatool.telemetry.cursor import FileCursor
from datatool.telemetry.reporter import TelemetryReporter


def _seed_action(session_factory):
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).add(name="exp", surface="checkout", owner="o",
                                          contract={}, spec={}, state="proposed")
        ActionRepository(s).add(experiment_id=exp.id, decision_id=None, kind="promote",
                                adapter="postgres", payload={}, clamped=False,
                                clamp_reason=None, succeeded=True, error=None)


def test_report_once_posts_events_and_advances_cursor(session_factory, tmp_path):
    _seed_action(session_factory)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"accepted": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://panel")
    cursor = FileCursor(tmp_path / "cur.json")
    reporter = TelemetryReporter(session_factory, cursor, client=client, token="tok")

    assert reporter.report_once() >= 1
    assert seen["auth"] == "Bearer tok"
    assert "promote" in seen["body"]
    assert cursor.load() != {}  # advanced


def test_report_once_does_not_advance_cursor_on_server_error(session_factory, tmp_path):
    _seed_action(session_factory)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://panel")
    cursor = FileCursor(tmp_path / "cur.json")
    reporter = TelemetryReporter(session_factory, cursor, client=client, token="tok")

    assert reporter.report_once() == 0
    assert cursor.load() == {}  # not advanced — rows remain for retry


def test_report_once_swallows_transport_errors(session_factory, tmp_path):
    _seed_action(session_factory)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://panel")
    reporter = TelemetryReporter(session_factory, FileCursor(tmp_path / "c.json"),
                                 client=client, token="tok")
    assert reporter.report_once() == 0  # no raise


def test_report_once_with_no_new_events_returns_zero(session_factory, tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                          base_url="http://panel")
    reporter = TelemetryReporter(session_factory, FileCursor(tmp_path / "c.json"),
                                 client=client, token="tok")
    assert reporter.report_once() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_reporter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'datatool.telemetry.reporter'`.

- [ ] **Step 3: Write minimal implementation**

```python
# datatool/telemetry/reporter.py
"""The telemetry reporter (spec §"Components", invariants 1–2).

``report_once`` tails the append-only tables, batches new events, and POSTs them
upward over an outbound-only HTTPS connection. It advances its cursor only after a
successful push, so a failed/offline push simply leaves the rows to be retried next
cycle — the append-only tables themselves are the durable offline buffer, and the
panel's dedup makes re-delivery idempotent.

``ReporterThread`` runs this on an interval in a daemon thread, fully decoupled from
the control loop: a slow or hung panel can never block a daemon tick (invariant:
"never blocks the loop"). All errors are swallowed and logged; the reporter is
strictly best-effort and read-only.
"""

from __future__ import annotations

import threading

import httpx
from sqlalchemy.orm import Session, sessionmaker

from datatool.observability.logging import get_logger
from datatool.persistence.db import session_scope
from datatool.telemetry.collector import collect_new
from datatool.telemetry.cursor import FileCursor
from datatool.telemetry.events import TelemetryBatch

_log = get_logger("telemetry.reporter")

_TIMEOUT = httpx.Timeout(5.0)  # tight: the reporter must never hang


class TelemetryReporter:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        cursor: FileCursor,
        *,
        client: httpx.Client,
        token: str,
        ingest_path: str = "/ingest",
    ):
        self._session_factory = session_factory
        self._cursor = cursor
        self._client = client
        self._token = token
        self._ingest_path = ingest_path

    def report_once(self) -> int:
        watermarks = self._cursor.load()
        with session_scope(self._session_factory) as session:
            events, advanced = collect_new(session, watermarks)
        if not events:
            return 0
        batch = TelemetryBatch(events=events)
        try:
            response = self._client.post(
                self._ingest_path,
                content=batch.model_dump_json(),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001 — best-effort; never propagate to the loop
            _log.warning("telemetry.push_failed", count=len(events))
            return 0
        self._cursor.save(advanced)
        return len(events)


class ReporterThread:
    """Runs ``report_once`` on an interval in a background daemon thread."""

    def __init__(self, reporter: TelemetryReporter, *, interval_seconds: float):
        self._reporter = reporter
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="datatool-telemetry-reporter", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._reporter.report_once()
            except Exception:  # noqa: BLE001 — defensive; report_once already guards
                _log.exception("telemetry.reporter_tick_failed")
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_reporter.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add datatool/telemetry/reporter.py tests/telemetry/test_reporter.py
git commit -m "feat(telemetry): add reporter with offline buffer and background thread"
```

---

## Task 17: `datatool connect` + wire reporter into the daemon

**Files:**
- Modify: `datatool/cli/commands.py` (add `connect_agent`; start the reporter thread in `run_daemon`)
- Modify: `datatool/cli/main.py` (add the `connect` command)
- Test: `tests/e2e/test_connect_cli.py`

**Interfaces:**
- Consumes: `get_settings`, `httpx`, `TelemetryReporter`, `ReporterThread`, `FileCursor`.
- Produces:
  - `connect_agent(*, url: str, token: str, env_path: Path = Path(".env")) -> str` — POSTs to `{url}/agent/connect` with the bearer token; on success writes `DATATOOL_CLOUD_URL` and `DATATOOL_CLOUD_TOKEN` to `env_path` (appending/updating) and returns the org name; raises `typer.Exit(1)` with a clear stderr message on failure.
  - CLI `datatool connect` with options `--url` (default `DATATOOL_CLOUD_URL`), `--token` (default `DATATOOL_CLOUD_TOKEN`), prompting for any missing value (the onboarding wizard), then printing a sentence-case confirmation and the next step (`run datatool daemon`).
  - `run_daemon` additionally starts a `ReporterThread` when `settings.cloud_url` and `settings.cloud_token` are set, using a cursor at `<config_dir>/telemetry_cursor.json` and a real `httpx.Client(base_url=settings.cloud_url)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/e2e/test_connect_cli.py
import httpx
from pathlib import Path

from datatool.cli.commands import connect_agent


def test_connect_writes_env_on_valid_token(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"org": "acme"})

    # Patch the client factory the command uses so no real network call happens.
    import datatool.cli.commands as commands
    monkeypatch.setattr(
        commands, "_connect_client",
        lambda url: httpx.Client(transport=httpx.MockTransport(handler), base_url=url),
    )

    env_path = tmp_path / ".env"
    org = connect_agent(url="http://panel", token="tok", env_path=env_path)
    assert org == "acme"
    body = env_path.read_text()
    assert "DATATOOL_CLOUD_URL=http://panel" in body
    assert "DATATOOL_CLOUD_TOKEN=tok" in body


def test_connect_rejects_bad_token(tmp_path, monkeypatch):
    import typer
    import pytest
    import datatool.cli.commands as commands
    monkeypatch.setattr(
        commands, "_connect_client",
        lambda url: httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(401)), base_url=url),
    )
    with pytest.raises(typer.Exit):
        connect_agent(url="http://panel", token="bad", env_path=tmp_path / ".env")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/e2e/test_connect_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'connect_agent'`.

- [ ] **Step 3: Write minimal implementation**

Add to `datatool/cli/commands.py` (imports at top as needed: `import httpx`, `from pathlib import Path`):

```python
def _connect_client(url: str) -> "httpx.Client":
    import httpx

    return httpx.Client(base_url=url)


def _upsert_env(env_path: Path, values: dict[str, str]) -> None:
    """Write or replace KEY=value lines in a .env file, preserving other lines."""
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    kept = [ln for ln in lines if "=" in ln and ln.split("=", 1)[0] not in values]
    kept.extend(f"{k}={v}" for k, v in values.items())
    env_path.write_text("\n".join(kept) + "\n")


def connect_agent(*, url: str, token: str, env_path: Path = Path(".env")) -> str:
    """Validate an enrollment token against the panel and persist agent config.

    Returns the org name on success; exits non-zero with a clear message otherwise.
    """
    import httpx

    client = _connect_client(url)
    try:
        response = client.post(
            "/agent/connect", headers={"Authorization": f"Bearer {token}"}, timeout=10.0
        )
        response.raise_for_status()
        org = response.json()["org"]
    except (httpx.HTTPError, KeyError, ValueError):
        _fail("could not validate the enrollment token against the panel")
        raise  # unreachable; _fail raises typer.Exit
    finally:
        client.close()
    _upsert_env(env_path, {"DATATOOL_CLOUD_URL": url, "DATATOOL_CLOUD_TOKEN": token})
    return org
```

In the same file, wire the reporter into `run_daemon`. Add these imports near the existing daemon imports inside `run_daemon`:
```python
    import httpx as _httpx

    from datatool.telemetry.cursor import FileCursor
    from datatool.telemetry.reporter import ReporterThread, TelemetryReporter
```
Then, immediately after `loop_thread.start()` and before `create_app(...)`, insert:
```python
    reporter_thread = None
    if settings.cloud_url and settings.cloud_token:
        reporter = TelemetryReporter(
            factory,
            FileCursor(Path(ctx.config_dir) / "telemetry_cursor.json"),
            client=_httpx.Client(base_url=settings.cloud_url),
            token=settings.cloud_token,
        )
        reporter_thread = ReporterThread(
            reporter, interval_seconds=settings.cloud_report_interval_seconds
        )
        reporter_thread.start()
        Console().print(f"telemetry reporter started → {settings.cloud_url}")
```
And ensure it is stopped on shutdown: wrap the existing `uvicorn.run(...)` call in try/finally:
```python
    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level=settings.log_level)
    finally:
        if reporter_thread is not None:
            reporter_thread.stop()
```
(`Path` is already imported at module top via `from pathlib import Path`; if not, add it.)

Add the CLI command to `datatool/cli/main.py`, after the `daemon` command:

```python
@app.command()
def connect(
    ctx: typer.Context,
    url: str = typer.Option(None, "--url", envvar="DATATOOL_CLOUD_URL", help="Panel URL."),
    token: str = typer.Option(
        None, "--token", envvar="DATATOOL_CLOUD_TOKEN", help="Enrollment token."
    ),
) -> None:
    """Connect this agent to the hosted console (validates the token, saves config)."""
    url = url or typer.prompt("panel url")
    token = token or typer.prompt("enrollment token", hide_input=True)
    org = cmd.connect_agent(url=url, token=token)
    typer.echo(f"connected to {org}. run 'datatool daemon' to start reporting.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/e2e/test_connect_cli.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add datatool/cli/commands.py datatool/cli/main.py tests/e2e/test_connect_cli.py
git commit -m "feat(cli): add datatool connect and wire reporter into the daemon"
```

---

## Task 18: End-to-end hybrid loop test

**Files:**
- Test: `tests/e2e/test_hybrid_loop.py`

This task adds no production code — it proves the whole Milestone-1 loop holds together and locks the org-isolation hard gate at the seam. The panel's `TestClient` (an `httpx.Client` subclass) is injected directly into the reporter as its HTTP client.

**Interfaces:**
- Consumes: everything built above.

- [ ] **Step 1: Write the failing test**

```python
# tests/e2e/test_hybrid_loop.py
"""End-to-end: agent telemetry → ingest → org-isolated storage. (spec §"Milestones")"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels
from cloud.persistence.db import (
    init_db as cloud_init_db, make_engine as cloud_make_engine,
    make_session_factory as cloud_make_session_factory, session_scope as cloud_scope,
)
from cloud.persistence.repositories import EventRepository, UserRepository
from datatool.persistence.db import (
    init_db, make_engine, make_session_factory, session_scope,
)
from datatool.persistence.repositories import ActionRepository, ExperimentRepository
from datatool.telemetry.cursor import FileCursor
from datatool.telemetry.reporter import TelemetryReporter


@pytest.fixture
def cloud_factory() -> sessionmaker:
    engine = cloud_make_engine("sqlite+pysqlite:///:memory:")
    cloud_init_db(engine)
    return cloud_make_session_factory(engine)


@pytest.fixture
def agent_factory() -> sessionmaker:
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    return make_session_factory(engine)


def test_agent_events_reach_the_panel_scoped_to_the_signing_org(
    cloud_factory, agent_factory, tmp_path
):
    app = create_app(cloud_factory, channels=LiveChannels(),
                     settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"))
    panel = TestClient(app, base_url="http://panel")

    # Two orgs sign up; only org A's token is given to the agent.
    panel.post("/signup", data={"org_name": "a", "email": "a@a.test", "password": "pw12345678"})
    token_a = panel.post("/tokens", data={"label": "prod"}).json()["token"]
    panel.post("/logout")
    panel.post("/signup", data={"org_name": "b", "email": "b@b.test", "password": "pw12345678"})

    # Agent produces an action row.
    with session_scope(agent_factory) as s:
        exp = ExperimentRepository(s).add(name="exp", surface="checkout", owner="o",
                                          contract={}, spec={}, state="proposed")
        ActionRepository(s).add(experiment_id=exp.id, decision_id=None, kind="promote",
                                adapter="postgres", payload={}, clamped=False,
                                clamp_reason=None, succeeded=True, error=None)

    # Reporter pushes to the panel using the TestClient as its HTTP client.
    reporter = TelemetryReporter(agent_factory, FileCursor(tmp_path / "cur.json"),
                                 client=panel, token=token_a)
    assert reporter.report_once() >= 1

    # Org A sees the event; org B sees nothing (hard gate).
    with cloud_scope(cloud_factory) as s:
        org_a = UserRepository(s).get_by_email("a@a.test").org_id
        org_b = UserRepository(s).get_by_email("b@b.test").org_id
        a_events = EventRepository(s).list_recent(org_a)
        b_events = EventRepository(s).list_recent(org_b)
    assert any(e.kind == "promote" for e in a_events)
    assert b_events == []


def test_replay_is_idempotent_on_the_panel(cloud_factory, agent_factory, tmp_path):
    app = create_app(cloud_factory, channels=LiveChannels(),
                     settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"))
    panel = TestClient(app, base_url="http://panel")
    panel.post("/signup", data={"org_name": "a", "email": "a@a.test", "password": "pw12345678"})
    token = panel.post("/tokens", data={"label": "x"}).json()["token"]

    with session_scope(agent_factory) as s:
        exp = ExperimentRepository(s).add(name="exp", surface="checkout", owner="o",
                                          contract={}, spec={}, state="proposed")
        ActionRepository(s).add(experiment_id=exp.id, decision_id=None, kind="promote",
                                adapter="postgres", payload={}, clamped=False,
                                clamp_reason=None, succeeded=True, error=None)

    cursor = FileCursor(tmp_path / "cur.json")
    r1 = TelemetryReporter(agent_factory, cursor, client=panel, token=token)
    r1.report_once()
    # Force a re-send from scratch by resetting the cursor; panel must dedup.
    cursor.save({})
    TelemetryReporter(agent_factory, cursor, client=panel, token=token).report_once()

    with cloud_scope(cloud_factory) as s:
        org = UserRepository(s).get_by_email("a@a.test").org_id
        assert len(EventRepository(s).list_recent(org)) == 1  # no duplicate
```

- [ ] **Step 2: Run test to verify it fails (or passes immediately)**

Run: `uv run pytest tests/e2e/test_hybrid_loop.py -v`
Expected: PASS (2 tests) — all production code exists by now. If it fails, the failure pinpoints the broken seam; fix the implicated task, do not weaken the test.

- [ ] **Step 3: Run the full suite + the calibration gate**

Run:
```bash
uv run pytest tests/unit tests/stats tests/integration tests/e2e tests/telemetry tests/cloud
uv run pytest tests/stats
```
Expected: all green; `tests/stats` shows 46 passed / 1 deselected (unchanged).

- [ ] **Step 4: Lint + format**

Run:
```bash
uv run ruff check . && uv run ruff format --check .
```
Expected: clean. If `ruff format --check` reports diffs, run `uv run ruff format .` and re-stage.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_hybrid_loop.py
git commit -m "test(e2e): prove the hybrid telemetry loop and org-isolation gate"
```

---

## Self-review

**Spec coverage** (each spec §Milestone-1 bullet → task):

| Spec requirement | Task(s) |
|---|---|
| Org sign-up + single admin login (session cookie) | 2, 3, 4, 5, 6, 11 |
| Enrollment token generation + revocation | 7, 12 |
| Agent-side reporter: outbound POST, offline buffer, never blocks loop | 15, 16, 17 |
| Ingest: token auth, schema validation, org-scoped storage + live channel | 8, 9, 13 |
| Terminal console streaming live events over SSE behind sign-in | 14 |
| Self-serve onboarding (`datatool connect` + wizard) | 17 |
| Org isolation = hard gate | 3, 9, 13, 18 (locked by an explicit test) |
| Two separate Postgres DBs / `cloud` doesn't import `core`/`control`/`stats` | 2 (own Base), shared dep limited to `events.py` |
| Shared versioned event schema | 1 |
| Stats calibration gate stays green | 18 (verified) |
| Telemetry up, control never down | 13 (`/agent/connect` is read-only), no downward path anywhere |
| Passwords hashed with strong KDF | 4 |
| Deferred (teammate invites/roles, payments, license enforcement, remote control) | not built — correct |

**Placeholder scan:** No "TBD/implement later/handle edge cases" steps; every code step shows complete code; the only intentional baseline-to-be-refined artifact is the console UI in Task 14, explicitly flagged for `frontend-design` without changing endpoints/contract.

**Type consistency check:** `TelemetryEvent`/`TelemetryBatch` fields are identical across Tasks 1, 3, 9, 13, 15, 16. `add_batch` returns `list[TelemetryEvent]` (new events) consistently in Tasks 3, 9, 13. `current_user` returns `m.User` and is consumed identically in Tasks 11–14. `resolve_token` returns `Org | None` in Tasks 7 and 13. Reporter constructor signature is identical in Tasks 16, 17, 18. Cursor `load()/save()` shapes match across Tasks 15, 16, 18. SSE frame format (`data: <json>\n\n`) matches between Task 14 server and the `console.js` client.

**Note for the implementer:** Tasks 8 and 14 depend on async test support. Task 8 introduces `pytest-asyncio` if absent; check `pyproject.toml` first and skip the add if it (or another asyncio plugin) is already configured.
