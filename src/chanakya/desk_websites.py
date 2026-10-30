"""Desk routes for Website Intelligence: website CRUD and crawl-job lifecycle.

Crawl jobs here only create rows and flip status/flag columns (``cancel_requested``,
``pause_requested``) — the same shape ``desk_acquisition.cancel_job`` uses for the
existing JobManager. The actual crawl execution that observes these flags and
advances the counters is a separate, later piece of work; this module is the
contract it will run against.

Auth: server.create_app() applies server.require_desk_auth to every route app-wide
(including this router), so there is no separate gate here either.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from . import server
from .db import Website, WebsiteCrawlJob, WebsiteDocument, WebsitePage, init_db, make_engine, session_factory
from .timeutil import now_utc
from .webcrawl.runner import run_crawl_job

router = APIRouter()

# Overridable by tests (monkeypatch.setattr) so /start runs against mocked HTTP, never the
# real network, in automated tests.
_client_factory = httpx.Client
_resolve_dns = True

VALID_CATEGORIES = {
    "government_procurement", "government_department", "nhai", "gepnic",
    "state_eprocurement", "psu", "company_website", "other",
}


def _session_factory():
    settings = server.desk_settings()
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    return session_factory(engine)


def _website_dict(w: Website) -> dict[str, Any]:
    return {
        "id": w.id, "name": w.name, "base_url": w.base_url, "category": w.category,
        "description": w.description, "enabled": w.enabled, "crawl_config": w.crawl_config,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "last_crawl_at": w.last_crawl_at.isoformat() if w.last_crawl_at else None,
        "last_crawl_status": w.last_crawl_status,
    }


def _crawl_job_dict(j: WebsiteCrawlJob) -> dict[str, Any]:
    return {
        "id": j.id, "website_id": j.website_id, "status": j.status,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "pages_discovered": j.pages_discovered, "pages_processed": j.pages_processed,
        "pages_failed": j.pages_failed, "documents_downloaded": j.documents_downloaded,
        "tenders_extracted": j.tenders_extracted, "error": j.error,
        "cancel_requested": j.cancel_requested, "pause_requested": j.pause_requested,
    }


def _require_category(category: str) -> None:
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, f"unknown category '{category}', expected one of {sorted(VALID_CATEGORIES)}")


class CreateWebsiteBody(BaseModel):
    name: str
    base_url: str
    category: str
    description: str | None = None
    enabled: bool = True
    crawl_config: dict[str, Any] = {}


class UpdateWebsiteBody(BaseModel):
    name: str | None = None
    base_url: str | None = None
    category: str | None = None
    description: str | None = None
    enabled: bool | None = None
    crawl_config: dict[str, Any] | None = None


def _new_website_id() -> str:
    import uuid

    return f"web_{uuid.uuid4().hex[:16]}"


def _new_crawl_job_id() -> str:
    import uuid

    return f"wcj_{uuid.uuid4().hex[:16]}"


@router.post("/api/websites")
def create_website(body: CreateWebsiteBody = Body(...)) -> dict[str, Any]:  # noqa: B008
    _require_category(body.category)
    sf = _session_factory()
    with sf() as session:
        website = Website(
            id=_new_website_id(), name=body.name, base_url=body.base_url, category=body.category,
            description=body.description, enabled=body.enabled, crawl_config=body.crawl_config,
            created_at=now_utc(),
        )
        session.add(website)
        session.commit()
        return _website_dict(website)


@router.get("/api/websites")
def list_websites() -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        rows = session.query(Website).order_by(Website.created_at.desc()).all()
        return {"websites": [_website_dict(w) for w in rows]}


@router.get("/api/websites/{website_id}")
def get_website(website_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        website = session.get(Website, website_id)
        if website is None:
            raise HTTPException(404, "unknown website")
        return _website_dict(website)


@router.patch("/api/websites/{website_id}")
def update_website(website_id: str, body: UpdateWebsiteBody = Body(...)) -> dict[str, Any]:  # noqa: B008
    if body.category is not None:
        _require_category(body.category)
    sf = _session_factory()
    with sf() as session:
        website = session.get(Website, website_id)
        if website is None:
            raise HTTPException(404, "unknown website")
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(website, field, value)
        session.commit()
        return _website_dict(website)


@router.delete("/api/websites/{website_id}")
def delete_website(website_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        website = session.get(Website, website_id)
        if website is None:
            raise HTTPException(404, "unknown website")
        session.query(WebsiteCrawlJob).filter_by(website_id=website_id).delete()
        session.delete(website)
        session.commit()
        return {"deleted": True}


@router.post("/api/websites/{website_id}/crawl-jobs")
def create_crawl_job(website_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        website = session.get(Website, website_id)
        if website is None:
            raise HTTPException(404, "unknown website")
        job = WebsiteCrawlJob(id=_new_crawl_job_id(), website_id=website_id, status="queued", created_at=now_utc())
        session.add(job)
        session.commit()
        return _crawl_job_dict(job)


@router.get("/api/websites/{website_id}/crawl-jobs")
def list_crawl_jobs(website_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        if session.get(Website, website_id) is None:
            raise HTTPException(404, "unknown website")
        rows = (
            session.query(WebsiteCrawlJob)
            .filter_by(website_id=website_id)
            .order_by(WebsiteCrawlJob.created_at.desc())
            .all()
        )
        return {"crawl_jobs": [_crawl_job_dict(j) for j in rows]}


def _get_crawl_job(session, website_id: str, job_id: str) -> WebsiteCrawlJob:
    if session.get(Website, website_id) is None:
        raise HTTPException(404, "unknown website")
    job = session.get(WebsiteCrawlJob, job_id)
    if job is None or job.website_id != website_id:
        raise HTTPException(404, "unknown crawl job")
    return job


@router.get("/api/websites/{website_id}/crawl-jobs/{job_id}")
def get_crawl_job(website_id: str, job_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        job = _get_crawl_job(session, website_id, job_id)
        return _crawl_job_dict(job)


@router.post("/api/websites/{website_id}/crawl-jobs/{job_id}/cancel")
def cancel_crawl_job(website_id: str, job_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        job = _get_crawl_job(session, website_id, job_id)
        if job.status not in ("queued", "running", "paused"):
            raise HTTPException(409, "crawl job is not in a cancellable state")
        job.cancel_requested = True
        job.status = "cancelled"
        job.finished_at = now_utc()
        session.commit()
        return _crawl_job_dict(job)


@router.post("/api/websites/{website_id}/crawl-jobs/{job_id}/pause")
def pause_crawl_job(website_id: str, job_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        job = _get_crawl_job(session, website_id, job_id)
        if job.status != "running":
            raise HTTPException(409, "crawl job must be running to pause")
        job.pause_requested = True
        job.status = "paused"
        session.commit()
        return _crawl_job_dict(job)


@router.post("/api/websites/{website_id}/crawl-jobs/{job_id}/start")
def start_crawl_job(website_id: str, job_id: str) -> dict[str, Any]:
    """Runs the crawl synchronously within the request.

    shortcut: a crawl bounded by the website's own page-limit config runs to completion (or a
    cancel/pause flag) before this call returns; a very large crawl would hold the request open
    for a long time. Upgrade to a background executor (as acquisition/job_manager.py already
    does for provider jobs) if/when crawl sizes outgrow a single request's timeout budget.
    """
    sf = _session_factory()
    with sf() as session:
        job = _get_crawl_job(session, website_id, job_id)
        if job.status != "queued":
            raise HTTPException(409, "crawl job must be queued to start")

    settings = server.desk_settings()
    documents_dir = Path(settings.app.data_dir) / "website_documents" / website_id / job_id
    run_crawl_job(
        website_id, job_id, sf, client_factory=_client_factory, resolve_dns=_resolve_dns,
        documents_dir=documents_dir,
    )

    with sf() as session:
        job = session.get(WebsiteCrawlJob, job_id)
        return _crawl_job_dict(job)


@router.get("/api/websites/{website_id}/crawl-jobs/{job_id}/pages")
def list_crawl_job_pages(website_id: str, job_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        _get_crawl_job(session, website_id, job_id)
        rows = session.query(WebsitePage).filter_by(crawl_job_id=job_id).order_by(WebsitePage.discovered_at).all()
        return {"pages": [
            {
                "id": p.id, "url": p.url, "depth": p.depth, "status": p.status,
                "http_status": p.http_status, "content_type": p.content_type, "error": p.error,
                "discovered_at": p.discovered_at.isoformat() if p.discovered_at else None,
            }
            for p in rows
        ]}


@router.get("/api/websites/{website_id}/crawl-jobs/{job_id}/documents")
def list_crawl_job_documents(website_id: str, job_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        _get_crawl_job(session, website_id, job_id)
        rows = (
            session.query(WebsiteDocument)
            .filter_by(crawl_job_id=job_id)
            .order_by(WebsiteDocument.created_at)
            .all()
        )
        return {"documents": [
            {
                "id": d.id, "source_page_url": d.source_page_url, "document_url": d.document_url,
                "local_path": d.local_path, "sha256": d.sha256, "content_type": d.content_type,
                "status": d.status, "extraction_error": d.extraction_error,
                "extracted_text": d.extracted_text,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ]}


@router.post("/api/websites/{website_id}/crawl-jobs/{job_id}/resume")
def resume_crawl_job(website_id: str, job_id: str) -> dict[str, Any]:
    sf = _session_factory()
    with sf() as session:
        job = _get_crawl_job(session, website_id, job_id)
        if job.status != "paused":
            raise HTTPException(409, "crawl job must be paused to resume")
        job.pause_requested = False
        job.status = "running"
        session.commit()
        return _crawl_job_dict(job)
