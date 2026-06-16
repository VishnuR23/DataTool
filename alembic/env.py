"""Alembic environment, wired to the ORM metadata and the configured database URL.

The URL comes from ``DATATOOL_DATABASE_URL`` via ``datatool.config`` so migrations
always target the same database the application uses. Importing the models module
registers every table on ``Base.metadata`` for autogeneration.
"""

from __future__ import annotations

from sqlalchemy import pool

from alembic import context
from datatool.config import get_settings
from datatool.persistence import models  # noqa: F401  (register tables on metadata)
from datatool.persistence.db import Base, make_engine

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = make_engine(_url())
    connectable.dispose()
    connectable = make_engine(_url())
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            poolclass=pool.NullPool,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
