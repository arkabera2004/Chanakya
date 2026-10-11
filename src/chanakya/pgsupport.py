"""PostgreSQL support that has no place in the schema module.

SQLite stays the default and nothing here runs for a SQLite URL. Each helper
exists because of one concrete difference between the two databases:

- URL spelling: SQLAlchemy reads ``postgresql://`` as psycopg2, which Chanakya
  does not install, and rejects ``postgres://`` outright. Both are rewritten to
  the psycopg 3 driver (``postgresql+psycopg://``).
- Session time zone: timestamps are stored as naive UTC. A session that is not
  on UTC would shift any timezone-aware value on its way into a
  ``timestamp without time zone`` column, so every connection is pinned to UTC.
- NUL characters: SQLite stores ``\\x00`` inside text, PostgreSQL rejects the
  whole statement. Text scraped from portals and documents is untrusted, so
  NULs are removed from string parameters before they reach the server.
- Single writer: the lock file in ``desklock`` only protects one host. On
  PostgreSQL a session-level advisory lock adds a guard that holds across
  hosts and disappears by itself when the holder's connection dies.
- Secrets: a PostgreSQL URL can carry a password, so anything printed goes
  through :func:`safe_url`.

Verified on 2026-10-10 against an embedded PostgreSQL 18.4 on the build machine,
where the whole test suite passed. The CI job ``test-postgres`` (PostgreSQL 17
in a container) had not been seen running when this was written.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.engine.url import URL
from sqlalchemy.exc import ArgumentError

PG_DRIVER = "postgresql+psycopg"
_BARE_SCHEMES = ("postgres://", "postgresql://")


def normalize_url(url: str) -> str:
    """Spell a PostgreSQL URL with the psycopg 3 driver. Other URLs pass through untouched."""
    for scheme in _BARE_SCHEMES:
        if url.startswith(scheme):
            return f"{PG_DRIVER}://{url.removeprefix(scheme)}"
    return url


def is_postgres(url: str) -> bool:
    return url.startswith(("postgresql", "postgres://"))


def safe_url(url: str) -> str:
    """The URL with any password masked, for logs and command output."""
    try:
        parsed = make_url(normalize_url(url))
    except ArgumentError:
        return "<unparseable database URL>"
    if parsed.password is None:
        return url  # nothing to hide; keeps a SQLite path readable
    return parsed.render_as_string(hide_password=True)


def _session_options(url: URL) -> str:
    """libpq ``options`` with the session pinned to UTC, keeping whatever the URL already asked for."""
    existing = url.query.get("options") or ""
    if isinstance(existing, tuple):
        existing = " ".join(existing)
    return f"{existing} -c timezone=UTC".strip()


def engine_kwargs(url: str) -> dict[str, Any]:
    """Keyword arguments for ``create_engine`` on PostgreSQL."""
    return {
        # Managed PostgreSQL closes idle connections; test a pooled one before use.
        "pool_pre_ping": True,
        "connect_args": {"options": _session_options(make_url(url))},
    }


def strip_nul(value: Any) -> Any:
    """Remove NUL characters from strings, looking inside the containers a DBAPI call receives."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {k: strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    if isinstance(value, tuple):
        return tuple(strip_nul(v) for v in value)
    return value


def _strip_nul_params(conn, cursor, statement, parameters, context, executemany):
    return statement, strip_nul(parameters)


def configure_engine(engine: Engine) -> None:
    """Attach the PostgreSQL-only listeners. Call once per engine."""
    event.listen(engine, "before_cursor_execute", _strip_nul_params, retval=True)


def require_driver() -> None:
    """Fail with an instruction, not an ImportError, when the optional driver is missing."""
    try:
        import psycopg  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "PostgreSQL needs the optional driver. Install it with: pip install 'chanakya[postgres]'"
        ) from exc


# ---------------------------------------------------------------- single writer
_locks_guard = threading.Lock()
_held: dict[str, tuple[Engine, Connection]] = {}


def advisory_key(url: str) -> int:
    """A stable signed 64-bit key for one database and schema (the password never enters the hash)."""
    parsed = make_url(normalize_url(url))
    identity = "|".join([
        "chanakya-writer", parsed.host or "", str(parsed.port or ""), parsed.database or "",
        str(parsed.query.get("options") or ""),
    ])
    return int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest()[:8], "big", signed=True)


def acquire_writer(url: str, token: str | Path) -> bool:
    """Take the cross-host writer lock. ``token`` names the holder (the lock file path).

    Returns False when another session already holds it. The lock lives on a
    dedicated connection that stays open until :func:`release_writer`; if this
    process dies, PostgreSQL releases the lock when the connection drops.
    """
    from .db import make_engine

    key = str(token)
    with _locks_guard:
        if key in _held:
            return True
        engine = make_engine(url)
        try:
            conn = engine.connect()
        except Exception:
            engine.dispose()
            raise
        try:
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": advisory_key(url)}).scalar())
            conn.commit()  # a session-level advisory lock outlives the transaction
        except Exception:
            conn.close()
            engine.dispose()
            raise
        if not got:
            conn.close()
            engine.dispose()
            return False
        _held[key] = (engine, conn)
        return True


def release_writer(token: str | Path) -> None:
    """Give the writer lock back. Safe to call when nothing is held."""
    with _locks_guard:
        entry = _held.pop(str(token), None)
    if entry is None:
        return
    engine, conn = entry
    try:
        conn.execute(text("SELECT pg_advisory_unlock_all()"))
        conn.commit()
    except Exception:  # noqa: BLE001 - a dead connection has already released the lock
        pass
    finally:
        try:
            conn.close()
        finally:
            engine.dispose()  # closes the pooled connection, which also ends the session
