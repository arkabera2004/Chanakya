"""Firecrawl adapter: mocked API tests (no credential/network required).

Live verification note: one real call was made during development (POST /v1/scrape against
https://example.com, 1 credit used) to confirm the response schema these tests assert
against; it is not repeated here to avoid further billable usage on every test run.
"""

from __future__ import annotations

import httpx

from chanakya.acquisition import AcquisitionRequest
from chanakya.acquisition.providers.firecrawl_provider import FirecrawlProvider
from chanakya.config import FirecrawlSettings


def _provider(enabled: bool = True, api_key: str | None = "fc-test-key") -> FirecrawlProvider:
    return FirecrawlProvider(FirecrawlSettings(enabled=enabled), secret_resolver=lambda _env: api_key)


def test_disabled_provider_is_not_configured():
    assert _provider(enabled=False).is_configured() is False


def test_missing_api_key_is_not_configured():
    p = _provider(api_key=None)
    assert p.is_configured() is False
    assert "FIRECRAWL_API_KEY" in p.validate_config()[0]


def test_successful_scrape_returns_markdown_and_usage():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer fc-test-key"
        return httpx.Response(200, json={
            "success": True,
            "data": {
                "markdown": "# Hello",
                "metadata": {"title": "Hello", "statusCode": 200, "creditsUsed": 1, "sourceURL": "https://x/"},
            },
        })

    client = httpx.Client(base_url="https://api.firecrawl.dev", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test")
    res = _provider().fetch(req, client=client)
    assert res.status == "ok"
    assert res.raw_content == "# Hello"
    assert res.diagnostics["credits_used"] == 1


def test_invalid_api_key_is_classified_blocked_not_failed():
    def handler(req):
        return httpx.Response(401, json={"success": False, "error": "Invalid API key"})

    client = httpx.Client(base_url="https://api.firecrawl.dev", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "blocked" and res.retryable is False


def test_rate_limit_is_classified_failed_and_retryable():
    def handler(req):
        return httpx.Response(429, json={"success": False, "error": "rate limited"})

    client = httpx.Client(base_url="https://api.firecrawl.dev", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "failed" and res.retryable is True


def test_provider_error_payload_is_surfaced():
    def handler(req):
        return httpx.Response(500, json={"success": False, "error": "internal error"})

    client = httpx.Client(base_url="https://api.firecrawl.dev", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "failed" and res.error == "internal error" and res.retryable is True
