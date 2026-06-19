"""Integration-test fixtures: a fresh in-memory database per test.

SQLite stands in for Postgres here purely as a test convenience (the ORM types are
dialect-portable); production runs on Postgres only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import Result
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from datatool.cli.main import app
from datatool.persistence.db import init_db, make_engine, make_session_factory

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    return make_session_factory(engine)


@dataclass
class CliEnv:
    """A CLI harness over a fresh file-backed SQLite DB and the repo's config dir.

    File-backed (not in-memory) so state persists across separate command
    invocations and the flag adapter's own session sees committed data.
    """

    runner: CliRunner
    db_url: str
    config_dir: str
    examples_dir: str

    def invoke(self, *args: str) -> Result:
        return self.runner.invoke(
            app, ["--database-url", self.db_url, "--config-dir", self.config_dir, *args]
        )

    def factory(self) -> sessionmaker[Session]:
        return make_session_factory(make_engine(self.db_url))

    def example(self, name: str = "pricing_page.yaml") -> str:
        return str(Path(self.examples_dir) / name)


@pytest.fixture
def cli_env(tmp_path) -> CliEnv:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'cli.db'}"
    init_db(make_engine(db_url))
    return CliEnv(
        runner=CliRunner(),
        db_url=db_url,
        config_dir=str(_REPO_ROOT / "config"),
        examples_dir=str(_REPO_ROOT / "examples"),
    )
