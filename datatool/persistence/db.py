"""Database engine, session management, and the ORM base (ARCHITECTURE.md §5).

PostgreSQL is the only *supported* deployment target (ARCHITECTURE.md §20 — one
process, one Postgres). The ORM models use dialect-portable column types, however,
so the test suite can run them against an in-memory SQLite database without a live
Postgres. SQLite is a test convenience only; it is not a second supported backend.

``JSONB`` is a small portable helper: JSONB on Postgres (indexable, typed), plain
JSON elsewhere.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import JSON, Engine, create_engine
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# JSONB on Postgres, JSON on any other dialect (i.e. SQLite under test).
JSONB = JSON().with_variant(_PG_JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine for ``database_url``."""
    return create_engine(database_url, echo=echo, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    """Create all tables from the ORM metadata.

    DECISION: the MVP bootstraps the schema with ``create_all`` (used by both the
    test suite and ``datatool init``). Alembic is wired for *future* schema
    evolution; a deployed schema is migrated, but the initial create is direct.
    Importing the models module here ensures every table is registered on the
    metadata before the create.
    """
    from datatool.persistence import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error, always close."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
