"""Apify adapter: mocked API tests.

Live verification note: auth was confirmed during development against the free,
no-compute-cost GET /v2/users/me endpoint. Actor execution is tested only against mocked
responses matching Apify's documented schema — running a real Actor costs compute credits
and picking an appropriate one is a product decision, not made here.
"""

from __future__ import annotations

import httpx

from chanakya.acquisition import AcquisitionRequest
from chanakya.acquisition.providers.apify_provider import ApifyProvider
from chanakya.config import ApifySettings


def _provider(enabled: bool = True, token: str | None = "apify-test-token") -> ApifyProvider:
    return ApifyProvider(ApifySettings(enabled=enabled), secret_resolver=lambda _env: token)


def test_disabled_provider_is_not_configured():
    assert _provider(enabled=False).is_configured() is False


def test_missing_token_is_not_configured():
    p = _provider(token=None)
    assert p.is_configured() is False
    assert "APIFY_API_TOKEN" in p.validate_config()[0]


def test_fetch_requires_actor_id():
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"))
    assert res.status == "failed" and "actor_id" in res.error


def test_successful_run_returns_dataset_items():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/datasets/ds1/items":
            return httpx.Response(200, json=[{"title": "Tender A"}, {"title": "Tender B"}])
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor"})
    res = _provider().fetch(req, client=client)
    assert res.status == "ok"
    assert res.diagnostics["item_count"] == 2
    assert res.diagnostics["items"][0]["title"] == "Tender A"


def test_run_polls_until_terminal_status():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/actor-runs/run1":
            calls["n"] += 1
            status = "RUNNING" if calls["n"] == 1 else "SUCCEEDED"
            return httpx.Response(200, json={"data": {"id": "run1", "status": status, "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/datasets/ds1/items":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor", "max_polls": 3})
    res = _provider().fetch(req, client=client)
    assert res.status == "ok" and calls["n"] == 2


def test_failed_run_is_classified_failed():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"data": {"id": "run1", "status": "FAILED", "defaultDatasetId": None}})

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor"})
    res = _provider().fetch(req, client=client)
    assert res.status == "failed" and res.diagnostics["status"] == "FAILED"


def test_rate_limit_during_polling_is_classified_failed_and_retryable():
    """start_run() already classifies a 429 correctly (status != 401/403 -> failed,
    retryable=True) — but run_status(), called in the polling loop, is not wrapped in any
    error handling at all. A 429 (or any HTTP error) while polling currently propagates as
    an unhandled httpx.HTTPStatusError out of fetch(), which the real job-manager path
    catches generically as a provider bug (retryable=False) — losing the fact that a rate
    limit is genuinely transient and should be retried."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/actor-runs/run1":
            return httpx.Response(429, json={"error": {"type": "rate-limit-exceeded"}})
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor", "max_polls": 3})
    res = _provider().fetch(req, client=client)
    assert res.status == "failed" and res.retryable is True


def test_invalid_token_is_classified_blocked():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "token-not-provided"}})

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor"})
    res = _provider().fetch(req, client=client)
    assert res.status == "blocked" and res.retryable is False
