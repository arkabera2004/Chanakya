"""Unified acquisition providers.

Nine acquisition strategies (local libraries and managed services) sit behind one
``AcquisitionProvider`` contract (see ``contract.py``) so the rest of Chanakya — the job
manager, the API, the frontend — only ever talks to that one interface. Providers never
normalise into ``TenderRecord`` themselves beyond what their own output format requires;
the pipeline's existing dedup/eligibility/scoring stages are not duplicated here.
"""

from .contract import (
    AcquisitionError,
    AcquisitionProvider,
    AcquisitionRequest,
    AcquisitionResult,
    ProviderBlockedError,
    ProviderCapability,
    ProviderConfigError,
    ProviderRateLimitedError,
)
from .registry import ProviderStatus, registry

__all__ = [
    "AcquisitionError",
    "AcquisitionProvider",
    "AcquisitionRequest",
    "AcquisitionResult",
    "ProviderBlockedError",
    "ProviderCapability",
    "ProviderConfigError",
    "ProviderRateLimitedError",
    "ProviderStatus",
    "registry",
]
