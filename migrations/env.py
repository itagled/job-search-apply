"""Alembic environment: the URL comes from ``jobradar.config`` and only the project's tables are handled.

Alembic ignores everything not in the models, so in a shared Postgres it never proposes to touch
tables from other projects.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from jobradar.db.models import Base
from jobradar.db.session import get_database_url

target_metadata = Base.metadata

if context.config.config_file_name and not context.config.attributes.get("connection"):
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    """Only the model tables (views and tables from other projects are excluded)."""
    return name in target_metadata.tables if type_ == "table" else True


def _url() -> str:
    return context.get_x_argument(as_dictionary=True).get("url") or get_database_url()


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_name=include_name,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")  # tests pass an open connection
    if connection is not None:
        _configure(connection=connection, render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        _configure(connection=conn, render_as_batch=conn.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
