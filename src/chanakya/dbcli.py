"""``chanakya db``: schema migrations for the database named in ``app.database_url``.

Kept apart from ``cli.py`` so the command group can change without touching the
pipeline commands. The rules these commands implement are in ``migrate``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Optional

import typer

db_app = typer.Typer(help="Database schema: show the revision, upgrade, adopt an existing database",
                     no_args_is_help=True)


@contextmanager
def _engine():
    from .cli import settings
    from .db import make_engine

    st = settings()
    try:
        engine = make_engine(st.app.database_url)
    except RuntimeError as exc:  # the optional PostgreSQL driver is not installed
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    try:
        yield st, engine
    finally:
        engine.dispose()


def _fail(exc: Exception) -> typer.Exit:
    typer.secho(str(exc), fg="red", err=True)
    return typer.Exit(1)


@db_app.command("current")
def current(check: Annotated[bool, typer.Option("--check", help="Exit 1 unless the database is at the head revision")] = False) -> None:
    """Show which revision the database is at and which one the code expects. Reads only."""
    from .migrate import schema_state
    from .pgsupport import safe_url

    with _engine() as (st, engine):
        state = schema_state(engine)
        typer.echo(f"database: {safe_url(st.app.database_url)}")
        typer.echo(f"revision: {state.current or 'none'}")
        typer.echo(f"head:     {state.head}")
        typer.echo(f"tables:   {len(state.tables)}")
        typer.secho(f"state:    {state.describe()}", fg="green" if state.kind == "current" else "yellow")
        if check and state.kind != "current":
            raise typer.Exit(1)


@db_app.command("upgrade")
def upgrade(revision: Annotated[str, typer.Argument(help="Target revision")] = "head",
            sql: Annotated[bool, typer.Option("--sql", help="Print the SQL for this database's dialect and change nothing")] = False) -> None:
    """Run migration scripts up to REVISION. On an empty database this builds the whole schema."""
    from alembic.util import CommandError

    from . import migrate
    from .cli import _writer, settings
    from .db import init_db

    if sql:
        migrate.upgrade_sql(settings().app.database_url, revision)
        return
    with _engine() as (st, engine), _writer(st, "migrate"):
        before = migrate.schema_state(engine)
        try:
            after = migrate.upgrade(engine, revision)
        except (migrate.SchemaOutOfDate, CommandError) as exc:  # CommandError: Alembic does not know the revision
            raise _fail(exc) from exc
        if after.kind == "current":
            init_db(engine)  # the schema_version row older tools read
        typer.echo(f"{before.current or 'none'} -> {after.current or 'none'} ({after.describe()})")


@db_app.command("stamp")
def stamp(revision: Annotated[str, typer.Argument(help="Revision to record")] = "head",
          force: Annotated[bool, typer.Option("--force", help="Skip the schema comparison")] = False) -> None:
    """Adopt an existing database: record its revision without running any migration.

    Writes only the alembic_version table. The schema is compared with what the
    migration scripts produce at REVISION first, and the command refuses on a mismatch.
    """
    from . import migrate
    from .cli import _writer
    from .db import init_db

    with _engine() as (st, engine), _writer(st, "migrate"):
        before = migrate.schema_state(engine)
        try:
            if before.kind == "unmanaged" and migrate.head_revision() == _resolve(revision):
                # What every command already does to a pre-Alembic database: add whole
                # tables that newer models introduced. Existing tables are not altered.
                init_db(engine)
            after = migrate.stamp(engine, revision, force=force)
        except migrate.SchemaOutOfDate as exc:
            if before.kind == "empty":
                typer.secho("The database is empty. Build it with: chanakya db upgrade", fg="yellow", err=True)
            raise _fail(exc) from exc
        typer.echo(f"{before.current or 'none'} -> {after.current} ({after.describe()})")


def _resolve(revision: str) -> Optional[str]:
    from .migrate import _script

    found = _script().get_revision(revision)
    return found.revision if found is not None else None


@db_app.command("revision")
def revision(message: Annotated[str, typer.Option("--message", "-m", help="What the migration does")],
             autogenerate: Annotated[bool, typer.Option("--autogenerate/--empty", help="Diff the models against the database")] = True,
             rev_id: Annotated[Optional[str], typer.Option(help="Revision id, for example 0002")] = None) -> None:
    """Developer command: write a new migration script. The database must be at the head revision."""
    from . import migrate

    with _engine() as (_, engine):
        state = migrate.schema_state(engine)
        if autogenerate and state.kind != "current":
            raise _fail(RuntimeError(f"autogenerate needs a database at the head revision; this one is {state.describe()}"))
        typer.echo(migrate.new_revision(engine, message, autogenerate=autogenerate, rev_id=rev_id))


@db_app.command("copy-from")
def copy_from(source_url: Annotated[str, typer.Argument(help="Database to read, for example sqlite:///./data/chanakya.db")]) -> None:
    """Copy all rows from SOURCE_URL into the configured database, which must be empty and at the head revision.

    This is the SQLite to PostgreSQL move (docs/18_DEPLOYMENT_AND_CICD.md). The source is only read.
    """
    from . import migrate
    from .cli import _writer
    from .db import make_engine
    from .pgsupport import safe_url

    with _engine() as (st, target), _writer(st, "migrate"):
        source = make_engine(source_url)
        try:
            copied = migrate.copy_data(source, target)
        except migrate.SchemaOutOfDate as exc:
            raise _fail(exc) from exc
        finally:
            source.dispose()
        for table, rows in copied.items():
            typer.echo(f"  {table:<34}{rows:>9}")
        typer.echo(f"copied {sum(copied.values())} rows from {safe_url(source_url)} to {safe_url(st.app.database_url)}")
