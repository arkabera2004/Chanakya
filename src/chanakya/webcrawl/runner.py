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

from ..config import DocumentSettings, FilteringSettings
from ..db import Website, WebsiteCompanyProfile, WebsiteCrawlJob, WebsiteDocument, WebsitePage, WebsiteTender
from ..pipeline.knowledge import KnowledgeBase
from ..timeutil import now_utc
from .capability_matching import build_tender_record, match_capabilities
from .company_extraction import extract_company_fields
from .crawler import CrawlConfig, DiscoveredPage, crawl_website
from .documents import download_and_extract
from .tender_extraction import extract_tender_fields

COMPANY_CATEGORY = "company_website"

_FAILED_STATUSES = {"failed"}


def _new_page_id() -> str:
    return f"wpg_{uuid.uuid4().hex[:16]}"


def _new_document_id() -> str:
    return f"wdoc_{uuid.uuid4().hex[:16]}"


def _new_tender_id() -> str:
    return f"wtd_{uuid.uuid4().hex[:16]}"


def _new_company_profile_id() -> str:
    return f"wcp_{uuid.uuid4().hex[:16]}"


def _company_fields_json(text: str, title: str | None) -> dict | None:
    fields = extract_company_fields(text, page_title=title)
    if not any(f.value is not None for f in fields.values()):
        return None
    return {name: {"value": f.value, "excerpt": f.excerpt, "method": f.method} for name, f in fields.items()}


def _tender_fields_json(text: str) -> dict | None:
    """Returns the field dict only when at least one field was actually found — a page or
    document with nothing tender-related in it gets no WebsiteTender row at all, rather than
    one full of "not found" noise."""
    fields = extract_tender_fields(text)
    # opportunity_type always resolves to something non-None for non-empty text (it falls back
    # to "tender"), so it alone must not count as "found" — only fields with real evidence do.
    if not any(f.excerpt is not None for f in fields.values()):
        return None
    return {name: {"value": f.value, "excerpt": f.excerpt, "method": f.method} for name, f in fields.items()}


def _capability_matches(source_url: str, fields: dict, text: str, kb: KnowledgeBase, filtering: FilteringSettings) -> list[dict]:
    record = build_tender_record(source_url, fields, text)
    result = match_capabilities(record, kb, filtering)
    return [
        {"capability_id": hit.capability_id, "score": hit.score, "strong": hit.strong, "medium": hit.medium}
        for hit in result.top
    ]


def run_crawl_job(
    website_id: str, job_id: str, session_factory, *,
    client_factory: Callable[[], httpx.Client] = httpx.Client,
    resolve_dns: bool = True,
    documents_dir: Path | None = None,
    knowledge_base: KnowledgeBase | None = None,
    filtering_settings: FilteringSettings | None = None,
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
        category = website.category
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
                extracted_text=page.text, discovered_at=now_utc(),
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

        tenders_extracted = 0
        if category == COMPANY_CATEGORY:
            for page in pages:
                if page.status != "fetched" or not page.text:
                    continue
                company_fields = _company_fields_json(page.text, page.title)
                if company_fields is None:
                    continue
                session.add(WebsiteCompanyProfile(
                    id=_new_company_profile_id(), website_id=website_id, crawl_job_id=job_id,
                    source_url=page.url, fields=company_fields, created_at=now_utc(),
                ))
        else:
            for page in pages:
                if page.status != "fetched" or not page.text:
                    continue
                tender_fields = _tender_fields_json(page.text)
                if tender_fields is None:
                    continue
                matches = (
                    _capability_matches(page.url, tender_fields, page.text, knowledge_base, filtering_settings)
                    if knowledge_base is not None and filtering_settings is not None else []
                )
                session.add(WebsiteTender(
                    id=_new_tender_id(), website_id=website_id, crawl_job_id=job_id,
                    source_url=page.url, fields=tender_fields, matches=matches, created_at=now_utc(),
                ))
                tenders_extracted += 1
            for _source_page_url, doc_url, result in documents:
                if result.status != "extracted" or not result.extracted_text:
                    continue
                tender_fields = _tender_fields_json(result.extracted_text)
                if tender_fields is None:
                    continue
                matches = (
                    _capability_matches(doc_url, tender_fields, result.extracted_text, knowledge_base, filtering_settings)
                    if knowledge_base is not None and filtering_settings is not None else []
                )
                session.add(WebsiteTender(
                    id=_new_tender_id(), website_id=website_id, crawl_job_id=job_id,
                    source_url=doc_url, fields=tender_fields, matches=matches, created_at=now_utc(),
                ))
                tenders_extracted += 1

        job.pages_discovered = len(pages)
        job.pages_processed = sum(1 for p in pages if p.status == "fetched")
        job.pages_failed = failed
        job.documents_downloaded = len(documents)
        job.tenders_extracted = tenders_extracted
        job.finished_at = now_utc()
        job.status = "cancelled" if was_cancelled else ("partial" if failed else "succeeded")

        website.last_crawl_at = job.finished_at
        website.last_crawl_status = job.status
        session.commit()
