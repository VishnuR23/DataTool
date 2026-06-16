"""Integration-test fixtures: a fresh in-memory database per test.

SQLite stands in for Postgres here purely as a test convenience (the ORM types are
dialect-portable); production runs on Postgres only.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from datatool.persistence.db import init_db, make_engine, make_session_factory


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    return make_session_factory(engine)
