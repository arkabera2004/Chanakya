"""Desk routes for the tender documents of one notice.

The portals keep tender documents behind a CAPTCHA, and Chanakya does not solve
it. A person downloads the documents and adds them here. They are stored where
a pass already looks: ``<inbox_dir>/documents/<notice id>/``, the same tree
``chanakya import --tender-id`` writes.

An upload is untrusted. The name is reduced to a safe basename, the size is
capped while the body is read, the type is taken from the bytes and not from the
extension, and an archive is opened with the same guarded extraction a pass uses.
Nothing is parsed for content here: reading the text is the pass's job.

Writes take the one-writer lock, so a watch or a pass that holds the database
gets a 409 instead of a file appearing under it mid-run.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
import traceback
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import server
from .app import open_session, run_pipeline
from .config import Settings
from .db import Opportunity, OpportunityDocument
from .desklock import LockHeld, acquire, held, release
from .documents.extract import detect_type, sha256_file
from .pipeline.runner import document_folders, documents_for
from .reporting.text import plain
from .security import SecurityError, ZipLimits, safe_extract_zip, safe_filename

router = APIRouter()

# Detected type -> the extension the stored file carries.
ACCEPTED = {"pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx", "zip": ".zip"}
READABLE_IN_ARCHIVE = ("pdf", "docx", "xlsx")
ACCEPTED_LABEL = "PDF, DOCX, XLSX or ZIP"


def max_upload_bytes(settings: Settings) -> int:
    """One document may be as large as one the HTTP client would download: ``http.max_download_mb``."""
    return int(settings.http.max_download_mb) * 1024 * 1024


def _busy_message(mode: str | None) -> str:
    what = {"watch": "A watch", "analyze": "A scoring run", "scan": "A pass", "upload": "Another upload"}.get(mode or "", "Another writer")
    return f"{what} already holds the database. Try again when it has finished."


@contextmanager
def _writer(settings: Settings, mode: str):
    """Hold the one-writer lock for a file change. 409 when a watch or a pass has it."""
    path = server.lock_path(settings)
    try:
        acquire(path, {
            "pid": os.getpid(), "desk": "live", "mode": mode,
            "started_at": datetime.utcnow().isoformat() + "Z", "pause_s": None,
        })
    except LockHeld as exc:
        raise HTTPException(409, _busy_message(exc.mode)) from exc
    try:
        yield
    finally:
        release(path, os.getpid())


def _refuse_if_busy(settings: Settings) -> None:
    info = held(server.lock_path(settings))
    if info is not None:
        raise HTTPException(409, _busy_message(str(info.get("mode") or "watch")))


def _notice(session: Session, opportunity_id: str) -> Opportunity:
    row = session.get(Opportunity, opportunity_id)
    if row is None:
        raise HTTPException(404, "unknown opportunity")
    return row


def _upload_folder(settings: Settings, o: Opportunity) -> Path:
    """Uploads land in the folder named after the notice id, which a pass always reads."""
    key = safe_filename(o.id)
    folder = settings.app.inbox_dir / "documents" / key
    if folder not in document_folders(settings, o):
        raise HTTPException(400, "this notice has an id that cannot name a folder")
    return folder


def _clean_name(raw: str) -> str:
    base = (raw or "").replace("\\", "/").split("/")[-1].strip(" .")
    if not base or not any(ch.isalnum() for ch in base):
        raise HTTPException(400, "The file needs a name.")
    return safe_filename(base)


def _with_extension(name: str, kind: str) -> str:
    """The stored name ends in the type the bytes are, whatever the upload called itself."""
    wanted = ACCEPTED[kind]
    if name.lower().endswith(wanted):
        return name
    return safe_filename(f"{name}{wanted}")


def _inspect_archive(path: Path, settings: Settings) -> list[str]:
    """Open the archive the guarded way and say which members a pass can read."""
    docs = settings.documents
    limits = ZipLimits(docs.max_zip_members, docs.max_zip_total_mb * 1024 * 1024, docs.max_zip_ratio)
    scratch = Path(tempfile.mkdtemp(prefix="chanakya_upload_"))
    try:
        try:
            members = safe_extract_zip(path, scratch, limits)
        except SecurityError as exc:
            raise HTTPException(400, f"This archive was rejected: {exc}.") from exc
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
            raise HTTPException(400, "This archive could not be opened.") from exc
        readable = [m.name for m in members if detect_type(m) in READABLE_IN_ARCHIVE]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if not readable:
        raise HTTPException(400, "This archive holds no PDF, DOCX or XLSX file, so there is nothing to read in it.")
    return readable


def _check_office_container(path: Path, settings: Settings) -> None:
    """DOCX and XLSX are archives too. Refuse one that is encrypted or expands past the archive cap."""
    cap = settings.documents.max_zip_total_mb * 1024 * 1024
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(400, "This file could not be opened.") from exc
    if any(info.flag_bits & 0x1 for info in infos):
        raise HTTPException(400, "This file is password protected. Save a copy without the password.")
    if sum(info.file_size for info in infos) > cap:
        raise HTTPException(400, f"This file expands to more than {settings.documents.max_zip_total_mb} MB and was not kept.")


def inspect_upload(path: Path, settings: Settings) -> tuple[str, list[str]]:
    """Type from the bytes, then the checks that type needs. Returns (type, readable archive members)."""
    kind = detect_type(path)
    if kind == "ole":
        raise HTTPException(415, "Legacy .doc and .xls files are not read. Save it as DOCX, XLSX or PDF and add that.")
    if kind not in ACCEPTED:
        raise HTTPException(415, f"Only {ACCEPTED_LABEL} files are accepted. This file is none of those.")
    if kind == "zip":
        return kind, _inspect_archive(path, settings)
    if kind in ("docx", "xlsx"):
        _check_office_container(path, settings)
    return kind, []


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _listing(settings: Settings, session: Session, o: Opportunity) -> dict[str, Any]:
    """The documents a pass would find for this notice, in the order it reads them."""
    limit = settings.budget.max_documents_per_tender
    inbox_folders = {folder.resolve(): folder.name for folder in document_folders(settings, o)}
    parsed = {
        row.sha256: row
        for row in session.scalars(select(OpportunityDocument).where(OpportunityDocument.opportunity_id == o.id)).all()
    }
    rows: list[dict[str, Any]] = []
    for index, path in enumerate(documents_for(settings, o)):
        try:
            stat = path.stat()
            kind = detect_type(path)
            record = parsed.get(sha256_file(path))
        except OSError:
            continue
        folder = inbox_folders.get(path.parent.resolve())
        rows.append({
            "name": path.name,
            "folder": folder,
            "origin": "inbox" if folder else "portal",
            "removable": folder is not None and path.name == safe_filename(path.name),
            "type": kind,
            "size_bytes": stat.st_size,
            "modified_at": _iso(stat.st_mtime),
            # A pass opens the first few only. The rest wait until one is removed.
            "within_limit": index < limit,
            "read": record is not None,
            "pages": record.pages if record else None,
            "needs_manual_review": bool(record.needs_manual_review) if record else None,
            "error": plain(record.error) if record and record.error else None,
        })
    return {
        "opportunity_id": o.id,
        "documents": rows,
        "max_documents": limit,
        "max_mb": settings.http.max_download_mb,
        "accepted": list(ACCEPTED),
        "analysis_state": o.analysis_state,
        "decision": o.filter_decision,
    }


@router.get("/api/opportunities/{opportunity_id}/documents")
def list_documents(opportunity_id: str) -> dict[str, Any]:
    settings = server.desk_settings()
    with open_session(settings) as session:
        return _listing(settings, session, _notice(session, opportunity_id))


@router.post("/api/opportunities/{opportunity_id}/documents")
async def upload_document(opportunity_id: str, request: Request, filename: str = "") -> dict[str, Any]:
    """One file per request, sent as the raw request body with its name in ``filename``."""
    settings = server.desk_settings()
    with open_session(settings) as session:
        _notice(session, opportunity_id)
    name = _clean_name(filename)
    cap = max_upload_bytes(settings)
    too_big = f"This file is larger than the {settings.http.max_download_mb} MB limit for one document."
    _refuse_if_busy(settings)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise HTTPException(413, too_big)

    # Received outside the documents tree, so a half-written file is never one a pass could read.
    incoming = settings.app.inbox_dir / ".uploads"
    incoming.mkdir(parents=True, exist_ok=True)
    partial = incoming / f"{secrets.token_hex(8)}.part"
    try:
        size = 0
        with open(partial, "wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > cap:
                    raise HTTPException(413, too_big)
                handle.write(chunk)
        if size == 0:
            raise HTTPException(400, "The file is empty.")
        kind, members = inspect_upload(partial, settings)
        name = _with_extension(name, kind)
        limit = settings.budget.max_documents_per_tender
        with _writer(settings, "upload"), open_session(settings) as session:
            o = _notice(session, opportunity_id)
            folder = _upload_folder(settings, o)
            target = folder / name
            replaced = target.is_file()
            if not replaced and len(documents_for(settings, o)) >= limit:
                raise HTTPException(
                    400,
                    f"This notice already has {limit} documents, the most read for one notice "
                    "(budget.max_documents_per_tender). Remove one before adding another.",
                )
            folder.mkdir(parents=True, exist_ok=True)
            os.replace(partial, target)
            return {
                "stored": {"name": name, "type": kind, "size_bytes": size, "replaced": replaced, "archive_members": members},
                **_listing(settings, session, o),
            }
    finally:
        partial.unlink(missing_ok=True)


@router.delete("/api/opportunities/{opportunity_id}/documents/{name}")
def remove_document(opportunity_id: str, name: str, folder: str | None = None) -> dict[str, Any]:
    """Remove a document a person added. Only files in this notice's inbox folders can go."""
    settings = server.desk_settings()
    if name != safe_filename(name) or Path(name).name != name:
        raise HTTPException(400, "That is not a stored document name.")
    with _writer(settings, "upload"), open_session(settings) as session:
        o = _notice(session, opportunity_id)
        folders = [f for f in document_folders(settings, o) if folder is None or f.name == folder]
        for candidate in folders:
            target = candidate / name
            if target.is_file() and target.resolve().parent == candidate.resolve():
                target.unlink()
                return _listing(settings, session, o)
        raise HTTPException(404, "That document is not attached to this notice.")


