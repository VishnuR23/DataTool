"""DataTool CLI entry point (ARCHITECTURE.md §12).

This is the ``datatool`` command. Only ``init`` and ``version`` exist so far — the
full command set (register, list, show, why, simulate, daemon, …) lands in the CLI
build step. ``init`` creates the database schema so the rest of the system has
somewhere to write.
"""

from __future__ import annotations

import typer

from datatool.config import get_settings
from datatool.persistence.db import init_db, make_engine

app = typer.Typer(help="DataTool — autonomous experimentation controller.", no_args_is_help=True)

__version__ = "0.1.0"


@app.command()
def version() -> None:
    """Print the DataTool version."""
    typer.echo(f"datatool {__version__}")


@app.command()
def init(
    database_url: str = typer.Option(
        None,
        help="Database URL to initialize. Defaults to DATATOOL_DATABASE_URL.",
    ),
) -> None:
    """Create the database schema (no manual SQL required)."""
    url = database_url or get_settings().database_url
    engine = make_engine(url)
    init_db(engine)
    typer.echo(f"initialized datatool schema at {url}")


if __name__ == "__main__":
    app()
