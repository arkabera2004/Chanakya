"""The desk's /api/acquisition/* routes: provider status and job lifecycle.

Uses the same temporary-database `settings` fixture as the rest of the desk API tests.
A deterministic stub provider is registered directly into the real registry's cache
(keyed by the test's own settings object) so job execution doesn't depend on any network
call or real provider package.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from chanakya import server
from chanakya.acquisition.contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability
from chanakya.acquisition.registry import registry as the_registry
from chanakya.server import create_app


class _StubProvider(AcquisitionProvider):
    provider_id = "scrapling"
    capabilities = frozenset({ProviderCapability.HTTP_FETCH})
    requires_credentials = False

    def is_configured(self) -> bool:
        return True

    def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
        return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="ok")


@pytest.fixture
def desk(settings, monkeypatch):
    monkeypatch.setattr(server, "desk_settings", lambda: settings)
    built = the_registry.providers(settings.acquisition_providers)  # build the real 9, then override one
    built["scrapling"] = _StubProvider(settings.acquisition_providers.scrapling)
    yield settings
    the_registry._cache.pop(id(settings.acquisition_providers), None)


def _wait_terminal(client: TestClient, job_id: str, tries: int = 100) -> dict:
    for _ in range(tries):
        job = client.get(f"/api/acquisition/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "partial", "failed", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not reach a terminal status")


def test_no_token_configured_is_open_by_default(desk):
    """Preserves today's open-localhost desk behavior when app.api_token_env is unset."""
    with TestClient(create_app()) as client:
        assert client.get("/api/acquisition/providers").status_code == 200


def test_configured_token_rejects_missing_or_wrong_credentials(desk, monkeypatch):
    desk.app.api_token_env = "CHANAKYA_TEST_ACQ_TOKEN"
    monkeypatch.setenv("CHANAKYA_TEST_ACQ_TOKEN", "s3cret")
    with TestClient(create_app()) as client:
        assert client.get("/api/acquisition/providers").status_code == 401
        assert client.get("/api/acquisition/providers", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/api/acquisition/providers", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_providers_endpoint_lists_all_nine(desk):
    with TestClient(create_app()) as client:
        body = client.get("/api/acquisition/providers").json()
    ids = {p["provider_id"] for p in body["providers"]}
    assert ids == {"scrapling", "crawlee", "crawl4ai", "scrapy", "playwright", "firecrawl", "apify", "browserless", "zyte"}
    scrapling = next(p for p in body["providers"] if p["provider_id"] == "scrapling")
    assert scrapling["configured"] is True


def test_create_job_for_unknown_provider_returns_200_with_failed_status(desk):
    with TestClient(create_app()) as client:
        resp = client.post("/api/acquisition/jobs", json={"provider_id": "not-real", "url": "https://x/"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert "unknown acquisition provider" in body["error"]


def test_create_job_runs_to_success_and_is_inspectable(desk):
    with TestClient(create_app()) as client:
        created = client.post("/api/acquisition/jobs", json={"provider_id": "scrapling", "url": "https://x/"}).json()
        assert created["status"] == "queued"
        job = _wait_terminal(client, created["id"])
        assert job["status"] == "succeeded"
        assert any(e["kind"] == "succeeded" for e in job["events"])

        listed = client.get("/api/acquisition/jobs").json()["jobs"]
        assert any(j["id"] == created["id"] for j in listed)

        filtered = client.get("/api/acquisition/jobs", params={"status": "succeeded"}).json()["jobs"]
        assert all(j["status"] == "succeeded" for j in filtered)


def test_analytics_with_no_jobs_is_zero_not_unavailable(desk):
    with TestClient(create_app()) as client:
        body = client.get("/api/acquisition/analytics").json()
    assert body["jobs_total"] == 0
    assert body["by_status"] == {} and body["by_provider"] == {}
    assert body["avg_duration_s"] is None, "no terminal job exists yet, so duration is unknown, not 0"


def test_analytics_reflects_a_real_completed_job(desk):
    with TestClient(create_app()) as client:
        created = client.post("/api/acquisition/jobs", json={"provider_id": "scrapling", "url": "https://x/"}).json()
        _wait_terminal(client, created["id"])
        body = client.get("/api/acquisition/analytics").json()
    assert body["jobs_total"] == 1
    assert body["by_status"]["succeeded"] == 1
    assert body["by_provider"]["scrapling"]["succeeded"] == 1
    assert body["avg_duration_s"] is not None and body["avg_duration_s"] >= 0


def test_get_unknown_job_is_404(desk):
    with TestClient(create_app()) as client:
        resp = client.get("/api/acquisition/jobs/does-not-exist")
    assert resp.status_code == 404


def test_cancel_of_a_terminal_job_is_409(desk):
    with TestClient(create_app()) as client:
        created = client.post("/api/acquisition/jobs", json={"provider_id": "scrapling", "url": "https://x/"}).json()
        _wait_terminal(client, created["id"])
        resp = client.post(f"/api/acquisition/jobs/{created['id']}/cancel")
    assert resp.status_code == 409


def test_retry_of_a_non_retryable_job_is_409(desk):
    with TestClient(create_app()) as client:
        created = client.post("/api/acquisition/jobs", json={"provider_id": "not-real", "url": "https://x/"}).json()
        resp = client.post(f"/api/acquisition/jobs/{created['id']}/retry")
    assert resp.status_code == 409


def test_unknown_capability_is_400(desk):
    with TestClient(create_app()) as client:
        resp = client.post("/api/acquisition/jobs", json={"provider_id": "scrapling", "url": "https://x/", "capability": "not-a-real-capability"})
    assert resp.status_code == 400
