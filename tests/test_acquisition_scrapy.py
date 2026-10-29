"""Scrapy adapter: local-integration tests against a loopback HTTP server (no live portal,
no network egress). Each fetch() spawns a real subprocess running an isolated Scrapy
CrawlerProcess — slower than the other adapters' tests, but it is exercising the actual
subprocess boundary, not a shortcut around it.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from chanakya.acquisition import AcquisitionRequest
from chanakya.acquisition.providers.scrapy_provider import ScrapyProvider
from chanakya.config import ScrapySettings

pytest.importorskip("scrapy")


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b"<html><body>hello scrapy</body></html>"

    def do_GET(self):  # noqa: N802
        self.send_response(self.status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
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


def _provider() -> ScrapyProvider:
    return ScrapyProvider(ScrapySettings(enabled=True))


def test_disabled_provider_is_not_configured():
    assert ScrapyProvider(ScrapySettings(enabled=False)).is_configured() is False


def test_enabled_provider_with_package_installed_is_configured():
    assert _provider().is_configured() is True


def test_disallowed_domain_is_blocked_before_any_crawl(local_server):
    # Default allowed_domains excludes 127.0.0.1 — proves the SSRF guard actually runs,
    # not just that it's importable.
    res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
    assert res.status == "blocked" and res.retryable is False


def test_successful_fetch_returns_body_and_status(local_server, monkeypatch):
    import chanakya.acquisition.providers.scrapy_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
    assert res.status == "ok"
    assert "hello scrapy" in res.raw_content
    assert res.diagnostics["status_code"] == 200


def test_selectors_extract_a_tender_record(local_server, monkeypatch):
    import chanakya.acquisition.providers.scrapy_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    _Handler.body = b'<html><body><h1 class="title">Supply of Widgets</h1><span class="org">Synthetic Dept</span></body></html>'
    try:
        req = AcquisitionRequest(
            url=local_server, source_id="test", extra={"selectors": {"title": "h1.title", "organisation": "span.org"}},
        )
        res = _provider().fetch(req)
        assert res.status == "ok"
        assert len(res.records) == 1
        assert res.records[0].title == "Supply of Widgets"
        assert res.records[0].organisation == "Synthetic Dept"
    finally:
        _Handler.body = b"<html><body>hello scrapy</body></html>"


def test_403_is_classified_blocked(local_server, monkeypatch):
    import chanakya.acquisition.providers.scrapy_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    _Handler.status = 403
    try:
        res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
        assert res.status == "blocked" and res.retryable is False
    finally:
        _Handler.status = 200


def test_503_is_classified_failed_and_retryable(local_server, monkeypatch):
    import chanakya.acquisition.providers.scrapy_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    _Handler.status = 503
    try:
        res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
        assert res.status == "failed" and res.retryable is True
    finally:
        _Handler.status = 200
