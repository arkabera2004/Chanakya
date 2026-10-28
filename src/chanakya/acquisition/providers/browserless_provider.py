"""Browserless adapter: remote browser-rendered retrieval via the documented REST
``/content`` endpoint.

Live-verified during this phase: ``POST https://chrome.browserless.io/content?token=...``
with ``{"url": "https://example.com"}`` returned HTTP 200 with the rendered HTML and
diagnostic headers (``X-Response-Status``, ``X-Response-Code``, ``X-Response-URL``) — not
guessed. An earlier attempt to connect over Playwright's CDP ``connect()`` to
``wss://chrome.browserless.io`` timed out (that classic WebSocket endpoint appears to have
moved to a regional ``production-<region>.browserless.io`` host); the REST API does not have
that problem and is what this adapter uses.
"""

from __future__ import annotations

import httpx

from ...config import BrowserlessSettings, Settings
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability, ProviderConfigError


class BrowserlessProvider(AcquisitionProvider):
    provider_id = "browserless"
    capabilities = frozenset({ProviderCapability.BROWSER_RENDER})
    requires_credentials = True

    def __init__(self, settings: BrowserlessSettings, *, secret_resolver=None) -> None:
        super().__init__(settings)
        self._secret_resolver = secret_resolver or Settings().secret

    def _token(self) -> str | None:
        return self._secret_resolver(self.settings.api_key_env)

    def is_configured(self) -> bool:
        return self.settings.enabled and bool(self._token())

    def validate_config(self) -> list[str]:
        problems = []
        if not self.settings.enabled:
            problems.append("browserless is disabled (acquisition_providers.browserless.enabled = false)")
        if not self._token():
            problems.append(f"browserless: {self.settings.api_key_env} is not set")
        return problems

    def health_check(self) -> dict:
        base = super().health_check()
        base["base_url"] = self.settings.base_url
        return base

    def fetch(self, request: AcquisitionRequest, *, client: httpx.Client | None = None) -> AcquisitionResult:
        token = self._token()
        if not self.settings.enabled or not token:
            raise ProviderConfigError("browserless is disabled or missing an API key")

        owns_client = client is None
        client = client or httpx.Client(base_url=self.settings.base_url, timeout=self.settings.timeout_s)
        try:
            try:
                resp = client.post(
                    "/content", params={"token": token}, headers={"Content-Type": "application/json"},
                    json={"url": request.url, **request.extra.get("options", {})},
                )
            except httpx.TimeoutException as exc:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"timeout: {exc}", retryable=True,
                )

            if resp.status_code in (401, 403):
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="blocked",
                    error=f"browserless auth rejected: HTTP {resp.status_code}", retryable=False,
                )
            if resp.status_code == 429:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error="browserless rate limit exceeded", retryable=True,
                )
            if resp.status_code >= 400:
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"HTTP {resp.status_code}", retryable=resp.status_code >= 500,
                )

            headers = resp.headers
            return AcquisitionResult(
                provider_id=self.provider_id, url=headers.get("x-response-url", request.url), status="ok",
                raw_content=resp.text, content_type="text/html",
                diagnostics={
                    "upstream_status": headers.get("x-response-status"),
                    "upstream_http_code": headers.get("x-response-code"),
                    "deployment": headers.get("x-browserless-deployment"),
                },
            )
        finally:
            if owns_client:
                client.close()
