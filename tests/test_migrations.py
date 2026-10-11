"""Alembic migrations, adoption of a pre-Alembic database, and the PostgreSQL helpers.

Everything here runs on SQLite. Tests that need a real PostgreSQL server are
marked ``needs_postgres`` and only run when CHANAKYA_TEST_DATABASE_URL is set
(the CI job ``test-postgres``).
"""

from __future__ import annotations

from datetime import datetime

import pytest
import yaml
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, inspect, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable
from typer.testing import CliRunner

from chanakya import migrate, pgsupport
from chanakya.cli import app as cli_app
from chanakya.db import Base, Opportunity, Source, SystemSetting, init_db, make_engine, session_factory

from .dbsupport import isolated_database, postgres_base_url

needs_postgres = pytest.mark.skipif(postgres_base_url() is None, reason="set CHANAKYA_TEST_DATABASE_URL to run on PostgreSQL")


@pytest.fixture
def engine(database_url):
    eng = make_engine(database_url)
    yield eng
    eng.dispose()


def structure(engine) -> dict:
    """Everything reflection can tell about the application tables, in a comparable form."""
    insp = inspect(engine)
    out = {}
    for table in insp.get_table_names():
        if table == migrate.VERSION_TABLE:
            continue
        out[table] = {
            "columns": {c["name"]: (str(c["type"]), c["nullable"]) for c in insp.get_columns(table)},
            "primary_key": tuple(insp.get_pk_constraint(table)["constrained_columns"]),
            "indexes": sorted((i["name"], tuple(i["column_names"]), bool(i["unique"])) for i in insp.get_indexes(table)),
            "unique": sorted(tuple(u["column_names"]) for u in insp.get_unique_constraints(table)),
            "foreign_keys": sorted((tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
                                   for f in insp.get_foreign_keys(table)),
        }
    return out


def assert_baseline_matches_models(migrated_url: str, created_url: str) -> None:
    migrated, created = make_engine(migrated_url), make_engine(created_url)
    try:
        state = migrate.upgrade(migrated)
        assert state.kind == "current" and state.current == migrate.head_revision()
        # 1. Tables and columns against the ORM metadata, by name.
        got = structure(migrated)
        assert set(got) == set(Base.metadata.tables)
        for name, table in Base.metadata.tables.items():
            assert set(got[name]["columns"]) == {c.name for c in table.columns}, name
        # 2. Types, nullability, keys, indexes and constraints against a create_all database.
        Base.metadata.create_all(created)
        assert got == structure(created)
        # 3. Alembic's own comparison finds nothing to migrate.
        with migrated.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata) == []
    finally:
        migrated.dispose()
        created.dispose()


# ---------------------------------------------------------------- baseline drift
def test_upgrading_an_empty_sqlite_database_gives_exactly_the_model_schema(tmp_path):
    """The guard against drift: change a model without a migration and this fails."""
    assert_baseline_matches_models(f"sqlite:///{tmp_path / 'migrated.db'}", f"sqlite:///{tmp_path / 'created.db'}")


@needs_postgres
def test_upgrading_an_empty_postgres_schema_gives_exactly_the_model_schema(tmp_path):
    with isolated_database(tmp_path) as migrated, isolated_database(tmp_path) as created:
        assert_baseline_matches_models(migrated, created)


def test_there_is_one_head_and_the_baseline_is_its_root():
    script = migrate._script()
    assert len(script.get_heads()) == 1
    assert script.get_base() == migrate.BASELINE_REVISION


def test_downgrade_removes_every_table(tmp_path):
    from alembic import command

    eng = make_engine(f"sqlite:///{tmp_path / 'down.db'}")
    try:
        migrate.upgrade(eng)
        with eng.begin() as conn:
            command.downgrade(migrate.alembic_config(connection=conn), "base")
        state = migrate.schema_state(eng)
        assert state.tables == frozenset() and state.current is None
    finally:
        eng.dispose()


# ---------------------------------------------------------------- fresh databases
def test_init_db_creates_a_fresh_database_already_at_the_head(engine):
    assert migrate.schema_state(engine).kind == "empty"
    init_db(engine)
    state = migrate.schema_state(engine)
    assert (state.kind, state.current) == ("current", migrate.head_revision())
    assert state.tables == frozenset(Base.metadata.tables)
    init_db(engine)  # opening it again changes nothing
    assert migrate.schema_state(engine) == state
    with session_factory(engine)() as s:
        assert s.get(SystemSetting, "schema_version") is not None


def test_a_database_built_by_upgrade_opens_like_any_other(engine):
    migrate.upgrade(engine)
    init_db(engine)
    with session_factory(engine)() as s:
        s.add(Source(id="synthetic", type="fixture"))
        s.commit()
        assert s.scalar(select(func.count()).select_from(Source)) == 1


