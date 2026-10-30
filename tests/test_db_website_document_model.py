"""Round-trip test for WebsiteDocument, the downloaded-and-extracted document record."""

from __future__ import annotations

from chanakya.db import Website, WebsiteCrawlJob, WebsiteDocument, init_db, make_engine, session_factory


def test_website_document_roundtrip(settings):
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    sf = session_factory(engine)
    with sf() as session:
        session.add(Website(id="web_1", name="NHAI", base_url="https://nhai.gov.in", category="nhai"))
        session.add(WebsiteCrawlJob(id="wcj_1", website_id="web_1", status="running"))
        session.commit()

        session.add(WebsiteDocument(
            id="wdoc_1", website_id="web_1", crawl_job_id="wcj_1",
            source_page_url="https://nhai.gov.in/tenders/1", document_url="https://nhai.gov.in/notice.pdf",
            local_path="/data/website_documents/web_1/wcj_1/notice.pdf", sha256="a" * 64,
            content_type="application/pdf", status="extracted", extracted_text="Tender notice text",
        ))
        session.commit()

        fetched = session.get(WebsiteDocument, "wdoc_1")
        assert fetched.website_id == "web_1"
        assert fetched.status == "extracted"
        assert fetched.extracted_text == "Tender notice text"
        assert fetched.extraction_error is None
