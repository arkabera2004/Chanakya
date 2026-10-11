"""Connector registry."""

from __future__ import annotations

from ..config import SourceConfig
from .base import DiscoveryContext, StructureChangedError, TenderSourceConnector
from .cppp import CPPPConnector
from .gepnic import GePNICConnector
from .manual import FixtureConnector, ManualImportConnector

REGISTRY: dict[str, type[TenderSourceConnector]] = {
    "cppp": CPPPConnector,
    "gepnic": GePNICConnector,
    "manual": ManualImportConnector,
    "fixture": FixtureConnector,
}

NETWORK_TYPES = {"cppp", "gepnic"}


def build_connector(config: SourceConfig, client=None) -> TenderSourceConnector:
    try:
        cls = REGISTRY[config.type]
    except KeyError as exc:
        raise ValueError(f"unknown source type {config.type!r}") from exc
    return cls(config, client)


__all__ = [
    "REGISTRY", "NETWORK_TYPES", "build_connector", "DiscoveryContext", "StructureChangedError",
    "TenderSourceConnector", "CPPPConnector", "GePNICConnector", "ManualImportConnector", "FixtureConnector",
]
