"""The desk's /api/websites* routes: website CRUD and crawl-job lifecycle.

Uses the same temporary-database `settings` fixture as the other desk API tests.
No crawl execution here (Phase 2) — crawl-job routes only create rows and flip
status/flag columns, exactly like desk_acquisition.cancel_job does for the
existing JobManager.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chanakya import server
from chanakya.server import create_app


@pytest.fixture
def desk(settings, monkeypatch):
    monkeypatch.setattr(server, "desk_settings", lambda: settings)
    return settings


def _create_website(client: TestClient, **overrides) -> dict:
    body = {"name": "NHAI", "base_url": "https://nhai.gov.in", "category": "nhai"}
    body.update(overrides)
    r = client.post("/api/websites", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_create_list_get_website(desk):
    with TestClient(create_app()) as client:
        created = _create_website(client)
        wid = created["id"]
        assert created["enabled"] is True
        assert created["crawl_config"] == {}
        assert created["last_crawl_at"] is None

        listed = client.get("/api/websites").json()["websites"]
        assert any(w["id"] == wid for w in listed)

        fetched = client.get(f"/api/websites/{wid}").json()
        assert fetched["name"] == "NHAI"
        assert fetched["base_url"] == "https://nhai.gov.in"


def test_create_website_rejects_unknown_category(desk):
    with TestClient(create_app()) as client:
        r = client.post("/api/websites", json={
            "name": "X", "base_url": "https://x.example", "category": "not_a_category",
        })
        assert r.status_code == 400


def test_get_unknown_website_is_404(desk):
    with TestClient(create_app()) as client:
        assert client.get("/api/websites/nope").status_code == 404


def test_patch_website_updates_fields(desk):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        r = client.patch(f"/api/websites/{wid}", json={"enabled": False, "description": "d"})
        assert r.status_code == 200
        assert r.json()["enabled"] is False
        assert r.json()["description"] == "d"


def test_patch_unknown_website_is_404(desk):
    with TestClient(create_app()) as client:
        assert client.patch("/api/websites/nope", json={"enabled": False}).status_code == 404


def test_delete_website_cascades_crawl_jobs(desk):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        client.post(f"/api/websites/{wid}/crawl-jobs")
        assert client.delete(f"/api/websites/{wid}").status_code == 200
        assert client.get(f"/api/websites/{wid}").status_code == 404


def test_delete_unknown_website_is_404(desk):
    with TestClient(create_app()) as client:
        assert client.delete("/api/websites/nope").status_code == 404


def test_create_crawl_job_for_unknown_website_is_404(desk):
    with TestClient(create_app()) as client:
        assert client.post("/api/websites/nope/crawl-jobs").status_code == 404


def test_create_and_get_crawl_job(desk):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        j = client.post(f"/api/websites/{wid}/crawl-jobs").json()
        assert j["status"] == "queued"
        assert j["website_id"] == wid
        fetched = client.get(f"/api/websites/{wid}/crawl-jobs/{j['id']}").json()
        assert fetched["id"] == j["id"]

        listed = client.get(f"/api/websites/{wid}/crawl-jobs").json()["crawl_jobs"]
        assert any(x["id"] == j["id"] for x in listed)


def test_get_crawl_job_for_unknown_website_is_404(desk):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        j = client.post(f"/api/websites/{wid}/crawl-jobs").json()
        assert client.get(f"/api/websites/nope/crawl-jobs/{j['id']}").status_code == 404


def _set_job_status(settings, job_id: str, status: str) -> None:
    from chanakya.db import WebsiteCrawlJob, make_engine, session_factory

    engine = make_engine(settings.app.database_url)
    with session_factory(engine)() as session:
        row = session.get(WebsiteCrawlJob, job_id)
        row.status = status
        session.commit()


def test_cancel_terminal_job_is_409(desk, settings):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        job_id = client.post(f"/api/websites/{wid}/crawl-jobs").json()["id"]
        _set_job_status(settings, job_id, "succeeded")
        assert client.post(f"/api/websites/{wid}/crawl-jobs/{job_id}/cancel").status_code == 409


def test_cancel_queued_job_succeeds(desk):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        job_id = client.post(f"/api/websites/{wid}/crawl-jobs").json()["id"]
        r = client.post(f"/api/websites/{wid}/crawl-jobs/{job_id}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"


def test_pause_then_resume_running_job(desk, settings):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        job_id = client.post(f"/api/websites/{wid}/crawl-jobs").json()["id"]
        _set_job_status(settings, job_id, "running")

        r = client.post(f"/api/websites/{wid}/crawl-jobs/{job_id}/pause")
        assert r.status_code == 200 and r.json()["status"] == "paused"

        r = client.post(f"/api/websites/{wid}/crawl-jobs/{job_id}/resume")
        assert r.status_code == 200 and r.json()["status"] == "running"


def test_pause_non_running_job_is_409(desk):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        job_id = client.post(f"/api/websites/{wid}/crawl-jobs").json()["id"]
        assert client.post(f"/api/websites/{wid}/crawl-jobs/{job_id}/pause").status_code == 409


def test_resume_non_paused_job_is_409(desk):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        job_id = client.post(f"/api/websites/{wid}/crawl-jobs").json()["id"]
        assert client.post(f"/api/websites/{wid}/crawl-jobs/{job_id}/resume").status_code == 409


def test_start_queued_crawl_job_runs_it_to_completion(desk, monkeypatch):
    import httpx

    from chanakya import desk_websites

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    monkeypatch.setattr(desk_websites, "_client_factory",
                         lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(desk_websites, "_resolve_dns", False)

    with TestClient(create_app()) as client:
        wid = _create_website(client, base_url="https://example.gov.in/")["id"]
        job_id = client.post(f"/api/websites/{wid}/crawl-jobs").json()["id"]

        r = client.post(f"/api/websites/{wid}/crawl-jobs/{job_id}/start")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "succeeded"
        assert body["pages_processed"] == 1

        pages = client.get(f"/api/websites/{wid}/crawl-jobs/{job_id}/pages").json()["pages"]
        assert len(pages) == 1
        assert pages[0]["url"] == "https://example.gov.in/"


def test_start_non_queued_crawl_job_is_409(desk):
    with TestClient(create_app()) as client:
        wid = _create_website(client)["id"]
        job_id = client.post(f"/api/websites/{wid}/crawl-jobs").json()["id"]
        client.post(f"/api/websites/{wid}/crawl-jobs/{job_id}/cancel")
        assert client.post(f"/api/websites/{wid}/crawl-jobs/{job_id}/start").status_code == 409
