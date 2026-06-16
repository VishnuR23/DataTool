"""CLI bootstrap commands (ARCHITECTURE.md §12).

`datatool version` reports the version; `datatool init` creates the full schema
with no manual SQL (acceptance criterion §21.1), here verified against SQLite.
"""

from __future__ import annotations

from sqlalchemy import inspect
from typer.testing import CliRunner

from datatool.cli.main import app
from datatool.persistence.db import make_engine

runner = CliRunner()


def test_version_reports_datatool_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "datatool" in result.stdout


def test_init_creates_the_full_schema(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'datatool.db'}"
    result = runner.invoke(app, ["init", "--database-url", url])
    assert result.exit_code == 0, result.stdout

    tables = set(inspect(make_engine(url)).get_table_names())
    assert "experiments" in tables
    assert len(tables) == 11
