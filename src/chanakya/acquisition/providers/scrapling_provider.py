"""Scrapling adapter: structured extraction via ``scrapling.Selector`` over HTML retrieved
by Chanakya's own ``PoliteClient``.

shortcut: this adapter never imports ``scrapling.Fetcher``/``StealthyFetcher``. Those pull
in ``curl_cffi`` and, transitively, ``patchright`` — a stealth fork of Playwright built to
evade bot detection, which conflicts with this project's explicit no-stealth-automation rule
(see docs/CHANAKYA_UNIFIED_CRAWLING_FEATURE_INVENTORY.md). ``scrapling.Selector`` is a pure
parsing/selection class with no such dependency, so retrieval stays on the already
SSRF-protected, robots.txt-respecting ``PoliteClient`` and only selection/extraction comes
from Scrapling. Upgrade path: if a future source genuinely requires Scrapling's own fetch
layer, re-evaluate after confirming ``curl_cffi`` alone (without enabling any stealth option)
is acceptable — do not enable ``StealthyFetcher`` under any circumstance.
"""

from __future__ import annotations

from ...config import ScraplingSettings
from ...http import BlockedError, FetchError, PoliteClient
from ...models import TenderRecord
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability


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
        from scrapling import Selector

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

            sel = Selector(html)
            extracted: dict[str, str | None] = {}
            for field_name, css in selectors.items():
                matches = sel.css(css)
                extracted[field_name] = matches[0].text.strip() if matches else None

            records: list[TenderRecord] = []
            review: dict[str, str | None] = {}
            if extracted.get("title"):
                records.append(
                    TenderRecord(
                        source_id=request.source_id,
                        platform=request.extra.get("platform", "scrapling"),
                        source_record_key=extracted.get("tender_id") or request.url,
                        source_tender_id=extracted.get("tender_id"),
                        ref_no=extracted.get("ref_no"),
                        title=extracted["title"],
                        organisation=extracted.get("organisation"),
                        detail_url=request.url,
                        evidence=f"Scrapling selector extraction of {request.url}",
                        raw=extracted,
                    )
                )
            else:
                # No title extracted: do not guess. Keep the evidence for manual review
                # instead of silently discarding or inventing a record (per the adaptive-
                # selector validation rule in the unified-crawling brief).
                review = extracted

            return AcquisitionResult(
                provider_id=self.provider_id, url=final_url, status="ok",
                records=records, raw_content=html, content_type="text/html",
                diagnostics={"extracted": extracted, "needs_review": review} if review else {"extracted": extracted},
            )
        finally:
            if owns_client:
                client.close()
