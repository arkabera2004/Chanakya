"""Scrapy adapter: one request per subprocess, result handed back as JSON.

Scrapy's ``CrawlerProcess`` starts a Twisted reactor, and a Twisted reactor can only be
started once per process — a second ``fetch()`` call in the same process raises
``ReactorNotRestartable``. Each call therefore spawns ``_scrapy_worker.py`` as an isolated
subprocess (bounded by ``download_timeout_s`` plus a fixed margin) and reads its JSON
result. This also satisfies the project's own instruction not to block normal FastAPI
requests on a long-running crawler: the subprocess, not this process's event loop, does
the waiting.

Scrapy's ``RobotsTxtMiddleware`` stays on (not overridden) — robots.txt is respected inside
the worker exactly like every other acquisition path in this project. Confirmed with a real
local run against https://example.com during development (``async def start()`` — Scrapy
2.13+ renamed the old sync ``start_requests`` entry point, found and fixed during
development, not assumed).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ...config import ScrapySettings
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability
from ..extraction import extract_by_selectors


class ScrapyProvider(AcquisitionProvider):
    provider_id = "scrapy"
    capabilities = frozenset({
        ProviderCapability.HTTP_FETCH, ProviderCapability.LINK_DISCOVERY, ProviderCapability.STRUCTURED_EXTRACTION,
    })
    requires_credentials = False

    def __init__(self, settings: ScrapySettings) -> None:
        super().__init__(settings)

    def is_configured(self) -> bool:
        if not self.settings.enabled:
            return False
        try:
            import scrapy  # noqa: F401
        except ImportError:
            return False
        return True

    def validate_config(self) -> list[str]:
        if not self.settings.enabled:
            return ["scrapy is disabled (acquisition_providers.scrapy.enabled = false)"]
        try:
            import scrapy  # noqa: F401
        except ImportError:
            return ["scrapy: package is not installed"]
        return []

    def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
        worker = Path(__file__).with_name("_scrapy_worker.py")
        with tempfile.TemporaryDirectory(prefix="chanakya-scrapy-") as tmp:
            out_path = Path(tmp) / "result.json"
            try:
                proc = subprocess.run(
                    [sys.executable, str(worker), request.url, str(out_path)],
                    capture_output=True, text=True,
                    timeout=self.settings.download_timeout_s + 15,
                )
            except subprocess.TimeoutExpired:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error="scrapy worker timed out", retryable=True,
                )

            if not out_path.exists():
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"scrapy worker produced no result (exit {proc.returncode}): {proc.stderr[-500:]}",
                    retryable=True,
                )

            outcome = json.loads(out_path.read_text(encoding="utf-8"))

        records: list = []
        diagnostics: dict = {"status_code": outcome.get("status_code")}
        selectors: dict[str, str] = request.extra.get("selectors", {})
        if outcome["status"] == "ok" and selectors and outcome.get("body"):
            records, review = extract_by_selectors(
                outcome["body"], selectors, source_id=request.source_id, url=outcome.get("url", request.url),
                platform=request.extra.get("platform", "scrapy"), provider_id=self.provider_id,
            )
            diagnostics["extracted"] = review if review else (records[0].raw if records else {})
            if review:
                diagnostics["needs_review"] = review

        return AcquisitionResult(
            provider_id=self.provider_id, url=outcome.get("url", request.url), status=outcome["status"],
            records=records, raw_content=outcome.get("body"), content_type=outcome.get("content_type"),
            error=outcome.get("error"), retryable=outcome.get("retryable", False),
            diagnostics=diagnostics,
        )
