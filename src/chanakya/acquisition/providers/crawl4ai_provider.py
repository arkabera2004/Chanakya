"""Crawl4AI adapter: local HTML-to-Markdown extraction via ``crawl4ai.AsyncWebCrawler``.

Crawl4AI renders pages through its own internally-managed Playwright browser and is
async-first; this adapter runs it through ``asyncio.run()`` per ``fetch()`` call, same
pattern as the Crawlee and Playwright adapters. Confirmed by a real local run against
https://example.com during development (``result.success``, ``result.status_code``,
``result.markdown.raw_markdown``).

``llm_assisted`` extraction is intentionally not implemented in this phase: Crawl4AI's own
LLM extraction strategy would call out to a model provider of its own, duplicating
Chanakya's existing llm.* budget/provider machinery rather than reusing it — if this is
wanted later, it should route through ``chanakya.llm`` instead of configuring a second,
independent LLM path (see docs/CHANAKYA_UNIFIED_CRAWLING_FEATURE_INVENTORY.md).
"""

from __future__ import annotations

import asyncio

from ...config import Crawl4AISettings
from ...security import validate_url
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability, ProviderConfigError
from ..extraction import extract_by_labels


class Crawl4AIProvider(AcquisitionProvider):
    provider_id = "crawl4ai"
    capabilities = frozenset(
        {ProviderCapability.BROWSER_RENDER, ProviderCapability.STRUCTURED_EXTRACTION}
    )  # extraction is now real (label-based Markdown parsing); see extract_by_labels
    requires_credentials = False

    def __init__(self, settings: Crawl4AISettings) -> None:
        super().__init__(settings)

    def is_configured(self) -> bool:
        if not self.settings.enabled:
            return False
        try:
            import crawl4ai  # noqa: F401
        except ImportError:
            return False
        return True

    def validate_config(self) -> list[str]:
        if not self.settings.enabled:
            return ["crawl4ai is disabled (acquisition_providers.crawl4ai.enabled = false)"]
        try:
            import crawl4ai  # noqa: F401
        except ImportError:
            return ["crawl4ai: package is not installed"]
        if self.settings.llm_assisted:
            return ["crawl4ai: llm_assisted extraction is not implemented in this phase"]
        return []

    def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
        if not self.is_configured():
            raise ProviderConfigError("crawl4ai is disabled or not installed")
        from ...config import HttpSettings

        http_settings: HttpSettings = request.extra.get("http_settings") or HttpSettings()
        try:
            validate_url(request.url, http_settings.allowed_domains, resolve_dns=request.extra.get("resolve_dns", True),
                         allow_http=request.extra.get("allow_http", False))
        except Exception as exc:
            return AcquisitionResult(
                provider_id=self.provider_id, url=request.url, status="blocked",
                error=str(exc), retryable=False,
            )
        return asyncio.run(self._fetch_async(request))

    async def _fetch_async(self, request: AcquisitionRequest) -> AcquisitionResult:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

        async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as crawler:
            result = await crawler.arun(url=request.url, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS))

        status_code = result.status_code
        if status_code is not None and status_code in (401, 403):
            return AcquisitionResult(
                provider_id=self.provider_id, url=request.url, status="blocked",
                error=f"HTTP {status_code}", retryable=False,
                diagnostics={"status_code": status_code},
            )
        if not result.success or (status_code is not None and status_code >= 400):
            return AcquisitionResult(
                provider_id=self.provider_id, url=request.url, status="failed",
                error=result.error_message or f"HTTP {status_code}",
                retryable=status_code is None or status_code >= 500,
                diagnostics={"status_code": status_code},
            )

        markdown = result.markdown.raw_markdown if hasattr(result.markdown, "raw_markdown") else str(result.markdown)
        # crawl4ai already tracks the true URL after any redirect; report that instead of the
        # original request URL. job_manager.py's _reclassify_offallowlist_redirects relies on
        # this field being the real final destination to catch a redirect to a host this
        # project never approved — reporting the original URL would make that check blind to
        # every Crawl4AI redirect.
        final_url = result.redirected_url or request.url

        records: list = []
        diagnostics: dict = {"status_code": status_code}
        labels: dict[str, list[str]] = request.extra.get("labels", {})
        if labels and markdown:
            records, review = extract_by_labels(
                markdown, labels, source_id=request.source_id, url=final_url,
                platform=request.extra.get("platform", "crawl4ai"), provider_id=self.provider_id,
            )
            diagnostics["extracted"] = review if review else (records[0].raw if records else {})
            if review:
                diagnostics["needs_review"] = review

        return AcquisitionResult(
            provider_id=self.provider_id, url=final_url, status="ok",
            records=records, raw_content=markdown, content_type="text/markdown",
            diagnostics=diagnostics,
        )
