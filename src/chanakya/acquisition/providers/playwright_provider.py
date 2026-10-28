"""Playwright adapter: local, headless browser retrieval for sources that demonstrably
need JS rendering.

As of docs/CRAWLING_BENCHMARK_SPECIFICATION.md, no current source (CPPP, GePNIC) needs this
— both are server-rendered HTML. This adapter exists so the capability is available behind
the shared contract without being wired into any source by default
(``acquisition_providers.playwright.enabled`` defaults to false).

Security: launches only vanilla Chromium/Firefox/WebKit via Playwright's own sync API — no
stealth plugin, no fingerprint spoofing, no CAPTCHA automation. Navigation is bounded by
``navigation_timeout_s``; browser/page/context are always closed, even on failure.
"""

from __future__ import annotations

from ...config import PlaywrightSettings
from ...security import validate_url
from ..contract import (
    AcquisitionProvider,
    AcquisitionRequest,
    AcquisitionResult,
    ProviderCapability,
    ProviderConfigError,
)


class PlaywrightProvider(AcquisitionProvider):
    provider_id = "playwright"
    capabilities = frozenset({ProviderCapability.BROWSER_RENDER, ProviderCapability.HTTP_FETCH})
    requires_credentials = False

    def __init__(self, settings: PlaywrightSettings) -> None:
        super().__init__(settings)

    def is_configured(self) -> bool:
        if not self.settings.enabled:
            return False
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        return True

    def validate_config(self) -> list[str]:
        if not self.settings.enabled:
            return ["playwright is disabled (acquisition_providers.playwright.enabled = false)"]
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return ["playwright: package is not installed"]
        if self.settings.browser not in ("chromium", "firefox", "webkit"):
            return [f"playwright: unsupported browser '{self.settings.browser}'"]
        return []

    def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
        if not self.is_configured():
            raise ProviderConfigError("playwright is disabled or not installed")

        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        # Reuse the project's own SSRF/allowlist validation before ever handing a URL to a
        # browser — a browser-rendered fetch is not exempt from the same network policy as
        # the plain HTTP client.
        from ...config import HttpSettings

        http_settings: HttpSettings = request.extra.get("http_settings") or HttpSettings()
        resolve_dns: bool = request.extra.get("resolve_dns", True)
        allow_http: bool = request.extra.get("allow_http", False)
        try:
            validate_url(request.url, http_settings.allowed_domains, resolve_dns=resolve_dns, allow_http=allow_http)
        except Exception as exc:  # security.SecurityError, kept generic to avoid a hard import cycle
            return AcquisitionResult(
                provider_id=self.provider_id, url=request.url, status="blocked",
                error=str(exc), retryable=False,
            )

        diagnostics: dict = {"browser": self.settings.browser, "headless": self.settings.headless}
        try:
            with sync_playwright() as pw:
                browser_type = getattr(pw, self.settings.browser)
                browser = browser_type.launch(headless=self.settings.headless)
                try:
                    context = browser.new_context()
                    try:
                        page = context.new_page()
                        console_errors: list[str] = []
                        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                        page.on("pageerror", lambda exc: console_errors.append(str(exc)))
                        response = page.goto(
                            request.url,
                            timeout=self.settings.navigation_timeout_s * 1000,
                            wait_until=request.extra.get("wait_until", "load"),
                        )
                        html = page.content()
                        status_code = response.status if response else None
                        diagnostics["console_errors"] = console_errors
                        diagnostics["http_status"] = status_code
                        if status_code is not None and status_code in (401, 403):
                            return AcquisitionResult(
                                provider_id=self.provider_id, url=page.url, status="blocked",
                                error=f"HTTP {status_code}", retryable=False, diagnostics=diagnostics,
                            )
                        if status_code is not None and status_code >= 400:
                            return AcquisitionResult(
                                provider_id=self.provider_id, url=page.url, status="failed",
                                error=f"HTTP {status_code}", retryable=status_code >= 500, diagnostics=diagnostics,
                            )
                        return AcquisitionResult(
                            provider_id=self.provider_id, url=page.url, status="ok",
                            raw_content=html, content_type="text/html", diagnostics=diagnostics,
                        )
                    finally:
                        context.close()
                finally:
                    browser.close()
        except PlaywrightTimeoutError as exc:
            return AcquisitionResult(
                provider_id=self.provider_id, url=request.url, status="failed",
                error=f"navigation timeout: {exc}", retryable=True, diagnostics=diagnostics,
            )
        except PlaywrightError as exc:
            return AcquisitionResult(
                provider_id=self.provider_id, url=request.url, status="failed",
                error=str(exc), retryable=True, diagnostics=diagnostics,
            )
