"""The shared acquisition-provider contract.

Every provider adapter (Scrapling, Crawlee, Crawl4AI, Scrapy, Playwright, Firecrawl, Apify,
Browserless, Zyte) implements ``AcquisitionProvider``. The contract deliberately does not
include tender normalisation, deduplication, eligibility or scoring — those stay in
``chanakya.pipeline`` and run once, after a provider hands back an ``AcquisitionResult``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from ..models import TenderRecord
from ..timeutil import now_ist


class ProviderCapability(str, Enum):
    HTTP_FETCH = "http_fetch"
    BROWSER_RENDER = "browser_render"
    STRUCTURED_EXTRACTION = "structured_extraction"
    LLM_EXTRACTION = "llm_extraction"
    LINK_DISCOVERY = "link_discovery"
    DOCUMENT_DOWNLOAD = "document_download"
    MANAGED_CRAWL = "managed_crawl"
    ACTOR_EXECUTION = "actor_execution"


class AcquisitionError(Exception):
    """Base class for acquisition failures. ``retryable`` tells the job manager whether a
    retry is worth attempting without the caller having to inspect the error's type."""

    retryable: bool = False

    def __init__(self, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable


class ProviderConfigError(AcquisitionError):
    """Missing/invalid credentials or configuration. Never retryable — retrying without
    fixing the configuration would just fail again."""

    retryable = False


class ProviderBlockedError(AcquisitionError):
    """CAPTCHA, auth wall, robots.txt, or an explicit access restriction. Never retryable —
    this is a policy boundary, not a transient fault."""

    retryable = False


class ProviderRateLimitedError(AcquisitionError):
    """429 or an equivalent provider-side throttle. Retryable with backoff."""

    retryable = True


class ProviderUnavailableError(AcquisitionError):
    """Timeout, connection failure, or a provider-side 5xx. Retryable with backoff."""

    retryable = True


@dataclass
class AcquisitionRequest:
    """What to fetch/extract, and with which capability."""

    url: str
    source_id: str
    capability: ProviderCapability = ProviderCapability.HTTP_FETCH
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AcquisitionResult:
    """What a provider handed back. ``status`` distinguishes a clean extraction from a
    policy-correct block from a genuine failure — these must never be conflated into one
    boolean, per the project's failure-classification rule."""

    provider_id: str
    url: str
    status: Literal["ok", "blocked", "failed"]
    records: list[TenderRecord] = field(default_factory=list)
    raw_content: str | None = None
    content_type: str | None = None
    fetched_at: datetime = field(default_factory=now_ist)
    error: str | None = None
    retryable: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)


class AcquisitionProvider(ABC):
    """Standard interface every provider adapter implements."""

    provider_id: str
    capabilities: frozenset[ProviderCapability] = frozenset()
    requires_credentials: bool = False

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    def is_configured(self) -> bool:
        """True when this provider has everything it needs to attempt a fetch (enabled,
        local library importable, and/or a credential resolved). Does not make a network call."""

    def validate_config(self) -> list[str]:
        """Human-readable configuration problems, empty when none. Default: just report
        whether ``is_configured()`` passed; adapters override for more specific messages."""
        return [] if self.is_configured() else [f"{self.provider_id} is not configured"]

    @abstractmethod
    def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
        """Run one acquisition. Must not raise for an expected outcome (blocked/failed) —
        those are represented in the returned ``AcquisitionResult.status``. May raise
        ``AcquisitionError`` for a configuration problem caught too late to validate upfront."""

    def health_check(self) -> dict[str, Any]:
        """Cheap, no-network-by-default status snapshot for the provider dashboard."""
        return {"provider_id": self.provider_id, "configured": self.is_configured()}
