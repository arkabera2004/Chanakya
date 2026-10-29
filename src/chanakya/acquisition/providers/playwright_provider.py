"""Playwright adapter: local, headless browser retrieval for sources that demonstrably
need JS rendering.

As of docs/CRAWLING_BENCHMARK_SPECIFICATION.md, no current source (CPPP, GePNIC) needs this
— both are server-rendered HTML. This adapter exists so the capability is available behind
the shared contract without being wired into any source by default
(``acquisition_providers.playwright.enabled`` defaults to false).

Security: launches only vanilla Chromium/Firefox/WebKit via Playwright's own sync API — no
stealth plugin, no fingerprint spoofing, no CAPTCHA automation. Navigation is bounded by
``navigation_timeout_s``; browser/page/context are always closed, even on failure.

Redirect handling: every hop of a redirect chain is validated against the same host
allowlist as the initial request (via ``page.on("request")``, since ``context.route()``
does not intercept a navigation's own redirect hops in the Playwright/Chromium version this
was verified against). A redirect to an off-allowlist host causes the whole fetch to be
reported ``blocked`` — its content is never trusted or stored. This is a final-content
guard, not a network-level one: the browser's outbound connection to the off-allowlist host
during the redirect cannot be prevented by this mechanism, only its result discarded. Only
``chanakya.http.PoliteClient`` (used by the scrapling adapter and the CPPP/GePNIC
connectors) validates a redirect target before the connection is made.
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
from ..extraction import extract_by_selectors


class PlaywrightProvider(AcquisitionProvider):
    provider_id = "playwright"
    capabilities = frozenset({
        ProviderCapability.BROWSER_RENDER, ProviderCapability.HTTP_FETCH, ProviderCapability.STRUCTURED_EXTRACTION,
    })
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
                        # page.goto() follows redirects inside the browser engine itself, so the
                        # single validate_url() call above (on request.url only) never sees an
                        # intermediate hop. ``context.route()`` was tried first and does NOT
                        # intercept a navigation's own redirect hops in this Playwright/Chromium
                        # version (verified directly: a route handler registered on "**/*" only
                        # ever fires for the initial request, never the redirected one) — so it
                        # cannot abort the request before it reaches an off-allowlist host.
                        # ``page.on("request")`` does reliably observe every hop by the time
                        # page.goto() returns, so every hop is validated and, if any one of them
                        # is off-allowlist, the navigation's result is discarded as "blocked"
                        # rather than trusted. This does NOT prevent the browser's outbound
                        # request to that host (a real, undocumented residual exposure — see the
                        # module docstring), only prevents Chanakya from trusting or storing what
                        # it returned.
                        off_allowlist_hops: list[str] = []

                        def _record_hop(req) -> None:  # noqa: ANN001 - playwright.sync_api.Request
                            try:
                                validate_url(req.url, http_settings.allowed_domains, resolve_dns=resolve_dns, allow_http=allow_http)
                            except Exception:  # security.SecurityError, kept generic (see import-cycle note above)
                                off_allowlist_hops.append(req.url)

                        page = context.new_page()
                        page.on("request", _record_hop)
                        console_errors: list[str] = []
                        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                        page.on("pageerror", lambda exc: console_errors.append(str(exc)))
                        response = page.goto(
                            request.url,
                            timeout=self.settings.navigation_timeout_s * 1000,
                            wait_until=request.extra.get("wait_until", "load"),
                        )
                        if off_allowlist_hops:
                            return AcquisitionResult(
                                provider_id=self.provider_id, url=off_allowlist_hops[-1], status="blocked",
                                error=f"a redirect hop left the host allowlist: {off_allowlist_hops[-1]}",
                                retryable=False, diagnostics=diagnostics,
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
                        records: list = []
                        selectors: dict[str, str] = request.extra.get("selectors", {})
                        if selectors:
                            records, review = extract_by_selectors(
                                html, selectors, source_id=request.source_id, url=page.url,
                                platform=request.extra.get("platform", "playwright"), provider_id=self.provider_id,
                            )
                            diagnostics["extracted"] = review if review else (records[0].raw if records else {})
                            if review:
                                diagnostics["needs_review"] = review
                        return AcquisitionResult(
                            provider_id=self.provider_id, url=page.url, status="ok",
                            records=records, raw_content=html, content_type="text/html", diagnostics=diagnostics,
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
