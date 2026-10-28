"""Provider registry: maps a provider id to its adapter class and reports status.

Adapter modules are imported lazily (inside ``_ADAPTERS`` lookups), so a provider whose
third-party package is not installed never breaks module import for the rest of the app —
it just reports ``configured=False`` with a clear reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from ..config import AcquisitionProvidersSettings
from .contract import AcquisitionProvider, ProviderCapability

if TYPE_CHECKING:
    pass


@dataclass
class ProviderStatus:
    provider_id: str
    enabled: bool
    configured: bool
    capabilities: list[str]
    requires_credentials: bool
    problems: list[str]


def _load_scrapling():
    from .providers.scrapling_provider import ScraplingProvider

    return ScraplingProvider


def _load_crawlee():
    from .providers.crawlee_provider import CrawleeProvider

    return CrawleeProvider


def _load_crawl4ai():
    from .providers.crawl4ai_provider import Crawl4AIProvider

    return Crawl4AIProvider


def _load_scrapy():
    from .providers.scrapy_provider import ScrapyProvider

    return ScrapyProvider


def _load_playwright():
    from .providers.playwright_provider import PlaywrightProvider

    return PlaywrightProvider


def _load_firecrawl():
    from .providers.firecrawl_provider import FirecrawlProvider

    return FirecrawlProvider


def _load_apify():
    from .providers.apify_provider import ApifyProvider

    return ApifyProvider


def _load_browserless():
    from .providers.browserless_provider import BrowserlessProvider

    return BrowserlessProvider


def _load_zyte():
    from .providers.zyte_provider import ZyteProvider

    return ZyteProvider


_LOADERS: dict[str, Callable[[], type[AcquisitionProvider]]] = {
    "scrapling": _load_scrapling,
    "crawlee": _load_crawlee,
    "crawl4ai": _load_crawl4ai,
    "scrapy": _load_scrapy,
    "playwright": _load_playwright,
    "firecrawl": _load_firecrawl,
    "apify": _load_apify,
    "browserless": _load_browserless,
    "zyte": _load_zyte,
}


class ProviderRegistry:
    """Builds and reports on provider adapters from ``acquisition_providers`` settings."""

    def __init__(self) -> None:
        self._cache: dict[int, dict[str, AcquisitionProvider]] = {}

    def providers(self, settings: AcquisitionProvidersSettings) -> dict[str, AcquisitionProvider]:
        key = id(settings)
        if key not in self._cache:
            built: dict[str, AcquisitionProvider] = {}
            for provider_id, loader in _LOADERS.items():
                provider_settings = getattr(settings, provider_id)
                try:
                    cls = loader()
                except ImportError:
                    built[provider_id] = _UnavailableProvider(provider_id, provider_settings)
                    continue
                built[provider_id] = cls(provider_settings)
            self._cache[key] = built
        return self._cache[key]

    def get(self, settings: AcquisitionProvidersSettings, provider_id: str) -> AcquisitionProvider:
        try:
            return self.providers(settings)[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown acquisition provider '{provider_id}'") from exc

    def status(self, settings: AcquisitionProvidersSettings) -> list[ProviderStatus]:
        out = []
        for provider_id, provider in self.providers(settings).items():
            provider_settings = getattr(settings, provider_id)
            out.append(
                ProviderStatus(
                    provider_id=provider_id,
                    enabled=getattr(provider_settings, "enabled", False),
                    configured=provider.is_configured(),
                    capabilities=sorted(c.value for c in provider.capabilities),
                    requires_credentials=provider.requires_credentials,
                    problems=provider.validate_config(),
                )
            )
        return out


class _UnavailableProvider(AcquisitionProvider):
    """Placeholder used when a provider's third-party package is not installed. Reports
    its real identity and a clear, actionable problem instead of crashing the registry."""

    capabilities: frozenset[ProviderCapability] = frozenset()

    def __init__(self, provider_id: str, settings) -> None:
        super().__init__(settings)
        self.provider_id = provider_id

    def is_configured(self) -> bool:
        return False

    def validate_config(self) -> list[str]:
        return [f"{self.provider_id}: required package is not installed"]

    def fetch(self, request):
        from .contract import AcquisitionResult

        return AcquisitionResult(
            provider_id=self.provider_id,
            url=request.url,
            status="failed",
            error=f"{self.provider_id}: required package is not installed",
            retryable=False,
        )


registry = ProviderRegistry()
