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
