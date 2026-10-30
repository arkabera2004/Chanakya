"""Round-trip tests for the Website Intelligence models (Website, WebsiteCrawlJob)."""

from __future__ import annotations

from chanakya.db import Website, WebsiteCrawlJob, init_db, make_engine, session_factory


def test_website_and_crawl_job_roundtrip(settings):
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    sf = session_factory(engine)
    with sf() as session:
        site = Website(id="web_1", name="NHAI", base_url="https://nhai.gov.in", category="nhai", enabled=True)
        session.add(site)
        session.commit()

        job = WebsiteCrawlJob(id="wcj_1", website_id="web_1", status="queued")
        session.add(job)
        session.commit()

        fetched_site = session.get(Website, "web_1")
        assert fetched_site.name == "NHAI"
        assert fetched_site.crawl_config == {}
        assert fetched_site.last_crawl_at is None

        fetched_job = session.get(WebsiteCrawlJob, "wcj_1")
        assert fetched_job.website_id == "web_1"
        assert fetched_job.status == "queued"
        assert fetched_job.pages_discovered == 0
        assert fetched_job.cancel_requested is False
