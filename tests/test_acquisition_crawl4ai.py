"""Crawl4AI adapter: local-integration tests against a loopback HTTP server (no live
portal, no network egress). Real AsyncWebCrawler run via its own internal browser.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from chanakya.acquisition import AcquisitionRequest
from chanakya.acquisition.providers.crawl4ai_provider import Crawl4AIProvider
from chanakya.config import Crawl4AISettings

pytest.importorskip("crawl4ai")


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b"<html><body><h1>Hello Crawl4AI with enough visible text content here</h1></body></html>"
    redirect_to: str | None = None  # when set, GET /redirect-start returns a 302 to this URL

    def do_GET(self):  # noqa: N802
        if self.path == "/redirect-start" and self.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
            return
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


def _provider() -> Crawl4AIProvider:
    return Crawl4AIProvider(Crawl4AISettings(enabled=True))


def test_disabled_provider_is_not_configured():
    assert Crawl4AIProvider(Crawl4AISettings(enabled=False)).is_configured() is False


def test_enabled_provider_with_package_installed_is_configured():
    assert _provider().is_configured() is True


def test_llm_assisted_mode_is_explicitly_not_implemented():
    p = Crawl4AIProvider(Crawl4AISettings(enabled=True, llm_assisted=True))
    assert "not implemented" in p.validate_config()[0]


def test_disallowed_domain_is_blocked_before_any_crawl(local_server):
    req = AcquisitionRequest(url=local_server, source_id="test")  # default allowed_domains excludes 127.0.0.1
    res = _provider().fetch(req)
    assert res.status == "blocked"


def test_successful_crawl_returns_markdown(local_server, monkeypatch):
    import chanakya.acquisition.providers.crawl4ai_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
    assert res.status == "ok"
    assert "Hello Crawl4AI" in res.raw_content
    assert res.diagnostics["status_code"] == 200


def test_403_is_classified_blocked(local_server, monkeypatch):
    import chanakya.acquisition.providers.crawl4ai_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    _Handler.status = 403
    try:
        res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
        assert res.status == "blocked" and res.retryable is False
    finally:
        _Handler.status = 200


def test_labels_extract_a_tender_record_from_real_markdown(local_server, monkeypatch):
    import chanakya.acquisition.providers.crawl4ai_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    _Handler.body = (
        b"<html><body><p>Title: Supply of Widgets</p><p>Ref No: T-77</p>"
        b"<p>This tender notice has enough visible text content to avoid Crawl4AI's "
        b"near-empty-content anti-bot heuristic during this test.</p></body></html>"
    )
    try:
        req = AcquisitionRequest(
            url=local_server, source_id="test",
            extra={"labels": {"title": ["title"], "ref_no": ["ref no"]}},
        )
        res = _provider().fetch(req)
        assert res.status == "ok"
        assert len(res.records) == 1
        assert res.records[0].title == "Supply of Widgets"
        assert res.records[0].ref_no == "T-77"
    finally:
        _Handler.body = b"<html><body><h1>Hello Crawl4AI with enough visible text content here</h1></body></html>"


def test_503_is_classified_failed_and_retryable(local_server, monkeypatch):
    import chanakya.acquisition.providers.crawl4ai_provider as mod

    monkeypatch.setattr(mod, "validate_url", lambda *a, **k: a[0])
    _Handler.status = 503
    try:
        res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
        assert res.status == "failed" and res.retryable is True
    finally:
        _Handler.status = 200
