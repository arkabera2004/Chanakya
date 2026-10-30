"""Round-trip test for WebsiteTender, the structured-extraction-with-evidence record."""

from __future__ import annotations

from chanakya.db import Website, WebsiteCrawlJob, WebsiteTender, init_db, make_engine, session_factory


def test_website_tender_roundtrip(settings):
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    sf = session_factory(engine)
    with sf() as session:
        session.add(Website(id="web_1", name="NHAI", base_url="https://nhai.gov.in", category="nhai"))
        session.add(WebsiteCrawlJob(id="wcj_1", website_id="web_1", status="running"))
        session.commit()

        session.add(WebsiteTender(
            id="wtd_1", website_id="web_1", crawl_job_id="wcj_1",
            source_url="https://nhai.gov.in/tenders/1",
            fields={"tender_id": {"value": "NHAI/1", "excerpt": "Tender No: NHAI/1", "method": "rule_based"}},
        ))
        session.commit()

        fetched = session.get(WebsiteTender, "wtd_1")
        assert fetched.website_id == "web_1"
        assert fetched.fields["tender_id"]["value"] == "NHAI/1"
