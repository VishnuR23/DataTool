from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from cloud.persistence.db import init_db, make_engine, make_session_factory


@pytest.fixture
def cloud_session_factory() -> sessionmaker[Session]:
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    return make_session_factory(engine)
