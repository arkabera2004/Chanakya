"""Which database the test suite runs on.

Default: a fresh SQLite file per test, exactly as before. With
``CHANAKYA_TEST_DATABASE_URL`` set to a PostgreSQL URL, every test gets its own
empty schema in that database instead (the schema is named in the URL's
``options``, so every engine the application opens from the settings lands in
it). The CI job ``test-postgres`` sets the variable.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ENV_VAR = "CHANAKYA_TEST_DATABASE_URL"


def postgres_base_url() -> str | None:
    return os.environ.get(ENV_VAR) or None


@contextmanager
def isolated_database(tmp_path: Path, name: str = "test") -> Iterator[str]:
    """Yield the URL of an empty database that belongs to one test."""
    base = postgres_base_url()
    if base is None:
        yield f"sqlite:///{tmp_path / 'data' / f'{name}.db'}"
        return
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from chanakya.db import make_engine
    from chanakya.pgsupport import normalize_url

    schema = f"chk_{uuid.uuid4().hex[:16]}"
    admin = make_engine(base)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    parsed = make_url(normalize_url(base))
    options = " ".join(filter(None, [str(parsed.query.get("options") or ""), f"-csearch_path={schema}"]))
    try:
        yield parsed.update_query_dict({"options": options}).render_as_string(hide_password=False)
    finally:
        try:
            with admin.begin() as conn:
                # A test that leaked a session must not hang the suite: give up after 5 s.
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        except Exception:  # noqa: BLE001 - the CI database is thrown away after the job
            pass
        admin.dispose()
