"""Firecrawl adapter: managed single-page scraping via the documented v1 ``/scrape`` endpoint.

Response shape verified against a real call made during this phase (``POST /v1/scrape``,
``{"url": "https://example.com", "formats": ["markdown"]}``) — not guessed:
``{"success": bool, "data": {"markdown": str, "metadata": {"statusCode": int,
"creditsUsed": int, "sourceURL": str, "title": str, ...}}}``. On error, Firecrawl returns
``{"success": false, "error": str}`` with a 4xx/5xx status.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...config import FirecrawlSettings, Settings
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability, ProviderConfigError
from ..extraction import extract_by_labels


class FirecrawlProvider(AcquisitionProvider):
    provider_id = "firecrawl"
    capabilities = frozenset(
        {ProviderCapability.MANAGED_CRAWL, ProviderCapability.STRUCTURED_EXTRACTION, ProviderCapability.HTTP_FETCH}
    )
    requires_credentials = True

    def __init__(self, settings: FirecrawlSettings, *, secret_resolver=None) -> None:
        super().__init__(settings)
        # secret_resolver mirrors Settings.secret() (redacts via obs.register_secret); a
        # standalone Settings() is used as a fallback so the provider still works when
        # constructed outside of the full app settings (e.g. in a unit test).
        self._secret_resolver = secret_resolver or Settings().secret

    def _api_key(self) -> str | None:
        return self._secret_resolver(self.settings.api_key_env)

    def is_configured(self) -> bool:
        return self.settings.enabled and bool(self._api_key())

    def validate_config(self) -> list[str]:
        problems = []
        if not self.settings.enabled:
            problems.append("firecrawl is disabled (acquisition_providers.firecrawl.enabled = false)")
        if not self._api_key():
            problems.append(f"firecrawl: {self.settings.api_key_env} is not set")
        return problems

    def fetch(self, request: AcquisitionRequest, *, client: httpx.Client | None = None) -> AcquisitionResult:
        api_key = self._api_key()
        if not self.settings.enabled or not api_key:
            raise ProviderConfigError("firecrawl is disabled or missing an API key")

        owns_client = client is None
        client = client or httpx.Client(base_url=self.settings.base_url, timeout=self.settings.timeout_s)
        try:
            formats = request.extra.get("formats", ["markdown"])
            retries_left = self.settings.max_retries
            while True:
                try:
                    resp = client.post(
                        "/v1/scrape",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"url": request.url, "formats": formats},
                    )
                except httpx.TimeoutException as exc:
                    return AcquisitionResult(
                        provider_id=self.provider_id, url=request.url, status="failed",
                        error=f"timeout: {exc}", retryable=True,
                    )
                if resp.status_code == 429 and retries_left > 0:
                    retries_left -= 1
                    continue
                break

            body: dict[str, Any] = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}

            if resp.status_code in (401, 403):
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="blocked",
                    error=f"firecrawl auth rejected: HTTP {resp.status_code}", retryable=False,
                )
            if resp.status_code == 429:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error="firecrawl rate limit exceeded", retryable=True,
                )
            if resp.status_code >= 400 or not body.get("success", False):
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=body.get("error") or f"HTTP {resp.status_code}",
                    retryable=resp.status_code >= 500,
                    diagnostics={"status_code": resp.status_code},
                )

            data = body.get("data", {})
            metadata = data.get("metadata", {})
            markdown = data.get("markdown")
            result_url = metadata.get("sourceURL", request.url)

            records: list = []
            diagnostics: dict = {
                "title": metadata.get("title"),
                "http_status": metadata.get("statusCode"),
                "credits_used": metadata.get("creditsUsed"),
                "scrape_id": metadata.get("scrapeId"),
            }
            labels: dict[str, list[str]] = request.extra.get("labels", {})
            if labels and markdown:
                records, review = extract_by_labels(
                    markdown, labels, source_id=request.source_id, url=result_url,
                    platform=request.extra.get("platform", "firecrawl"), provider_id=self.provider_id,
                )
                diagnostics["extracted"] = review if review else (records[0].raw if records else {})
                if review:
                    diagnostics["needs_review"] = review

            return AcquisitionResult(
                provider_id=self.provider_id, url=result_url, status="ok",
                records=records, raw_content=markdown, content_type="text/markdown",
                diagnostics=diagnostics,
            )
        finally:
            if owns_client:
                client.close()
