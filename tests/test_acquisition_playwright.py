"""Playwright adapter: local-integration tests against a loopback HTTP server (no live
portal, no network egress — the server binds to 127.0.0.1 only). Real Chromium launch,
real navigation, real DOM content extraction.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from chanakya.acquisition import AcquisitionRequest
from chanakya.acquisition.providers.playwright_provider import PlaywrightProvider
from chanakya.config import HttpSettings, PlaywrightSettings

pytest.importorskip("playwright.sync_api")


class _Handler(http.server.BaseHTTPRequestHandler):
    body = b"<html><body><h1 id='t'>Hello Playwright</h1></body></html>"
    status = 200

    def do_GET(self):  # noqa: N802 (stdlib API name)
        self.send_response(self.status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):  # silence default stderr logging
        pass


@pytest.fixture
def local_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _provider() -> PlaywrightProvider:
    return PlaywrightProvider(PlaywrightSettings(enabled=True, headless=True))


def test_disabled_provider_is_not_configured():
    p = PlaywrightProvider(PlaywrightSettings(enabled=False))
    assert p.is_configured() is False


def test_enabled_provider_with_chromium_installed_is_configured():
    assert _provider().is_configured() is True


def test_renders_a_page_and_returns_its_content(local_server, monkeypatch):
    # validate_url correctly blocks 127.0.0.1 as a private/reserved address — that security
    # check is tested on its own merits elsewhere (chanakya.security's own test coverage).
    # This loopback-only test exists to exercise navigation/content/status handling in
    # isolation, so the URL-validation step is bypassed here, not weakened in the adapter.
    import chanakya.acquisition.providers.playwright_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    req = AcquisitionRequest(url=local_server, source_id="test", extra={})
    res = _provider().fetch(req)
    assert res.status == "ok", res.error
    assert "Hello Playwright" in res.raw_content
    assert res.diagnostics["http_status"] == 200


def test_selectors_extract_a_tender_record(local_server, monkeypatch):
    import chanakya.acquisition.providers.playwright_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    _Handler.body = b'<html><body><h1 class="title">Supply of Widgets</h1><span class="org">Synthetic Dept</span></body></html>'
    try:
        req = AcquisitionRequest(
            url=local_server, source_id="test", extra={"selectors": {"title": "h1.title", "organisation": "span.org"}},
        )
        res = _provider().fetch(req)
        assert res.status == "ok", res.error
        assert len(res.records) == 1
        assert res.records[0].title == "Supply of Widgets"
        assert res.records[0].organisation == "Synthetic Dept"
    finally:
        _Handler.body = b"<html><body><h1 id='t'>Hello Playwright</h1></body></html>"


def test_disallowed_domain_is_blocked_before_any_navigation(local_server):
    req = AcquisitionRequest(
        url=local_server, source_id="test",
        extra={"http_settings": HttpSettings(allowed_domains=["eprocure.gov.in"]), "resolve_dns": False, "allow_http": True},
    )
    res = _provider().fetch(req)
    assert res.status == "blocked"


def test_server_error_status_is_classified_failed_and_retryable(local_server, monkeypatch):
    import chanakya.acquisition.providers.playwright_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    _Handler.status = 503
    try:
        req = AcquisitionRequest(url=local_server, source_id="test", extra={})
        res = _provider().fetch(req)
        assert res.status == "failed" and res.retryable is True
    finally:
        _Handler.status = 200
