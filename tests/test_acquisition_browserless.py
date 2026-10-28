"""Browserless adapter: mocked API tests.

Live verification note: one real call was made during development (POST /content against
https://example.com) and confirmed end to end through this exact adapter code, returning
HTTP 200 with rendered HTML and the X-Response-* diagnostic headers these tests assert
against. Not repeated here to avoid further billable usage on every test run.
"""

from __future__ import annotations

import httpx

from chanakya.acquisition import AcquisitionRequest
from chanakya.acquisition.providers.browserless_provider import BrowserlessProvider
from chanakya.config import BrowserlessSettings


def _provider(enabled: bool = True, token: str | None = "bl-test-key") -> BrowserlessProvider:
    return BrowserlessProvider(BrowserlessSettings(enabled=enabled), secret_resolver=lambda _env: token)


def test_disabled_provider_is_not_configured():
    assert _provider(enabled=False).is_configured() is False


def test_missing_token_is_not_configured():
    p = _provider(token=None)
    assert p.is_configured() is False
    assert "BROWSERLESS_API_KEY" in p.validate_config()[0]


def test_successful_render_returns_html_and_diagnostics():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["token"] == "bl-test-key"
        return httpx.Response(
            200, text="<html>hi</html>",
            headers={"x-response-status": "ok", "x-response-code": "200", "x-response-url": "https://x/"},
        )

    client = httpx.Client(base_url="https://chrome.browserless.io", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "ok"
    assert res.raw_content == "<html>hi</html>"
    assert res.diagnostics["upstream_status"] == "ok"


def test_selectors_extract_a_tender_record():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text='<html><body><h1 class="title">Supply of Widgets</h1><span class="org">Synthetic Dept</span></body></html>',
            headers={"x-response-status": "ok", "x-response-code": "200", "x-response-url": "https://x/"},
        )

    client = httpx.Client(base_url="https://chrome.browserless.io", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"selectors": {"title": "h1.title", "organisation": "span.org"}})
    res = _provider().fetch(req, client=client)
    assert res.status == "ok"
    assert len(res.records) == 1
    assert res.records[0].title == "Supply of Widgets"
    assert res.records[0].organisation == "Synthetic Dept"


def test_invalid_token_is_classified_blocked():
    def handler(req):
        return httpx.Response(401, text="unauthorized")

    client = httpx.Client(base_url="https://chrome.browserless.io", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "blocked" and res.retryable is False


def test_rate_limit_is_classified_failed_and_retryable():
    def handler(req):
        return httpx.Response(429, text="too many requests")

    client = httpx.Client(base_url="https://chrome.browserless.io", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "failed" and res.retryable is True


def test_server_error_is_classified_failed_and_retryable():
    def handler(req):
        return httpx.Response(502, text="bad gateway")

    client = httpx.Client(base_url="https://chrome.browserless.io", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "failed" and res.retryable is True