# ---------------------------------------------------------------- adoption
def _legacy_database(engine) -> None:
    """A database as Chanakya made it before Alembic: create_all, the schema_version row, some rows."""
    Base.metadata.create_all(engine)
    with session_factory(engine)() as s:
        s.add(SystemSetting(key="schema_version", value={"version": 1}))
        s.add(Source(id="synthetic_portal", type="fixture", name="Synthetic portal"))
        s.add(Opportunity(id="CHK-synthetic01", dedup_key="h:synthetic01", source_id="synthetic_portal",
                          platform="Synthetic", source_record_key="k1", title="SYNTHETIC notice for the adoption test",
                          content_hash="0" * 64, published_at=datetime(2026, 10, 1, 4, 30)))
        s.commit()


def _row_counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in sorted(Base.metadata.tables)}


def test_an_existing_database_keeps_working_and_is_adopted_by_stamp_without_losing_a_row(engine):
    _legacy_database(engine)
    before = _row_counts(engine)
    assert migrate.schema_state(engine).kind == "unmanaged"

    init_db(engine)  # every command does this; it must not stamp or change anything
    assert migrate.schema_state(engine).kind == "unmanaged"
    assert _row_counts(engine) == before

    with pytest.raises(migrate.SchemaOutOfDate, match="chanakya db stamp"):
        migrate.upgrade(engine)  # the baseline would collide with the existing tables

    state = migrate.stamp(engine)
    assert (state.kind, state.current) == ("current", migrate.head_revision())
    assert _row_counts(engine) == before
    init_db(engine)
    with session_factory(engine)() as s:
        assert s.get(Opportunity, "CHK-synthetic01").published_at == datetime(2026, 10, 1, 4, 30)
    assert migrate.upgrade(engine).kind == "current"  # nothing left to run


