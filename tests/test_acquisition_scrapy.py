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


def test_successful_fetch_returns_body_and_status(local_server):
    res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
    assert res.status == "ok"
    assert "hello scrapy" in res.raw_content
    assert res.diagnostics["status_code"] == 200


def test_403_is_classified_blocked(local_server):
    _Handler.status = 403
    try:
        res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
        assert res.status == "blocked" and res.retryable is False
    finally:
        _Handler.status = 200


def test_503_is_classified_failed_and_retryable(local_server):
    _Handler.status = 503
    try:
        res = _provider().fetch(AcquisitionRequest(url=local_server, source_id="test"))
        assert res.status == "failed" and res.retryable is True
    finally:
        _Handler.status = 200
