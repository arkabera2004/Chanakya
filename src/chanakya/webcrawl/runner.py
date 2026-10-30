"""Executes one WebsiteCrawlJob: runs ``crawl_website``, persists a ``WebsitePage`` row per
discovered URL, and updates the job's counters/status. Re-reads ``cancel_requested`` from the
database before each page (via ``should_stop``) so a request made from another thread/process
is observed without this module holding any in-memory job state of its own.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx

from ..config import DocumentSettings
from ..db import Website, WebsiteCrawlJob, WebsiteDocument, WebsitePage
from ..timeutil import now_utc
from .crawler import CrawlConfig, DiscoveredPage, crawl_website
from .documents import download_and_extract

_FAILED_STATUSES = {"failed"}


def _new_page_id() -> str:
    return f"wpg_{uuid.uuid4().hex[:16]}"


def _new_document_id() -> str:
    return f"wdoc_{uuid.uuid4().hex[:16]}"


def run_crawl_job(
    website_id: str, job_id: str, session_factory, *,
    client_factory: Callable[[], httpx.Client] = httpx.Client,
    resolve_dns: bool = True,
    documents_dir: Path | None = None,
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

        documents = []
        if not should_stop():
            doc_dir = documents_dir or Path(tempfile.mkdtemp(prefix="chanakya_website_docs_"))
            doc_settings = DocumentSettings()
            seen_doc_urls: set[str] = set()
            for page in pages:
                if page.status != "fetched":
                    continue
                for doc_url in page.document_links:
                    if doc_url in seen_doc_urls:
                        continue
                    seen_doc_urls.add(doc_url)
                    result = download_and_extract(client, doc_url, dest_dir=doc_dir, settings=doc_settings)
                    documents.append((page.url, doc_url, result))
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

        for source_page_url, doc_url, result in documents:
            session.add(WebsiteDocument(
                id=_new_document_id(), website_id=website_id, crawl_job_id=job_id,
                source_page_url=source_page_url, document_url=doc_url, local_path=result.local_path,
                sha256=result.sha256, content_type=result.content_type, status=result.status,
                extraction_error=result.error, extracted_text=result.extracted_text, created_at=now_utc(),
            ))

        job.pages_discovered = len(pages)
        job.pages_processed = sum(1 for p in pages if p.status == "fetched")
        job.pages_failed = failed
        job.documents_downloaded = len(documents)
        job.finished_at = now_utc()
        job.status = "cancelled" if was_cancelled else ("partial" if failed else "succeeded")

        website.last_crawl_at = job.finished_at
        website.last_crawl_status = job.status
        session.commit()
