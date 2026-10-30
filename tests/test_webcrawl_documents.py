"""download_and_extract: fetch a document link, save it to disk, extract its text via the
existing chanakya.documents.extract_document (no new parser). All HTTP is mocked.
"""

from __future__ import annotations

import httpx

from chanakya.config import DocumentSettings
from chanakya.webcrawl.documents import download_and_extract


def test_download_and_extract_pdf_like_text_document(tmp_path):
    # A real PDF binary is overkill for this test; use a .txt extension so detect_type routes
    # to the plain-text path and still exercises the real extract_document() call.
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"Tender notice body text", headers={"content-type": "text/plain"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = download_and_extract(
        client, "https://example.gov.in/notice.txt", dest_dir=tmp_path, settings=DocumentSettings(),
    )
    assert result.status == "extracted"
    assert "Tender notice body text" in result.extracted_text
    assert result.sha256 is not None
    assert result.local_path is not None
    assert result.error is None


def test_download_and_extract_reports_http_failure():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    def run(tmp_path):
        return download_and_extract(
            client, "https://example.gov.in/missing.pdf", dest_dir=tmp_path, settings=DocumentSettings(),
        )

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        result = run(Path(d))
    assert result.status == "failed"
    assert result.error is not None
    assert result.extracted_text is None


def test_download_and_extract_enforces_max_bytes(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1000, headers={"content-type": "text/plain"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = download_and_extract(
        client, "https://example.gov.in/huge.txt", dest_dir=tmp_path, settings=DocumentSettings(), max_bytes=10,
    )
    assert result.status == "failed"
    assert "too large" in result.error.lower()
