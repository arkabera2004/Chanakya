"""Zyte adapter: mocked API tests only — BLOCKED_BY_CREDENTIALS, no key was provided.

No real call has been made to Zyte in this project. These tests exercise the adapter
against Zyte's publicly documented request/response schema, not a verified live schema.
"""

from __future__ import annotations

import base64

import httpx

from chanakya.acquisition import AcquisitionRequest
from chanakya.acquisition.providers.zyte_provider import ZyteProvider
from chanakya.config import ZyteSettings


def _provider(enabled: bool = True, api_key: str | None = "zyte-test-key") -> ZyteProvider:
    return ZyteProvider(ZyteSettings(enabled=enabled), secret_resolver=lambda _env: api_key)


def test_without_a_key_the_provider_is_blocked_by_credentials():
    p = ZyteProvider(ZyteSettings(enabled=True), secret_resolver=lambda _env: None)
    assert p.is_configured() is False
    assert "BLOCKED_BY_CREDENTIALS" in p.validate_config()[0]


def test_disabled_provider_is_not_configured():
    assert _provider(enabled=False).is_configured() is False


def test_http_response_body_is_base64_decoded():
    body_b64 = base64.b64encode(b"<html>hi</html>").decode()

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json={"url": "https://x/", "statusCode": 200, "httpResponseBody": body_b64})

    client = httpx.Client(base_url="https://api.zyte.com/v1", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "ok" and res.raw_content == "<html>hi</html>"
    assert res.diagnostics["status_code"] == 200


def test_browser_render_mode_requests_browser_html():
    def handler(req: httpx.Request) -> httpx.Response:
        import json

        assert json.loads(req.content)["browserHtml"] is True
        return httpx.Response(200, json={"url": "https://x/", "statusCode": 200, "browserHtml": "<html>rendered</html>"})

    client = httpx.Client(base_url="https://api.zyte.com/v1", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"browser_render": True})
    res = _provider().fetch(req, client=client)
    assert res.status == "ok" and res.raw_content == "<html>rendered</html>"


def test_invalid_key_is_classified_blocked():
    def handler(req):
        return httpx.Response(401)

    client = httpx.Client(base_url="https://api.zyte.com/v1", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "blocked" and res.retryable is False


def test_rate_limit_is_classified_failed_and_retryable():
    def handler(req):
        return httpx.Response(429)

    client = httpx.Client(base_url="https://api.zyte.com/v1", transport=httpx.MockTransport(handler))
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"), client=client)
    assert res.status == "failed" and res.retryable is True
