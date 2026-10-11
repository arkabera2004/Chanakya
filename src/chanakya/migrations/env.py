"""Alembic environment.

The connection normally arrives from ``chanakya.migrate`` (``chanakya db ...``).
If Alembic is invoked some other way, the URL comes from Chanakya's settings
(``app.database_url`` / ``CHANAKYA_DATABASE_URL``), never from a file here.
"""

from __future__ import annotations

from alembic import context

from chanakya.db import Base, make_engine
from chanakya.pgsupport import normalize_url

target_metadata = Base.metadata


def _url() -> str:
    url = context.config.attributes.get("url")
    if not url:
        from chanakya.config import load_settings

        url = load_settings().app.database_url
    return normalize_url(url)


def _configure(**kwargs) -> None:
    context.configure(target_metadata=target_metadata, compare_type=True, **kwargs)


def run_offline() -> None:
    """Emit SQL for the target dialect without connecting (``chanakya db upgrade --sql``)."""
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    # SQLite cannot ALTER most things in place; batch mode rebuilds the table instead.
    _configure(connection=connection, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = make_engine(_url())
    try:
        with engine.begin() as conn:
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