def _analyse_one(opportunity_id: str) -> None:
    """Re-assess one notice on the configured model, inside the same budget guard as any pass."""
    owns_lock = False
    path: Path | None = None

    def finish(status: str, error: str | None, **extra: Any) -> None:
        with server._job_lock:
            if server._job is not None:
                server._job.update({"status": status, "error": error, **extra})

    try:
        settings = server.desk_settings()
        path = server.lock_path(settings)
        acquire(path, {
            "pid": os.getpid(), "desk": "live", "mode": "analyze",
            "started_at": datetime.utcnow().isoformat() + "Z", "pause_s": None,
        })
        owns_lock = True
        server._append_event({
            "kind": "judge", "opportunity_id": opportunity_id,
            "message": "Reading what is attached to this notice. I am not opening a portal.",
        })
        with open_session(settings) as session:
            result = run_pipeline(
                settings, session, email=False, export=False, trigger="notice",
                skip_scan=True, assess_scope="all", only_ids=[opportunity_id],
                on_event=server._append_event,
            )
            finish(result.run.status, result.run.error, run_id=result.run.id, stats=server._run_row(result.run))
    except LockHeld as exc:
        finish("failed", _busy_message(exc.mode))
    except Exception as exc:  # noqa: BLE001 - surface the failure to the desk
        message = f"{type(exc).__name__}: {exc}"
        with server._job_lock:
            if server._job is not None:
                server._job["events"].append({
                    "at": datetime.utcnow().isoformat() + "Z", "kind": "error",
                    "message": message, "trace": traceback.format_exc()[-2000:],
                })
        finish("failed", message)
    finally:
        if owns_lock and path is not None:
            release(path, os.getpid())


@router.post("/api/opportunities/{opportunity_id}/analyze")
def analyse_notice(opportunity_id: str) -> dict[str, Any]:
    """Start a job that re-assesses this one notice. The desk follows it on ``GET /api/job``."""
    settings = server.desk_settings()
    with open_session(settings) as session:
        _notice(session, opportunity_id)
    _refuse_if_busy(settings)
    with server._job_lock:
        if server._job and server._job.get("status") == "running":
            raise HTTPException(409, "A pass is already running. Try again when it has finished.")
        server._job = {
            "id": datetime.utcnow().strftime("job_%Y%m%dT%H%M%S") + "_" + secrets.token_hex(2),
            "kind": "analyze",
            "opportunity_id": opportunity_id,
            "status": "running",
            "stage": "judge",
            "events": [],
            "error": None,
            "run_id": None,
            "stats": None,
        }
        snapshot = dict(server._job)
    threading.Thread(target=_analyse_one, args=(opportunity_id,), name="chanakya-analyse-one", daemon=True).start()
    return {"job": snapshot}
