"""Desk routes for the unified acquisition system: provider status, job creation,
inspection, cancellation, and retry. One JobManager per process — its thread-pool
semaphore must outlive any single request, so it is built once and reused, with its
provider settings refreshed from the current config on every call (the desk reloads
config per request; see server.desk_settings).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from . import server
from .acquisition import registry
from .acquisition.contract import ProviderCapability
from .acquisition.job_manager import JobManager
from .config import Settings
from .db import AcquisitionJob, AcquisitionJobEvent, init_db, make_engine, session_factory

# Auth: server.create_app() applies server.require_desk_auth to every route app-wide
# (including this router), so there is no separate gate here any more.
router = APIRouter()

_job_manager: JobManager | None = None


def _manager(settings: Settings) -> JobManager:
    global _job_manager
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    sf = session_factory(engine)
    if _job_manager is None:
        _job_manager = JobManager(sf, settings.acquisition_providers)
    else:
        _job_manager.update_provider_settings(settings.acquisition_providers)
        _job_manager.update_session_factory(sf)  # the database url can change between config reloads too
    return _job_manager


def _job_dict(job: AcquisitionJob) -> dict[str, Any]:
    return {
        "id": job.id, "provider_id": job.provider_id, "source_id": job.source_id, "url": job.url,
        "capability": job.capability, "status": job.status, "scan_run_id": job.scan_run_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "attempt": job.attempt, "max_attempts": job.max_attempts, "retryable": job.retryable,
        "error": job.error, "record_count": job.record_count, "diagnostics": job.diagnostics,
        "cancel_requested": job.cancel_requested,
    }


@router.get("/api/acquisition/providers")
def list_providers() -> dict[str, Any]:
    settings = server.desk_settings()
    statuses = registry.status(settings.acquisition_providers)
    return {"providers": [
        {
            "provider_id": s.provider_id, "enabled": s.enabled, "configured": s.configured,
            "capabilities": s.capabilities, "requires_credentials": s.requires_credentials,
            "problems": s.problems,
        }
        for s in statuses
    ]}


class CreateJobBody(BaseModel):
    provider_id: str
    url: str
    source_id: str | None = None
    capability: str = "http_fetch"
    extra: dict[str, Any] = {}


@router.post("/api/acquisition/jobs")
def create_job(body: CreateJobBody = Body(...)) -> dict[str, Any]:  # noqa: B008 - FastAPI's own idiom
    settings = server.desk_settings()
    try:
        capability = ProviderCapability(body.capability)
    except ValueError as exc:
        raise HTTPException(400, f"unknown capability '{body.capability}'") from exc
    jm = _manager(settings)
    job = jm.create_job(
        provider_id=body.provider_id, url=body.url, source_id=body.source_id,
        capability=capability, extra=body.extra or None,
    )
    if job.status == "queued":
        jm.submit(job.id)
    return _job_dict(job)


@router.get("/api/acquisition/jobs")
def list_jobs(status: str | None = None, provider_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    settings = server.desk_settings()
    jm = _manager(settings)
    limit = max(1, min(limit, 200))
    with jm.jobs_session() as session:
        q = session.query(AcquisitionJob)
        if status:
            q = q.filter(AcquisitionJob.status == status)
        if provider_id:
            q = q.filter(AcquisitionJob.provider_id == provider_id)
        rows = q.order_by(AcquisitionJob.created_at.desc()).limit(limit).all()
        return {"jobs": [_job_dict(r) for r in rows]}


@router.get("/api/acquisition/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    settings = server.desk_settings()
    jm = _manager(settings)
    with jm.jobs_session() as session:
        job = session.get(AcquisitionJob, job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        events = (
            session.query(AcquisitionJobEvent)
            .filter_by(job_id=job_id)
            .order_by(AcquisitionJobEvent.id)
            .all()
        )
        return {
            **_job_dict(job),
            "events": [
                {"at": e.at.isoformat() if e.at else None, "kind": e.kind, "message": e.message, "data": e.data}
                for e in events
            ],
        }


@router.post("/api/acquisition/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    settings = server.desk_settings()
    jm = _manager(settings)
    ok = jm.cancel(job_id)
    if not ok:
        raise HTTPException(409, "job is not in a cancellable state, or does not exist")
    return {"cancelled": True}


@router.post("/api/acquisition/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict[str, Any]:
    settings = server.desk_settings()
    jm = _manager(settings)
    new_job = jm.retry(job_id)
    if new_job is None:
        raise HTTPException(409, "job is not eligible for retry (must be failed and retryable)")
    jm.submit(new_job.id)
    return _job_dict(new_job)


@router.get("/api/acquisition/analytics")
def analytics() -> dict[str, Any]:
    """Real aggregates over persisted AcquisitionJob rows — nothing here is estimated or
    fabricated. ``jobs_total: 0`` means no jobs exist yet, not that metrics are unavailable;
    duration/retry figures are explicitly ``null`` when there is no terminal job to compute
    them from, so an empty dataset is never confused with a zero result."""
    settings = server.desk_settings()
    jm = _manager(settings)
    with jm.jobs_session() as session:
        jobs = session.query(AcquisitionJob).all()

    by_status: dict[str, int] = {}
    by_provider: dict[str, dict[str, int]] = {}
    durations_s: list[float] = []
    retried = 0
    records_produced = 0
    for job in jobs:
        by_status[job.status] = by_status.get(job.status, 0) + 1
        provider_counts = by_provider.setdefault(job.provider_id, {})
        provider_counts[job.status] = provider_counts.get(job.status, 0) + 1
        if job.attempt > 1:
            retried += 1
        records_produced += job.record_count
        if job.started_at and job.finished_at:
            durations_s.append((job.finished_at - job.started_at).total_seconds())

    return {
        "jobs_total": len(jobs),
        "by_status": by_status,
        "by_provider": by_provider,
        "records_produced": records_produced,
        "jobs_retried": retried,
        "avg_duration_s": round(sum(durations_s) / len(durations_s), 3) if durations_s else None,
        "p95_duration_s": round(sorted(durations_s)[int(len(durations_s) * 0.95)], 3) if durations_s else None,
    }
