"""Scrapling adapter: structured extraction over HTML retrieved by Chanakya's own
``PoliteClient``.

shortcut: this adapter never imports ``scrapling.Fetcher``/``StealthyFetcher``. Those pull
in ``curl_cffi`` and, transitively, ``patchright`` — a stealth fork of Playwright built to
evade bot detection, which conflicts with this project's explicit no-stealth-automation rule
(see docs/CHANAKYA_UNIFIED_CRAWLING_FEATURE_INVENTORY.md). Retrieval stays on the already
SSRF-protected, robots.txt-respecting ``PoliteClient``. Upgrade path: if a future source
genuinely requires Scrapling's own fetch layer, re-evaluate after confirming ``curl_cffi``
alone (without enabling any stealth option) is acceptable — do not enable ``StealthyFetcher``
under any circumstance.

Field extraction itself now uses the shared ``acquisition.extraction.extract_by_selectors``
helper (BeautifulSoup-based) instead of ``scrapling.Selector`` directly — a plain CSS-selector
field map exercises none of Scrapling's distinguishing adaptive-selector capability either
way, so sharing one implementation with every other HTML-returning provider (Crawlee, Scrapy,
Playwright, Browserless, Zyte) was a strictly better trade than keeping six near-duplicate
copies. ``scrapling`` itself is still a required, checked dependency — ``is_configured()``
gates this provider on it being installed, consistent with every other provider's package
check — it is simply no longer imported inside ``fetch()``.
"""

from __future__ import annotations

from ...config import ScraplingSettings
from ...http import BlockedError, FetchError, PoliteClient
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability
from ..extraction import extract_by_selectors


class ScraplingProvider(AcquisitionProvider):
    provider_id = "scrapling"
    capabilities = frozenset({ProviderCapability.STRUCTURED_EXTRACTION, ProviderCapability.LINK_DISCOVERY})
    requires_credentials = False

    def __init__(self, settings: ScraplingSettings) -> None:
        super().__init__(settings)

    def is_configured(self) -> bool:
        if not self.settings.enabled:
            return False
        try:
            import scrapling  # noqa: F401
        except ImportError:
            return False
        return True

    def validate_config(self) -> list[str]:
        if not self.settings.enabled:
            return ["scrapling is disabled (acquisition_providers.scrapling.enabled = false)"]
        try:
            import scrapling  # noqa: F401
        except ImportError:
            return ["scrapling: package is not installed"]
        return []

    def fetch(self, request: AcquisitionRequest, *, client: PoliteClient | None = None) -> AcquisitionResult:
        owns_client = client is None
        if client is None:
            from ...config import HttpSettings

            client = PoliteClient(HttpSettings())
        try:
            try:
                final_url, html = client.get_text(request.url)
            except BlockedError as exc:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="blocked",
                    error=str(exc), retryable=False,
                )
            except FetchError as exc:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=str(exc), retryable=True,
                )

            selectors: dict[str, str] = request.extra.get("selectors", {})
            if not selectors:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=final_url, status="ok",
                    raw_content=html, content_type="text/html",
                    diagnostics={"note": "no selector map given; returning raw content only"},
                )

            records, review = extract_by_selectors(
                html, selectors, source_id=request.source_id, url=request.url,
                platform=request.extra.get("platform", "scrapling"), provider_id=self.provider_id,
            )
            extracted = review if review else (records[0].raw if records else {})

            return AcquisitionResult(
                provider_id=self.provider_id, url=final_url, status="ok",
                records=records, raw_content=html, content_type="text/html",
                diagnostics={"extracted": extracted, "needs_review": review} if review else {"extracted": extracted},
            )
        finally:
            if owns_client:
                client.close()
