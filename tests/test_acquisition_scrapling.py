"""Scrapling adapter: structured extraction over mocked HTTP responses.

Retrieval goes through chanakya.http.PoliteClient (SSRF/robots/retry already tested
elsewhere); this file only tests the Scrapling-specific selection/extraction behavior and
the adapter's own blocked/failed/needs-review classification.
"""

from __future__ import annotations

import httpx

from chanakya.acquisition import AcquisitionRequest
from chanakya.acquisition.providers.scrapling_provider import ScraplingProvider
from chanakya.config import HttpSettings, ScraplingSettings
from chanakya.http import PoliteClient


def _client(handler) -> PoliteClient:
    return PoliteClient(HttpSettings(min_interval_s=0, max_retries=1, backoff_base_s=0), transport=httpx.MockTransport(handler), sleep=lambda s: None, resolve_dns=False)


def _provider(enabled: bool = True) -> ScraplingProvider:
    return ScraplingProvider(ScraplingSettings(enabled=enabled))


def test_disabled_provider_is_not_configured():
    p = _provider(enabled=False)
    assert p.is_configured() is False
    assert "disabled" in p.validate_config()[0]


def test_enabled_provider_with_package_installed_is_configured():
    assert _provider(enabled=True).is_configured() is True


def test_extracts_mapped_selectors_into_a_tender_record():
    html = '<html><body><h1 class="title">Supply of Widgets</h1><span class="org">Synthetic Dept</span></body></html>'

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html)

    req = AcquisitionRequest(
        url="https://eprocure.gov.in/x", source_id="test",
        extra={"selectors": {"title": "h1.title", "organisation": "span.org"}},
    )
    res = _provider().fetch(req, client=_client(handler))
    assert res.status == "ok"
    assert len(res.records) == 1
    assert res.records[0].title == "Supply of Widgets"
    assert res.records[0].organisation == "Synthetic Dept"
    assert res.diagnostics["extracted"] == {"title": "Supply of Widgets", "organisation": "Synthetic Dept"}


def test_missing_title_is_kept_for_review_not_guessed():
    html = '<html><body><span class="org">Synthetic Dept</span></body></html>'

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html)

    req = AcquisitionRequest(
        url="https://eprocure.gov.in/x", source_id="test",
        extra={"selectors": {"title": "h1.title", "organisation": "span.org"}},
    )
    res = _provider().fetch(req, client=_client(handler))
    assert res.status == "ok"
    assert res.records == []
    assert res.diagnostics["needs_review"]["title"] is None


def test_captcha_page_is_classified_blocked_not_failed():
    html = "<html><body><form><img alt='Image CAPTCHA'></form></body></html>"

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403, text=html)

    req = AcquisitionRequest(url="https://eprocure.gov.in/x", source_id="test", extra={"selectors": {"title": "h1"}})
    res = _provider().fetch(req, client=_client(handler))
    assert res.status == "blocked"
    assert res.retryable is False


def test_server_error_is_classified_failed_and_retryable():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(503)

    req = AcquisitionRequest(url="https://eprocure.gov.in/x", source_id="test", extra={"selectors": {"title": "h1"}})
    res = _provider().fetch(req, client=_client(handler))
    assert res.status == "failed"
    assert res.retryable is True


def test_no_selectors_returns_raw_content_only():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<html>hi</html>")

    req = AcquisitionRequest(url="https://eprocure.gov.in/x", source_id="test")
    res = _provider().fetch(req, client=_client(handler))
    assert res.status == "ok" and res.records == [] and "hi" in res.raw_content
