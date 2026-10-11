"""One writer per desk database.

The lock file sits next to the SQLite file (in ``data_dir`` for any other
database). A live pid holds it. A dead pid is stale and may be replaced. On
PostgreSQL the file is backed by an advisory lock, so two hosts cannot both write. Stop is a flag the watch loop reads between
sources and between page chunks. Events for a run are appended beside the
database so a CLI watch and a desk watch narrate the same lines.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings

GEPNIC_CAPTION = (
    "The public home list is about ten notices. "
    "The active-tender search asks for a CAPTCHA and is not submitted."
)

# Portals the desk does not fetch. State notices still arrive on the CPPP feed,
# or a person drops the export in the manual inbox. This is policy, not a failure.
RESTRICTED_PORTALS: list[dict[str, str]] = [
    {
        "id": "mahatenders",
        "name": "Maharashtra eProcurement",
        "reason": (
            "robots.txt disallows the site. State tenders still arrive on the CPPP state feed, "
            "or by manual import."
        ),
    },
    {
        "id": "gem_bidplus",
        "name": "GeM bidplus",
        "reason": (
            "The bidplus listing is restricted. The public GeM mirror on CPPP is the feed this desk reads. "
            "Manual import is the other path."
        ),
    },
    {
        "id": "ireps",
        "name": "IREPS",
        "reason": "IREPS is restricted. Use manual import. This desk does not log in or solve a CAPTCHA.",
    },
]


class LockHeld(Exception):
    def __init__(self, pid: int, mode: str) -> None:
        self.pid = pid
        self.mode = mode
        super().__init__(f"database writer already running (pid {pid}, mode {mode})")


def database_dir(settings: Settings) -> Path:
    url = settings.app.database_url
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        return Path(url.removeprefix("sqlite:///")).parent
    return Path(settings.app.data_dir)


# Lock file path -> database URL, filled only for PostgreSQL. ``acquire`` and
# ``release`` take a path, not settings, so this is how they learn that the
# file lock must be backed by a database advisory lock (see ``pgsupport``).
_postgres_urls: dict[str, str] = {}


def lock_path(settings: Settings) -> Path:
    path = database_dir(settings) / "watch.lock"
    url = settings.app.database_url
    if url.startswith(("postgresql", "postgres://")):
        # The lock file (in data_dir) guards one host. PostgreSQL can be reached
        # from several, so the writer also takes an advisory lock in the database.
        _postgres_urls[str(path)] = url
    return path


def _guard_database(path: Path) -> None:
    """PostgreSQL only: back the lock file with an advisory lock that holds across hosts."""
    url = _postgres_urls.get(str(path))
    if url is None:
        return
    from . import pgsupport

    try:
        granted = pgsupport.acquire_writer(url, path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if not granted:
        path.unlink(missing_ok=True)
        raise LockHeld(0, "a writer on another host")


def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_lock(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def held(path: Path) -> dict[str, Any] | None:
    """The lock counts only while its pid is alive. A leftover status=running row does not."""
    current = read_lock(path)
    if not current:
        return None
    try:
        pid = int(current.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if not pid_alive(pid):
        return None
    current["pid"] = pid
    return current


def acquire(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {**payload, "stop": False}
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            current = held(path)
            if current is not None:
                raise LockHeld(int(current["pid"]), str(current.get("mode") or "watch")) from None
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle)
        _guard_database(path)
        return
    current = held(path)
    raise LockHeld(int(current["pid"]) if current else 0, str((current or {}).get("mode") or "watch"))


def request_stop(path: Path) -> dict[str, Any] | None:
    current = held(path)
    if current is None:
        return None
    current["stop"] = True
    path.write_text(json.dumps(current), encoding="utf-8")
    return current


def stop_requested(path: Path) -> bool:
    current = read_lock(path)
    return bool(current and current.get("stop"))


def release(path: Path, pid: int) -> None:
    if pid == os.getpid() and str(path) in _postgres_urls:
        from . import pgsupport

        pgsupport.release_writer(path)
    current = read_lock(path)
    if not current:
        return
    try:
        owner = int(current.get("pid") or 0)
    except (TypeError, ValueError):
        owner = 0
    if owner == pid:
        path.unlink(missing_ok=True)


@contextmanager
def writer_lock(settings: Settings, mode: str, desk: str = "live"):
    """Hold the database for one command. Raises LockHeld when another writer is alive."""
    path = lock_path(settings)
    acquire(path, {
        "pid": os.getpid(), "desk": desk, "mode": mode,
        "started_at": datetime.utcnow().isoformat() + "Z", "pause_s": None,
    })
    try:
        yield path
    finally:
        release(path, os.getpid())


def append_event(directory: Path, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    stamped = {"at": datetime.utcnow().isoformat() + "Z", **event}
    path = directory / f"events-{run_id}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stamped, default=str) + "\n")
    return stamped


def read_events(directory: Path, run_id: str, *, after: str | None = None, limit: int = 400) -> list[dict[str, Any]]:
    path = directory / f"events-{run_id}.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if after and str(row.get("at") or "") <= after:
            continue
        rows.append(row)
    return rows[-limit:]


def spawn_watch(root: Path, pause_s: int = 600, retry_s: int = 30, config: Path | None = None) -> int:
    """Start ``chanakya watch`` as its own process so a desk restart does not kill it.

    ``config`` pins the watch to the config the desk itself is reading, so a desk started
    on the demo inbox (or a test) can never launch a watch against the default live config.
    """
    log_path = root / "data" / "watch.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, "-m", "chanakya", *(["--config", str(config)] if config else []),
         "watch", "--pause-s", str(pause_s), "--retry-s", str(retry_s), "--no-email"],
        cwd=str(root),
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        **kwargs,
    )
    return proc.pid
