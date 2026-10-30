"""Download a document link discovered by the crawler and extract its text via the existing
``chanakya.documents.extract_document`` — this module owns fetching/saving only, no parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..config import DocumentSettings
from ..documents import extract_document
from ..security import safe_filename

DEFAULT_MAX_BYTES = 25 * 1024 * 1024


@dataclass
class DownloadResult:
    status: str  # downloaded | extracted | failed
    local_path: str | None = None
    sha256: str | None = None
    content_type: str | None = None
    extracted_text: str | None = None
    error: str | None = None


def _filename_for(url: str) -> str:
    name = urlsplit(url).path.rsplit("/", 1)[-1] or "document"
    return safe_filename(name)


def download_and_extract(
    client: httpx.Client, url: str, *, dest_dir: Path, settings: DocumentSettings,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> DownloadResult:
    try:
        resp = client.get(url, timeout=30.0)
    except httpx.HTTPError as exc:
        return DownloadResult(status="failed", error=f"request failed: {exc}")

    if resp.status_code >= 400:
        return DownloadResult(status="failed", error=f"HTTP {resp.status_code}")

    content = resp.content
    if len(content) > max_bytes:
        return DownloadResult(status="failed", error=f"document too large ({len(content)} bytes > {max_bytes})")

    dest_dir.mkdir(parents=True, exist_ok=True)
    local_path = dest_dir / _filename_for(url)
    local_path.write_bytes(content)

    try:
        doc = extract_document(local_path, settings)
    except Exception as exc:  # noqa: BLE001 - extraction failure is a recorded outcome, not a crash
        return DownloadResult(
            status="failed", local_path=str(local_path), content_type=resp.headers.get("content-type"),
            error=f"extraction failed: {exc}",
        )

    text = "\n\n".join(p.text for p in doc.all_pages())
    return DownloadResult(
        status="extracted", local_path=str(local_path), sha256=doc.sha256,
        content_type=resp.headers.get("content-type"), extracted_text=text,
    )
