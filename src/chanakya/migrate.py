"""Schema migrations (Alembic), wired to Chanakya's own settings.

The migration scripts live inside the package (``chanakya/migrations``) so a
plain ``pip install .`` ships them, as the Docker image does. There is no
``alembic.ini``: the database URL always comes from ``app.database_url``.

How a database gets its schema (docs/07_DATA_MODEL.md, "Migrations"):

- Empty database: ``init_db`` creates every table from the ORM models and
  stamps the database at the head revision. ``chanakya db upgrade`` on an empty
  database runs the migration scripts instead and ends in the same schema;
  ``tests/test_migrations.py`` holds the two paths equal.
- Stamped database: the schema only changes through ``chanakya db upgrade``.
  ``init_db`` refuses to open a database whose revision is not the head.
- Database made by ``create_all`` before Alembic existed (no
  ``alembic_version`` table): it keeps working exactly as before, and is
  adopted with ``chanakya db stamp``, which checks the schema and then writes
  one row. No data is touched.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from .pgsupport import safe_url

log = logging.getLogger("chanakya.migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
VERSION_TABLE = "alembic_version"
BASELINE_REVISION = "0001"


class SchemaOutOfDate(RuntimeError):
    """The database and the code disagree about the schema. The message says what to run."""


@dataclass(frozen=True)
class SchemaState:
    kind: str                # empty | unmanaged | current | behind | unknown
    current: str | None      # revision recorded in the database
    head: str                # newest revision the code knows
    tables: frozenset[str]   # application tables present (alembic_version excluded)

    def describe(self) -> str:
        return {
            "empty": "empty: no tables yet",
            "unmanaged": "not under migration control (tables exist, no alembic_version). Adopt it with: chanakya db stamp",
            "current": f"up to date at {self.current}",
            "behind": f"at {self.current}, the code expects {self.head}. Run: chanakya db upgrade",
            "unknown": f"at {self.current}, which this version of the code does not know (head is {self.head})",
        }[self.kind]


def alembic_config(*, connection: Connection | None = None, url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    if connection is not None:
        cfg.attributes["connection"] = connection
    if url is not None:
        cfg.attributes["url"] = url
    return cfg


@lru_cache(maxsize=1)
def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(alembic_config())


def head_revision() -> str:
    head = _script().get_current_head()
    if head is None:  # pragma: no cover - the baseline always exists
        raise RuntimeError("no migration scripts found")
    return head


def _state(conn: Connection) -> SchemaState:
    names = set(inspect(conn).get_table_names())
    tables = frozenset(names - {VERSION_TABLE})
    head = head_revision()
    current = MigrationContext.configure(conn).get_current_revision() if VERSION_TABLE in names else None
    if current is None:
        kind = "unmanaged" if tables else "empty"
    elif current == head:
        kind = "current"
    else:
        known = {rev.revision for rev in _script().walk_revisions()}
        kind = "behind" if current in known else "unknown"
    return SchemaState(kind, current, head, tables)


def schema_state(engine: Engine) -> SchemaState:
    with engine.connect() as conn:
        return _state(conn)


_create_lock = threading.Lock()


def prepare_database(engine: Engine) -> SchemaState:
    """Make the database usable by this version of the code, or say exactly why it is not.

    Called by ``db.init_db`` every time a session is opened, so it must stay
    cheap: one catalogue query on a database that is already at the head.
    """
    state = schema_state(engine)
    if state.kind in ("empty", "unmanaged"):
        # Tables may have to be created. The desk opens a session per request and
        # runs a pass on a thread, so two openers can arrive at a new database at
        # the same moment: threads in this process take turns.
        with _create_lock:
            state = schema_state(engine)
            if state.kind == "empty":
                state = _create(engine)
            if state.kind == "unmanaged":
                return _keep_unmanaged(engine, state)
    if state.kind == "current":
        from .db import Base

        missing = sorted(set(Base.metadata.tables) - state.tables)
        if missing:
            raise SchemaOutOfDate(
                f"database is stamped at {state.current} but has no table(s) {', '.join(missing)}. "
                "The models gained a table without a migration. Add a revision (chanakya db revision "
                "--autogenerate) and run chanakya db upgrade. A throwaway development database can be deleted instead."
            )
        return state
    raise SchemaOutOfDate(f"database {safe_url(str(engine.url))} is {state.describe()}")


def _keep_unmanaged(engine: Engine, state: SchemaState) -> SchemaState:
    """A database from before Alembic (or one another process is creating right now).

    Same behaviour as before migrations existed: create whole tables that are
    missing, change nothing that exists, record nothing.
    """
    from .db import Base

    try:
        Base.metadata.create_all(engine)
    except DBAPIError:
        # "table already exists": another process created it between the check and the CREATE.
        if set(Base.metadata.tables) - schema_state(engine).tables:
            raise
    log.info("database %s is %s", safe_url(str(engine.url)), state.describe())
    return state


def _create(engine: Engine, wait_s: float = 10.0) -> SchemaState:
    """Build an empty database from the models and stamp it at the head.

    The caller holds ``_create_lock``. Another process doing the same shows up
    as a database error ("table already exists", a locked file); this opener
    then waits for that process to finish instead of failing.
    """
    from .db import Base

    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            MigrationContext.configure(conn).stamp(_script(), "head")
    except DBAPIError:
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                state = schema_state(engine)
            except DBAPIError:  # still being written
                state = None
            if state is not None and state.kind == "current":
                return state
            time.sleep(0.1)
        raise
    return schema_state(engine)


def upgrade(engine: Engine, revision: str = "head") -> SchemaState:
    """Run migration scripts up to ``revision``. Refuses a database that was never stamped."""
    state = schema_state(engine)
    if state.kind == "unmanaged":
        raise SchemaOutOfDate(
            "this database already has tables but no alembic_version, so the baseline would collide with them. "
            "Adopt it first with: chanakya db stamp"
        )
    with engine.begin() as conn:
        command.upgrade(alembic_config(connection=conn), revision)
    return schema_state(engine)


def upgrade_sql(url: str, revision: str = "head") -> None:
    """Print the SQL an upgrade would run for this URL's dialect, without connecting."""
    command.upgrade(alembic_config(url=url), revision, sql=True)


