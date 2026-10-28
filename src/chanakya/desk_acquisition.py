"""Desk routes for the unified acquisition system: provider status, job creation,
inspection, cancellation, and retry. One JobManager per process — its thread-pool
semaphore must outlive any single request, so it is built once and reused, with its
provider settings refreshed from the current config on every call (the desk reloads
config per request; see server.desk_settings).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel

from . import server
from .acquisition import registry
from .acquisition.contract import ProviderCapability
from .acquisition.job_manager import JobManager
from .config import Settings
from .db import AcquisitionJob, AcquisitionJobEvent, init_db, make_engine, session_factory


def require_api_token(authorization: Annotated[str | None, Header()] = None) -> None:
    """Bearer-token gate for every /api/acquisition/* route. A no-op (today's open-localhost
    behavior) when app.api_token_env is unset; once set, a missing/wrong token is a 401."""
    settings = server.desk_settings()
    expected = settings.secret(settings.app.api_token_env) if settings.app.api_token_env else None
    if not expected:
        return
    provided = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None
    if provided != expected:
        raise HTTPException(401, "missing or invalid acquisition API token")


router = APIRouter(dependencies=[Depends(require_api_token)])

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
