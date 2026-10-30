"""Round-trip test for WebsiteCompanyProfile, the company-fact extraction record."""

from __future__ import annotations

from chanakya.db import Website, WebsiteCompanyProfile, WebsiteCrawlJob, init_db, make_engine, session_factory


def test_website_company_profile_roundtrip(settings):
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    sf = session_factory(engine)
    with sf() as session:
        session.add(Website(id="web_1", name="Acme", base_url="https://acme.example", category="company_website"))
        session.add(WebsiteCrawlJob(id="wcj_1", website_id="web_1", status="running"))
        session.commit()

        session.add(WebsiteCompanyProfile(
            id="wcp_1", website_id="web_1", crawl_job_id="wcj_1",
            source_url="https://acme.example/about",
            fields={"company_name": {"value": "Acme Robotics", "excerpt": "Acme Robotics", "method": "rule_based"}},
        ))
        session.commit()

        fetched = session.get(WebsiteCompanyProfile, "wcp_1")
        assert fetched.website_id == "web_1"
        assert fetched.fields["company_name"]["value"] == "Acme Robotics"
