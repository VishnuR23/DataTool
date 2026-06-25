from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from cloud.persistence.db import init_db, make_engine, make_session_factory


@pytest.fixture
def cloud_session_factory(tmp_path) -> sessionmaker[Session]:
    # File-backed SQLite so connections share the same database. In-memory databases
    # get isolated per-connection in SQLite, which breaks TestClient use-cases.
    db_url = f"sqlite+pysqlite:///{tmp_path / 'cloud.db'}"
    engine = make_engine(db_url)
    init_db(engine)
    return make_session_factory(engine)
