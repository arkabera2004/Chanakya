"""Crawlee adapter: bounded-concurrency HTTP crawling via ``crawlee.crawlers.HttpCrawler``.

Crawlee is async-first; this adapter runs it through ``asyncio.run()`` from the synchronous
``fetch()`` call so the rest of Chanakya (sync connectors, sync job manager) does not need
to become async. Request-level failures are captured via Crawlee's own
``failed_request_handler`` rather than raised, so one bad URL in a batch does not abort the
whole run. API confirmed by a real local run against https://example.com during development
(``HttpCrawler(max_requests_per_crawl=1)`` returning HTTP 200 and the response body via
``context.http_response.read()``).
"""

from __future__ import annotations

import asyncio

from ...config import CrawleeSettings
from ...security import validate_url
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability
from ..extraction import extract_by_selectors


class CrawleeProvider(AcquisitionProvider):
    provider_id = "crawlee"
    capabilities = frozenset({
        ProviderCapability.HTTP_FETCH, ProviderCapability.LINK_DISCOVERY, ProviderCapability.STRUCTURED_EXTRACTION,
    })
    requires_credentials = False

    def __init__(self, settings: CrawleeSettings) -> None:
        super().__init__(settings)

    def is_configured(self) -> bool:
        if not self.settings.enabled:
            return False
        try:
            import crawlee.crawlers  # noqa: F401
        except ImportError:
            return False
        return True

    def validate_config(self) -> list[str]:
        if not self.settings.enabled:
            return ["crawlee is disabled (acquisition_providers.crawlee.enabled = false)"]
        try:
            import crawlee.crawlers  # noqa: F401
        except ImportError:
            return ["crawlee: package is not installed"]
        return []

    def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
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
        import tempfile

        from crawlee.configuration import Configuration
        from crawlee.crawlers import HttpCrawler, HttpCrawlingContext
        from crawlee.errors import HttpStatusCodeError, SessionError

        outcome: dict = {}

        # Each fetch() call gets its own storage_dir: Crawlee's default file-system request
        # queue binds internal locks to the event loop that created them, so a shared
        # storage dir across repeated asyncio.run() calls raises "bound to a different
        # event loop" on the second call. An isolated temp dir per call sidesteps that
        # without needing to keep one long-lived crawler/event loop alive.
        crawler = HttpCrawler(
            max_requests_per_crawl=1,
            max_request_retries=self.settings.max_retries,
            configuration=Configuration(storage_dir=tempfile.mkdtemp(prefix="chanakya-crawlee-"), purge_on_start=True),
        )

        @crawler.router.default_handler
        async def handler(context: HttpCrawlingContext) -> None:
            body = await context.http_response.read()
            outcome["status"] = "ok"
            outcome["url"] = context.request.url
            outcome["status_code"] = context.http_response.status_code
            outcome["content"] = body.decode("utf-8", errors="replace")

        @crawler.failed_request_handler
        async def on_failed(context, error) -> None:
            # Crawlee treats 401/403 as a "blocked session" (crawlee.errors.SessionError) and
            # retries with session rotation internally before giving up — a library default,
            # not something this adapter configures. Either error shape is classified as a
            # policy block here, consistent with BlockedError elsewhere in the project.
            outcome["status"] = "failed"
            outcome["url"] = context.request.url
            outcome["error"] = str(error)
            outcome["retryable"] = not isinstance(error, HttpStatusCodeError) or error.status_code >= 500
            if isinstance(error, SessionError) or (isinstance(error, HttpStatusCodeError) and error.status_code in (401, 403)):
                outcome["status"] = "blocked"
                outcome["retryable"] = False

        await crawler.run([request.url])

        if not outcome:
            return AcquisitionResult(
                provider_id=self.provider_id, url=request.url, status="failed",
                error="crawlee: no handler outcome recorded", retryable=True,
            )

        records: list = []
        diagnostics: dict = {"status_code": outcome.get("status_code")}
        selectors: dict[str, str] = request.extra.get("selectors", {})
        if outcome["status"] == "ok" and selectors and outcome.get("content"):
            records, review = extract_by_selectors(
                outcome["content"], selectors, source_id=request.source_id, url=outcome.get("url", request.url),
                platform=request.extra.get("platform", "crawlee"), provider_id=self.provider_id,
            )
            diagnostics["extracted"] = review if review else (records[0].raw if records else {})
            if review:
                diagnostics["needs_review"] = review

        return AcquisitionResult(
            provider_id=self.provider_id, url=outcome.get("url", request.url), status=outcome["status"],
            records=records, raw_content=outcome.get("content"),
            content_type="text/html" if outcome.get("content") else None,
            error=outcome.get("error"), retryable=outcome.get("retryable", False),
            diagnostics=diagnostics,
        )