def test_stamp_refuses_a_schema_that_does_not_match_and_says_what_differs(engine):
    _legacy_database(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE sources DROP COLUMN last_error"))
        conn.execute(text("DROP TABLE embedding_cache"))
    with pytest.raises(migrate.SchemaOutOfDate) as err:
        migrate.stamp(engine)
    assert "missing column sources.last_error" in str(err.value)
    assert "missing table embedding_cache" in str(err.value)
    assert migrate.schema_state(engine).kind == "unmanaged"
    assert migrate.stamp(engine, force=True).kind == "current"  # the operator can overrule the check


def test_a_stamped_database_at_another_revision_is_refused_with_the_command_to_run(engine):
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'ffff'"))
    assert migrate.schema_state(engine).kind == "unknown"
    with pytest.raises(migrate.SchemaOutOfDate, match="ffff"):
        init_db(engine)


def test_a_model_table_without_a_migration_is_reported_not_silently_created(engine):
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE embedding_cache"))
    with pytest.raises(migrate.SchemaOutOfDate, match="embedding_cache"):
        init_db(engine)


# ---------------------------------------------------------------- CLI
def _config(settings, tmp_path):
    cfg = tmp_path / "config" / "chanakya.yaml"
    cfg.parent.mkdir(exist_ok=True)
    data = settings.model_dump(mode="json", exclude={"config_path"})
    data["app"]["capabilities_file"] = str(settings.app.capabilities_file)
    cfg.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(cfg)


def test_cli_upgrade_then_current(settings, tmp_path):
    cfg, runner = _config(settings, tmp_path), CliRunner()
    res = runner.invoke(cli_app, ["--config", cfg, "db", "current", "--check"])
    assert res.exit_code == 1 and "empty" in res.output
    res = runner.invoke(cli_app, ["--config", cfg, "db", "upgrade"])
    assert res.exit_code == 0, res.output
    assert f"none -> {migrate.head_revision()}" in res.output
    res = runner.invoke(cli_app, ["--config", cfg, "db", "current", "--check"])
    assert res.exit_code == 0 and "up to date" in res.output
    assert runner.invoke(cli_app, ["--config", cfg, "status"]).exit_code == 0


def test_cli_stamp_adopts_an_existing_database(settings, tmp_path):
    cfg, runner = _config(settings, tmp_path), CliRunner()
    eng = make_engine(settings.app.database_url)
    try:
        _legacy_database(eng)
        before = _row_counts(eng)
        res = runner.invoke(cli_app, ["--config", cfg, "db", "current"])
        assert res.exit_code == 0 and "chanakya db stamp" in res.output
        res = runner.invoke(cli_app, ["--config", cfg, "db", "upgrade"])
        assert res.exit_code == 1 and "chanakya db stamp" in res.output
        res = runner.invoke(cli_app, ["--config", cfg, "db", "stamp"])
        assert res.exit_code == 0, res.output
        assert migrate.schema_state(eng).kind == "current"
        assert _row_counts(eng) == before
    finally:
        eng.dispose()


def test_cli_stamp_on_an_empty_database_points_to_upgrade(settings, tmp_path):
    res = CliRunner().invoke(cli_app, ["--config", _config(settings, tmp_path), "db", "stamp"])
    assert res.exit_code == 1 and "chanakya db upgrade" in res.output


# ---------------------------------------------------------------- PostgreSQL helpers (no server needed)
def test_postgres_urls_are_spelled_for_psycopg3_and_never_printed_with_the_password():
    assert pgsupport.normalize_url("postgres://u:pw@h/db") == "postgresql+psycopg://u:pw@h/db"
    assert pgsupport.normalize_url("postgresql://u:pw@h/db") == "postgresql+psycopg://u:pw@h/db"
    assert pgsupport.normalize_url("postgresql+psycopg://u:pw@h/db") == "postgresql+psycopg://u:pw@h/db"
    assert pgsupport.normalize_url("sqlite:///./data/x.db") == "sqlite:///./data/x.db"
    shown = pgsupport.safe_url("postgresql://chanakya:synthetic-pass-7@db.internal/chanakya")
    assert "synthetic-pass-7" not in shown and "db.internal" in shown
    assert pgsupport.safe_url("sqlite:///C:/data/x.db") == "sqlite:///C:/data/x.db"


def test_a_postgres_session_is_pinned_to_utc_and_keeps_the_url_options():
    kwargs = pgsupport.engine_kwargs("postgresql+psycopg://u@h/db?options=-csearch_path%3Dchk_x")
    assert kwargs["connect_args"]["options"] == "-csearch_path=chk_x -c timezone=UTC"
    assert pgsupport.engine_kwargs("postgresql+psycopg://u@h/db")["connect_args"]["options"] == "-c timezone=UTC"


def test_nul_characters_are_removed_from_statement_parameters():
    params = {"title": "tender\x00 notice", "n": 3, "nested": ["a\x00", ("b\x00", None)]}
    assert pgsupport.strip_nul(params) == {"title": "tender notice", "n": 3, "nested": ["a", ("b", None)]}
    assert pgsupport.strip_nul([{"t": "x\x00"}, {"t": "y"}]) == [{"t": "x"}, {"t": "y"}]


def test_the_writer_lock_key_is_stable_and_does_not_depend_on_the_password():
    a = pgsupport.advisory_key("postgresql://u:one@h:5432/db")
    assert a == pgsupport.advisory_key("postgresql+psycopg://u:two@h:5432/db")
    assert a != pgsupport.advisory_key("postgresql://u:one@h:5432/other")
    assert -(2**63) <= a < 2**63


def test_string_columns_are_unbounded_on_postgres_and_unchanged_on_sqlite():
    pg = str(CreateTable(Opportunity.__table__).compile(dialect=postgresql.dialect()))
    lite = str(CreateTable(Opportunity.__table__).compile(dialect=sqlite.dialect()))
    assert "VARCHAR(" not in pg and "organisation TEXT" in pg
    assert "organisation VARCHAR(400)" in lite and "dedup_key VARCHAR(300) NOT NULL" in lite


def test_the_lock_file_moves_to_data_dir_and_registers_the_database_on_postgres(settings):
    from chanakya import desklock

    settings.app.database_url = "postgresql+psycopg://u@db.internal/chanakya"
    path = desklock.lock_path(settings)
    assert path == settings.app.data_dir / "watch.lock"
    assert desklock._postgres_urls[str(path)] == settings.app.database_url
    desklock._postgres_urls.pop(str(path))


def test_init_does_not_print_a_database_password(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHANAKYA_DATABASE_URL", "postgresql://chanakya:synthetic-pass-9@db.internal/chanakya")
    monkeypatch.setattr("chanakya.cli.open_session", lambda st: __import__("contextlib").nullcontext())
    res = CliRunner().invoke(cli_app, ["init"])
    assert res.exit_code == 0, res.output
    assert "synthetic-pass-9" not in res.output and "db.internal" in res.output


# ---------------------------------------------------------------- PostgreSQL only (CI)
@needs_postgres
def test_postgres_stores_what_sqlite_stores(engine):
    """Over-long text, a NUL character and a timezone-aware timestamp must not fail or shift."""
    from datetime import UTC

    init_db(engine)
    with session_factory(engine)() as s:
        s.add(Source(id="synthetic", type="fixture", name="x" * 5000, last_error="bad\x00byte",
                     last_success_at=datetime(2026, 10, 1, 4, 30, tzinfo=UTC)))
        s.commit()
    with session_factory(engine)() as s:
        row = s.get(Source, "synthetic")
        assert len(row.name) == 5000 and row.last_error == "badbyte"
        assert row.last_success_at == datetime(2026, 10, 1, 4, 30)


@needs_postgres
def test_postgres_lets_only_one_writer_hold_the_database(database_url):
    assert pgsupport.acquire_writer(database_url, "first") is True
    try:
        assert pgsupport.acquire_writer(database_url, "second") is False
    finally:
        pgsupport.release_writer("first")
    assert pgsupport.acquire_writer(database_url, "second") is True
    pgsupport.release_writer("second")


# ---------------------------------------------------------------- moving the data
def test_copy_data_moves_every_row_into_an_empty_database(tmp_path, database_url):
    """SQLite to the test database (SQLite here, PostgreSQL in CI): counts, values and NULLs survive."""
    from chanakya.db import AIAnalysisResult, EligibilityFindingRow

    source, target = make_engine(f"sqlite:///{tmp_path / 'source.db'}"), make_engine(database_url)
    try:
        _legacy_database(source)
        with session_factory(source)() as s:
            s.add(AIAnalysisResult(id=41, cache_key="k" * 64, purpose="triage", model="synthetic", prompt_version="1",
                                   kb_version="1", result={"b": 1, "a": [1, 2]}))
            s.add(EligibilityFindingRow(opportunity_id="CHK-synthetic01", criterion="turnover",
                                        requirement_text="SYNTHETIC requirement", evidence=None))
            s.commit()
        migrate.stamp(source)
        init_db(target)

        copied = migrate.copy_data(source, target)
        assert copied == _row_counts(source) == _row_counts(target)
        with session_factory(target)() as s:
            assert s.get(Opportunity, "CHK-synthetic01").title == "SYNTHETIC notice for the adoption test"
            assert s.get(AIAnalysisResult, 41).result == {"b": 1, "a": [1, 2]}
            assert s.scalar(select(func.count()).select_from(EligibilityFindingRow)
                            .where(EligibilityFindingRow.evidence.is_(None))) == 1
            # New rows must get ids above the copied ones (the PostgreSQL sequence was moved).
            fresh = AIAnalysisResult(cache_key="n" * 64, purpose="triage", model="synthetic", prompt_version="1",
                                     kb_version="1", result={})
            s.add(fresh)
            s.commit()
            assert fresh.id > 41
        with pytest.raises(migrate.SchemaOutOfDate, match="already has rows"):
            migrate.copy_data(source, target)
    finally:
        source.dispose()
        target.dispose()


def test_cli_stamp_first_adds_whole_tables_that_newer_models_introduced(settings, tmp_path):
    """The live database predated a model table. Adoption must cope, as every other command already does."""
    cfg, runner = _config(settings, tmp_path), CliRunner()
    eng = make_engine(settings.app.database_url)
    try:
        _legacy_database(eng)
        with eng.begin() as conn:
            conn.execute(text("DROP TABLE notification_rules"))
        before = _row_counts_existing(eng)
        res = runner.invoke(cli_app, ["--config", cfg, "db", "stamp"])
        assert res.exit_code == 0, res.output
        after = _row_counts(eng)
        assert after.pop("notification_rules") == 0 and after == before
        assert migrate.schema_state(eng).kind == "current"
    finally:
        eng.dispose()


def _row_counts_existing(engine) -> dict[str, int]:
    present = set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        return {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
                for t in sorted(Base.metadata.tables) if t in present}


def test_cli_upgrade_to_an_unknown_revision_fails_cleanly(settings, tmp_path):
    res = CliRunner().invoke(cli_app, ["--config", _config(settings, tmp_path), "db", "upgrade", "nope"])
    assert res.exit_code == 1 and "nope" in res.output
    assert "Traceback" not in res.output


def test_two_openers_arriving_at_a_new_database_together_both_succeed(database_url):
    """Regression: the desk opens a session per request while a pass starts on a thread.

    Before the fix the losers failed with "table already exists", "database is locked"
    or a duplicate schema_version row.
    """
    import threading

    errors: list[str] = []
    barrier = threading.Barrier(4)

    def opener() -> None:
        eng = make_engine(database_url)
        try:
            barrier.wait(timeout=30)
            init_db(eng)
        except Exception as exc:  # noqa: BLE001 - the test reports whatever went wrong
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            eng.dispose()

    threads = [threading.Thread(target=opener) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    eng = make_engine(database_url)
    try:
        assert migrate.schema_state(eng).kind == "current"
        with session_factory(eng)() as s:
            assert s.scalar(select(func.count()).select_from(SystemSetting)) == 1
    finally:
        eng.dispose()