def _reflect(conn: Connection) -> dict[str, set[str]]:
    insp = inspect(conn)
    return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names() if t != VERSION_TABLE}


def expected_schema(revision: str = "head") -> dict[str, set[str]]:
    """Tables and columns the migration scripts produce at ``revision`` (built in a scratch in-memory SQLite)."""
    scratch = create_engine("sqlite://")
    try:
        with scratch.begin() as conn:
            command.upgrade(alembic_config(connection=conn), revision)
            return _reflect(conn)
    finally:
        scratch.dispose()


def schema_diff(engine: Engine, revision: str = "head") -> list[str]:
    """Differences that make it unsafe to declare this database to be at ``revision``. Empty means it matches."""
    from .db import Base

    want = expected_schema(revision)
    with engine.connect() as conn:
        have = _reflect(conn)
    problems: list[str] = []
    for table in sorted(want):
        if table not in have:
            problems.append(f"missing table {table}")
            continue
        for column in sorted(want[table] - have[table]):
            problems.append(f"missing column {table}.{column}")
        for column in sorted(have[table] - want[table]):
            problems.append(f"unexpected column {table}.{column}")
    for table in sorted(set(have) - set(want)):
        if table in Base.metadata.tables:  # a Chanakya table from a later revision; foreign tables are ignored
            problems.append(f"unexpected table {table}")
    return problems


def stamp(engine: Engine, revision: str = "head", *, force: bool = False) -> SchemaState:
    """Record ``revision`` as the database's schema version without running any migration.

    Only the ``alembic_version`` table is written. Unless ``force`` is set the
    schema is first compared, table by table and column by column, with what
    the migration scripts produce at that revision.
    """
    resolved = _script().get_revision(revision)
    if resolved is None:
        raise SchemaOutOfDate(f"unknown revision {revision!r}")
    if not force:
        problems = schema_diff(engine, resolved.revision)
        if problems:
            raise SchemaOutOfDate(
                f"the schema does not match revision {resolved.revision}: " + "; ".join(problems)
            )
    with engine.begin() as conn:
        MigrationContext.configure(conn).stamp(_script(), resolved.revision)
    return schema_state(engine)


def new_revision(engine: Engine, message: str, *, autogenerate: bool = True, rev_id: str | None = None) -> str:
    """Write a new migration script (a developer command). Returns the file path."""
    with engine.begin() as conn:
        script = command.revision(alembic_config(connection=conn), message=message, autogenerate=autogenerate,
                                  rev_id=rev_id)
    _script.cache_clear()
    return str(getattr(script, "path", script))


def copy_data(source: Engine, target: Engine, *, batch_size: int = 1000) -> dict[str, int]:
    """Copy every row from one Chanakya database into an empty one (the SQLite to PostgreSQL move).

    Both databases must be at the head revision, so their schemas are the same.
    Values travel through the model types, so JSON, booleans and timestamps
    arrive as the target stores them. The target is written in one
    transaction: a failure leaves it as it was. The source is only read.
    """
    from sqlalchemy import JSON, delete, func, insert, null, select, text

    from .db import Base

    for label, engine in (("source", source), ("target", target)):
        state = schema_state(engine)
        if state.kind != "current":
            raise SchemaOutOfDate(f"the {label} database is {state.describe()}")
    tables = Base.metadata.sorted_tables  # parents before the tables that reference them
    copied: dict[str, int] = {}
    with source.connect() as src, target.begin() as dst:
        for table in tables:
            if table.name != "system_settings" and dst.scalar(select(func.count()).select_from(table)):
                raise SchemaOutOfDate(f"the target database already has rows in {table.name}; copy into an empty database")
        dst.execute(delete(Base.metadata.tables["system_settings"]))  # the row init_db wrote; the source has its own
        for table in tables:
            # A JSON column that holds SQL NULL must stay SQL NULL, not become the JSON value null.
            json_columns = [c.name for c in table.columns if isinstance(c.type, JSON)]
            result = src.execution_options(stream_results=True).execute(select(table))
            count = 0
            while rows := result.mappings().fetchmany(batch_size):
                batch = [dict(row) for row in rows]
                for row in batch:
                    for name in json_columns:
                        if row[name] is None:
                            row[name] = null()
                dst.execute(insert(table), batch)
                count += len(rows)
            copied[table.name] = count
        if dst.dialect.name == "postgresql":
            # Rows arrived with their ids, so each serial sequence must be moved past the highest one.
            for table in tables:
                for column in table.primary_key.columns:
                    if column.autoincrement is True:
                        dst.execute(text(
                            f"SELECT setval(pg_get_serial_sequence('{table.name}', '{column.name}'), "
                            f'COALESCE((SELECT MAX("{column.name}") FROM "{table.name}"), 1), '
                            f'(SELECT MAX("{column.name}") IS NOT NULL FROM "{table.name}"))'
                        ))
        for table in tables:
            if dst.scalar(select(func.count()).select_from(table)) != copied[table.name]:
                raise SchemaOutOfDate(f"row count mismatch in {table.name} after the copy; nothing was committed")
    return copied
