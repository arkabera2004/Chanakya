"""Round-trip test for WebsitePage, the discovered-page record a crawl run persists."""

from __future__ import annotations

from chanakya.db import Website, WebsiteCrawlJob, WebsitePage, init_db, make_engine, session_factory


def test_website_page_roundtrip(settings):
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    sf = session_factory(engine)
    with sf() as session:
        session.add(Website(id="web_1", name="NHAI", base_url="https://nhai.gov.in", category="nhai"))
        session.add(WebsiteCrawlJob(id="wcj_1", website_id="web_1", status="running"))
        session.commit()

        session.add(WebsitePage(
            id="wpg_1", website_id="web_1", crawl_job_id="wcj_1",
            url="https://nhai.gov.in/tenders/1", depth=1, status="fetched",
            http_status=200, content_type="text/html",
        ))
        session.commit()

        fetched = session.get(WebsitePage, "wpg_1")
        assert fetched.website_id == "web_1"
        assert fetched.crawl_job_id == "wcj_1"
        assert fetched.status == "fetched"
        assert fetched.error is None


def test_website_page_stores_extracted_text(settings):
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    sf = session_factory(engine)
    with sf() as session:
        session.add(Website(id="web_2", name="X", base_url="https://x.example", category="other"))
        session.add(WebsiteCrawlJob(id="wcj_2", website_id="web_2", status="running"))
        session.commit()
        session.add(WebsitePage(
            id="wpg_2", website_id="web_2", crawl_job_id="wcj_2",
            url="https://x.example/", depth=0, status="fetched", extracted_text="Tender Notice",
        ))
        session.commit()
        assert session.get(WebsitePage, "wpg_2").extracted_text == "Tender Notice"
