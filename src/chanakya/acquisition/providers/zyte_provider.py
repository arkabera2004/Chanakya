"""Zyte API adapter: HTTP/browser-rendered retrieval and extraction via the documented
``POST /v1/extract`` endpoint (HTTP Basic auth, API key as username, empty password).

No ZYTE_API_KEY was provided during this phase — this adapter is implemented against
Zyte's publicly documented request/response schema (``{"url": ..., "httpResponseBody": true}``
-> ``{"url": ..., "statusCode": int, "httpResponseBody": <base64>}``, or
``{"browserHtml": true}`` -> ``{"browserHtml": str}``) but has **not** been live-verified.
It is TESTED only against mocked responses and stays BLOCKED_BY_CREDENTIALS until a key is
provided (see docs/CHANAKYA_UNIFIED_CRAWLING_FEATURE_INVENTORY.md).
"""

from __future__ import annotations

import base64

import httpx

from ...config import Settings, ZyteSettings
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability, ProviderConfigError


class ZyteProvider(AcquisitionProvider):
    provider_id = "zyte"
    capabilities = frozenset(
        {ProviderCapability.HTTP_FETCH, ProviderCapability.BROWSER_RENDER, ProviderCapability.STRUCTURED_EXTRACTION}
    )
    requires_credentials = True

    def __init__(self, settings: ZyteSettings, *, secret_resolver=None) -> None:
        super().__init__(settings)
        self._secret_resolver = secret_resolver or Settings().secret

    def _api_key(self) -> str | None:
        return self._secret_resolver(self.settings.api_key_env)

    def is_configured(self) -> bool:
        return self.settings.enabled and bool(self._api_key())

    def validate_config(self) -> list[str]:
        problems = []
        if not self.settings.enabled:
            problems.append("zyte is disabled (acquisition_providers.zyte.enabled = false)")
        if not self._api_key():
            problems.append(f"zyte: {self.settings.api_key_env} is not set (BLOCKED_BY_CREDENTIALS)")
        return problems

    def fetch(self, request: AcquisitionRequest, *, client: httpx.Client | None = None) -> AcquisitionResult:
        api_key = self._api_key()
        if not self.settings.enabled or not api_key:
            raise ProviderConfigError("zyte is disabled or missing an API key")

        browser_render = bool(request.extra.get("browser_render", False))
        owns_client = client is None
        client = client or httpx.Client(base_url=self.settings.base_url, timeout=self.settings.timeout_s)
        try:
            body = {"url": request.url}
            body["browserHtml" if browser_render else "httpResponseBody"] = True

            try:
                resp = client.post("/extract", auth=(api_key, ""), json=body)
            except httpx.TimeoutException as exc:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"timeout: {exc}", retryable=True,
                )

            if resp.status_code in (401, 403):
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="blocked",
                    error=f"zyte auth rejected: HTTP {resp.status_code}", retryable=False,
                )
            if resp.status_code == 429:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error="zyte rate limit exceeded", retryable=True,
                )
            if resp.status_code >= 400:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"HTTP {resp.status_code}", retryable=resp.status_code >= 500,
                )

            data = resp.json()
            if browser_render:
                html = data.get("browserHtml")
            else:
                encoded = data.get("httpResponseBody")
                html = base64.b64decode(encoded).decode("utf-8", errors="replace") if encoded else None

            return AcquisitionResult(
                provider_id=self.provider_id, url=data.get("url", request.url), status="ok",
                raw_content=html, content_type="text/html",
                diagnostics={"status_code": data.get("statusCode")},
            )
        finally:
            if owns_client:
                client.close()
