"""The crawl-job runner: executes crawl_website against a real Website/WebsiteCrawlJob pair,
persists WebsitePage rows, and updates the job's counters/status. All HTTP is mocked.
"""

from __future__ import annotations

import httpx

from chanakya.db import (
    Website, WebsiteCrawlJob, WebsiteDocument, WebsitePage, WebsiteTender,
    init_db, make_engine, session_factory,
)
from chanakya.webcrawl.runner import run_crawl_job

BASE = "https://example.gov.in/"


def _client_factory(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _ok_html(text: str = "<html></html>") -> httpx.Response:
    return httpx.Response(200, text=text, headers={"content-type": "text/html"})


def _setup(settings):
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    sf = session_factory(engine)
    with sf() as session:
        session.add(Website(id="web_1", name="X", base_url=BASE, category="other"))
        session.add(WebsiteCrawlJob(id="wcj_1", website_id="web_1", status="queued"))
        session.commit()
    return sf


def test_run_crawl_job_persists_pages_and_marks_succeeded(settings):
    sf = _setup(settings)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return _ok_html('<a href="/tenders/1">t</a>')
        return _ok_html()

    run_crawl_job("web_1", "wcj_1", sf, client_factory=_client_factory(handler), resolve_dns=False)

    with sf() as session:
        job = session.get(WebsiteCrawlJob, "wcj_1")
        assert job.status == "succeeded"
        assert job.started_at is not None
        assert job.finished_at is not None
        assert job.pages_discovered == 2
        assert job.pages_processed == 2
        assert job.pages_failed == 0

        website = session.get(Website, "web_1")
        assert website.last_crawl_status == "succeeded"
        assert website.last_crawl_at is not None

        pages = session.query(WebsitePage).filter_by(crawl_job_id="wcj_1").all()
        assert {p.url for p in pages} == {BASE, "https://example.gov.in/tenders/1"}


def test_run_crawl_job_marks_partial_when_some_pages_fail(settings):
    sf = _setup(settings)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return _ok_html('<a href="/broken">b</a>')
        if path == "/broken":
            return httpx.Response(500)
        return _ok_html()

    run_crawl_job("web_1", "wcj_1", sf, client_factory=_client_factory(handler), resolve_dns=False)

    with sf() as session:
        job = session.get(WebsiteCrawlJob, "wcj_1")
        assert job.status == "partial"
        assert job.pages_failed == 1
        assert job.pages_processed == 1


def test_run_crawl_job_stops_when_cancel_requested(settings):
    sf = _setup(settings)
    with sf() as session:
        session.get(WebsiteCrawlJob, "wcj_1").cancel_requested = True
        session.commit()

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        return _ok_html('<a href="/a">a</a><a href="/b">b</a>')

    run_crawl_job("web_1", "wcj_1", sf, client_factory=_client_factory(handler), resolve_dns=False)

    with sf() as session:
        job = session.get(WebsiteCrawlJob, "wcj_1")
        assert job.status == "cancelled"


def test_run_crawl_job_downloads_and_extracts_linked_documents(settings, tmp_path):
    sf = _setup(settings)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return _ok_html('<a href="/notice.pdf">Notice</a>')
        if path == "/notice.pdf":
            # Magic-byte detection (not the extension) drives extraction, so plain text here
            # still exercises the real extract_document() text path.
            return httpx.Response(200, content=b"Tender notice body", headers={"content-type": "application/pdf"})
        return _ok_html()

    run_crawl_job(
        "web_1", "wcj_1", sf, client_factory=_client_factory(handler), resolve_dns=False,
        documents_dir=tmp_path,
    )

    with sf() as session:
        job = session.get(WebsiteCrawlJob, "wcj_1")
        assert job.documents_downloaded == 1

        docs = session.query(WebsiteDocument).filter_by(crawl_job_id="wcj_1").all()
        assert len(docs) == 1
        assert docs[0].document_url == "https://example.gov.in/notice.pdf"
        assert docs[0].source_page_url == BASE
        assert docs[0].status == "extracted"
        assert "Tender notice body" in docs[0].extracted_text


def test_run_crawl_job_persists_page_extracted_text(settings):
    sf = _setup(settings)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return _ok_html("<html><body><h1>Tender Notice</h1></body></html>")
        return _ok_html()

    run_crawl_job("web_1", "wcj_1", sf, client_factory=_client_factory(handler), resolve_dns=False)

    with sf() as session:
        home = session.query(WebsitePage).filter_by(crawl_job_id="wcj_1", url=BASE).one()
        assert "Tender Notice" in home.extracted_text


def test_run_crawl_job_extracts_tender_fields_from_page_text(settings):
    sf = _setup(settings)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return _ok_html("<html><body><p>Tender No: NHAI/2026/1</p>"
                             "<p>State: Tamil Nadu</p></body></html>")
        return _ok_html()

    run_crawl_job("web_1", "wcj_1", sf, client_factory=_client_factory(handler), resolve_dns=False)

    with sf() as session:
        job = session.get(WebsiteCrawlJob, "wcj_1")
        assert job.tenders_extracted == 1

        tenders = session.query(WebsiteTender).filter_by(crawl_job_id="wcj_1").all()
        assert len(tenders) == 1
        assert tenders[0].source_url == BASE
        assert tenders[0].fields["tender_id"]["value"] == "NHAI/2026/1"
        assert tenders[0].fields["state"]["value"] == "Tamil Nadu"


def test_run_crawl_job_skips_tender_record_when_nothing_found(settings):
    sf = _setup(settings)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return _ok_html("<html><body>Nothing tender-related here.</body></html>")
        return _ok_html()

    run_crawl_job("web_1", "wcj_1", sf, client_factory=_client_factory(handler), resolve_dns=False)

    with sf() as session:
        job = session.get(WebsiteCrawlJob, "wcj_1")
        assert job.tenders_extracted == 0
        assert session.query(WebsiteTender).filter_by(crawl_job_id="wcj_1").count() == 0


def test_run_crawl_job_matches_capabilities_when_knowledge_base_given(settings):
    from chanakya.pipeline.knowledge import load_knowledge_base

    sf = _setup(settings)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return _ok_html(
                "<html><body><p>Tender No: NHAI/2026/1</p>"
                "<p>Supply, installation and commissioning of AI based video analytics "
                "and surveillance system</p></body></html>",
            )
        return _ok_html()

    kb = load_knowledge_base(settings.app.capabilities_file)
    run_crawl_job(
        "web_1", "wcj_1", sf, client_factory=_client_factory(handler), resolve_dns=False,
        knowledge_base=kb, filtering_settings=settings.filtering,
    )

    with sf() as session:
        tender = session.query(WebsiteTender).filter_by(crawl_job_id="wcj_1").one()
        assert isinstance(tender.matches, list)
        assert len(tender.matches) > 0
        assert "capability_id" in tender.matches[0]
        assert "score" in tender.matches[0]


def test_run_crawl_job_skips_matching_when_no_knowledge_base_given(settings):
    sf = _setup(settings)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return _ok_html("<html><body><p>Tender No: NHAI/2026/1</p></body></html>")
        return _ok_html()

    run_crawl_job("web_1", "wcj_1", sf, client_factory=_client_factory(handler), resolve_dns=False)

    with sf() as session:
        tender = session.query(WebsiteTender).filter_by(crawl_job_id="wcj_1").one()
        assert tender.matches == []
