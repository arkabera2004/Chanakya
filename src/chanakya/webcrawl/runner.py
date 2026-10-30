"""Executes one WebsiteCrawlJob: runs ``crawl_website``, persists a ``WebsitePage`` row per
discovered URL, and updates the job's counters/status. Re-reads ``cancel_requested`` from the
database before each page (via ``should_stop``) so a request made from another thread/process
is observed without this module holding any in-memory job state of its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx

from ..db import Website, WebsiteCrawlJob, WebsitePage
from ..timeutil import now_utc
from .crawler import CrawlConfig, DiscoveredPage, crawl_website

_FAILED_STATUSES = {"failed"}


def _new_page_id() -> str:
    return f"wpg_{uuid.uuid4().hex[:16]}"


def run_crawl_job(
    website_id: str, job_id: str, session_factory, *,
    client_factory: Callable[[], httpx.Client] = httpx.Client,
    resolve_dns: bool = True,
) -> None:
    """Synchronous: the caller decides whether to run this on a background thread."""
    with session_factory() as session:
        website = session.get(Website, website_id)
        job = session.get(WebsiteCrawlJob, job_id)
        if website is None or job is None:
            return
        job.status = "running"
        job.started_at = now_utc()
        session.commit()
        base_url = website.base_url
        crawl_config = dict(website.crawl_config or {})

    def should_stop() -> bool:
        with session_factory() as session:
            current = session.get(WebsiteCrawlJob, job_id)
            return current is None or current.cancel_requested

    config = CrawlConfig(
        max_pages=crawl_config.get("max_pages", 50),
        max_depth=crawl_config.get("max_depth", 2),
        extra_allowed_domains=crawl_config.get("extra_allowed_domains", []),
        resolve_dns=resolve_dns,
    )

    client = client_factory()
    try:
        pages: list[DiscoveredPage] = crawl_website(base_url, config, client, should_stop=should_stop)
    finally:
        client.close()

    with session_factory() as session:
        job = session.get(WebsiteCrawlJob, job_id)
        website = session.get(Website, website_id)
        if job is None or website is None:
            return

        was_cancelled = job.cancel_requested
        failed = 0
        for page in pages:
            session.add(WebsitePage(
                id=_new_page_id(), website_id=website_id, crawl_job_id=job_id,
                url=page.url, depth=page.depth, status=page.status,
                http_status=page.http_status, content_type=page.content_type, error=page.error,
                discovered_at=now_utc(),
            ))
            if page.status in _FAILED_STATUSES:
                failed += 1

        job.pages_discovered = len(pages)
        job.pages_processed = sum(1 for p in pages if p.status == "fetched")
        job.pages_failed = failed
        job.finished_at = now_utc()
        job.status = "cancelled" if was_cancelled else ("partial" if failed else "succeeded")

        website.last_crawl_at = job.finished_at
        website.last_crawl_status = job.status
        session.commit()
