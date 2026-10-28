"""Unified job manager: creates and executes AcquisitionJob rows against the provider
registry, persists every state transition, and feeds any TenderRecords a provider returns
through the existing pipeline.store.upsert_record — no second normalization/dedup path.

Jobs run on a bounded daemon thread pool so a FastAPI request creating a job never blocks on
the provider's own network call; the caller gets the job id back immediately and polls (or
is pushed) status via the DB row.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid

from sqlalchemy.orm import Session, sessionmaker

from ..config import AcquisitionProvidersSettings
from ..db import AcquisitionJob, AcquisitionJobEvent
from ..timeutil import now_utc
from .contract import AcquisitionRequest, ProviderCapability
from .registry import registry

logger = logging.getLogger("chanakya.acquisition.job_manager")

_TERMINAL_STATUSES = {"succeeded", "partial", "failed", "cancelled"}


def _new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:16]}"


def _record_event(session: Session, job_id: str, kind: str, message: str = "", data: dict | None = None) -> None:
    session.add(AcquisitionJobEvent(job_id=job_id, kind=kind, message=message, data=data or {}))


class JobManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider_settings: AcquisitionProvidersSettings,
        *,
        max_concurrency: int = 3,
        max_attempts: int = 2,
        sleep=time.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._provider_settings = provider_settings
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._semaphore = threading.Semaphore(max_concurrency)
        # Request-specific extras (e.g. a selector map) are not persisted on the AcquisitionJob
        # row — they can be arbitrary/unbounded and are only needed once, at run time — so they
        # live in this process-local dict between create_job() and the thread that runs it.
        self._pending_extra: dict[str, dict] = {}
        self.recover_orphaned_jobs()

    def recover_orphaned_jobs(self) -> int:
        """A job left ``queued`` or ``running`` when the process last stopped has no live
        thread behind it any more — nothing will ever finish it. Mark each one ``failed``
        (retryable, so ``retry()`` can create a fresh job) rather than leaving it stuck
        forever. Safe to call multiple times; only touches non-terminal rows. Returns the
        number of jobs recovered."""
        with self._session_factory() as session:
            orphans = session.query(AcquisitionJob).filter(AcquisitionJob.status.in_(["queued", "running"])).all()
            for job in orphans:
                job.status, job.finished_at = "failed", now_utc()
                job.error, job.retryable = "orphaned: no running process when the application restarted", True
                _record_event(session, job.id, "failed", job.error)
            if orphans:
                session.commit()
            return len(orphans)

    def update_provider_settings(self, provider_settings: AcquisitionProvidersSettings) -> None:
        """Refresh the provider config this manager reads from — e.g. after the desk config
        file (or its profile overlay) is reloaded — without rebuilding the thread pool/
        semaphore that bounds concurrency across the process's whole lifetime."""
        self._provider_settings = provider_settings

    def update_session_factory(self, session_factory: sessionmaker[Session]) -> None:
        """Refresh the session factory — e.g. the database URL changed between config
        reloads — without rebuilding the thread pool/semaphore."""
        self._session_factory = session_factory

    def jobs_session(self) -> Session:
        """A new Session for read-only inspection by callers outside this module (e.g. the
        API layer listing/filtering jobs). Use as ``with jm.jobs_session() as session:``."""
        return self._session_factory()

    def create_job(
        self, *, provider_id: str, url: str, source_id: str | None = None,
        capability: ProviderCapability = ProviderCapability.HTTP_FETCH, extra: dict | None = None,
        scan_run_id: str | None = None, dedupe_pending: bool = True,
    ) -> AcquisitionJob:
        """Create (or, if ``dedupe_pending``, reuse) an AcquisitionJob row. Does not execute
        it — call ``submit`` to run it on the bounded thread pool."""
        with self._session_factory() as session:
            try:
                provider = registry.get(self._provider_settings, provider_id)
            except KeyError:
                job = AcquisitionJob(
                    id=_new_job_id(), provider_id=provider_id, source_id=source_id, url=url,
                    capability=capability.value, status="failed", created_at=now_utc(), finished_at=now_utc(),
                    error=f"unknown acquisition provider '{provider_id}'", retryable=False,
                )
                session.add(job)
                _record_event(session, job.id, "failed", job.error)
                session.commit()
                return job

            if dedupe_pending:
                existing = (
                    session.query(AcquisitionJob)
                    .filter(AcquisitionJob.provider_id == provider_id, AcquisitionJob.url == url,
                            AcquisitionJob.status.in_(["queued", "running"]))
                    .order_by(AcquisitionJob.created_at.desc())
                    .first()
                )
                if existing is not None:
                    return existing

            problems = provider.validate_config()
            job = AcquisitionJob(
                id=_new_job_id(), provider_id=provider_id, source_id=source_id, url=url,
                capability=capability.value, scan_run_id=scan_run_id, created_at=now_utc(),
                max_attempts=self._max_attempts,
                status="failed" if problems else "queued",
                error="; ".join(problems) if problems else None,
                finished_at=now_utc() if problems else None,
            )
            session.add(job)
            _record_event(session, job.id, "created", "; ".join(problems) if problems else "job created",
                          {"extra": extra} if extra else {})
            session.commit()
            session.refresh(job)
            if extra is not None:
                self._pending_extra[job.id] = extra  # see submit(); kept out of the DB row by design
            return job

    def submit(self, job_id: str) -> bool:
        """Run a queued job on the bounded thread pool. Returns False if the job isn't in a
        runnable state (already running/terminal, or unknown)."""
        with self._session_factory() as session:
            job = session.get(AcquisitionJob, job_id)
            if job is None or job.status != "queued":
                return False
        thread = threading.Thread(target=self._run_bounded, args=(job_id,), name=f"acq-job-{job_id}", daemon=True)
        thread.start()
        return True

    def _run_bounded(self, job_id: str) -> None:
        with self._semaphore:
            self._run(job_id)

    def _run(self, job_id: str) -> None:
        extra = self._pending_extra.pop(job_id, {})
        with self._session_factory() as session:
            job = session.get(AcquisitionJob, job_id)
            if job is None:
                return
            if job.cancel_requested:
                job.status, job.finished_at = "cancelled", now_utc()
                _record_event(session, job_id, "cancelled", "cancelled before the first attempt")
                session.commit()
                return
            job.status, job.started_at = "running", now_utc()
            _record_event(session, job_id, "started")
            session.commit()
            provider_id, url, capability_value = job.provider_id, job.url, job.capability
            source_id = job.source_id

        provider = registry.get(self._provider_settings, provider_id)
        capability = ProviderCapability(capability_value)
        request = AcquisitionRequest(url=url, source_id=source_id or provider_id, capability=capability, extra=extra)

        attempt = 1
        while True:
            try:
                result = provider.fetch(request)
            except Exception as exc:  # a provider bug, not an expected ok/blocked/failed outcome
                logger.exception("acquisition job %s: provider %s raised unexpectedly", job_id, provider_id)
                with self._session_factory() as session:
                    job = session.get(AcquisitionJob, job_id)
                    job.status, job.finished_at, job.attempt = "failed", now_utc(), attempt
                    job.error, job.retryable = f"{type(exc).__name__}: {exc}", False
                    _record_event(session, job_id, "failed", job.error)
                    session.commit()
                return

            with self._session_factory() as session:
                job = session.get(AcquisitionJob, job_id)
                if job.cancel_requested:
                    job.status, job.finished_at, job.attempt = "cancelled", now_utc(), attempt
                    _record_event(session, job_id, "cancelled", "cancel requested mid-run")
                    session.commit()
                    return

                if result.status == "ok":
                    stored = self._store_records(session, result, source_id)
                    job.status = "succeeded" if stored["errors"] == 0 else "partial"
                    job.finished_at, job.attempt = now_utc(), attempt
                    job.record_count = stored["count"]
                    job.diagnostics = {**result.diagnostics, "stored": stored}
                    _record_event(session, job_id, "succeeded", data=job.diagnostics)
                    session.commit()
                    return

                if result.status == "blocked":
                    job.status, job.finished_at, job.attempt = "failed", now_utc(), attempt
                    job.error, job.retryable = result.error, False
                    job.diagnostics = result.diagnostics
                    _record_event(session, job_id, "blocked", result.error or "", result.diagnostics)
                    session.commit()
                    return

                # status == "failed"
                job.attempt, job.error, job.retryable = attempt, result.error, result.retryable
                job.diagnostics = result.diagnostics
                if result.retryable and attempt < job.max_attempts:
                    _record_event(session, job_id, "retrying", result.error or "")
                    session.commit()
                else:
                    job.status, job.finished_at = "failed", now_utc()
                    _record_event(session, job_id, "failed", result.error or "", result.diagnostics)
                    session.commit()
                    return
            attempt += 1
            self._sleep(min(2 ** attempt, 10))

    def _store_records(self, session: Session, result, source_id: str | None) -> dict:
        from ..pipeline import store

        count, errors = 0, 0
        for rec in result.records:
            try:
                store.upsert_record(session, rec, run_id=None)
                count += 1
            except Exception:
                logger.exception("acquisition: failed to store a record from %s", rec.source_id)
                errors += 1
        if result.records:
            session.commit()
        return {"count": count, "errors": errors}

    def cancel(self, job_id: str) -> bool:
        with self._session_factory() as session:
            job = session.get(AcquisitionJob, job_id)
            if job is None or job.status in _TERMINAL_STATUSES:
                return False
            job.cancel_requested = True
            _record_event(session, job_id, "cancelled", "cancellation requested")
            session.commit()
            return True

    def retry(self, job_id: str) -> AcquisitionJob | None:
        """Create a fresh job for a failed, retryable job's provider/url, keeping history
        rather than mutating the original row."""
        with self._session_factory() as session:
            job = session.get(AcquisitionJob, job_id)
            if job is None or job.status != "failed" or not job.retryable:
                return None
            provider_id, url, source_id, capability = job.provider_id, job.url, job.source_id, job.capability
        return self.create_job(
            provider_id=provider_id, url=url, source_id=source_id,
            capability=ProviderCapability(capability), dedupe_pending=False,
        )
