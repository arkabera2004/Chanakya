"""Desk routes for the Costs and Products pages, and the capability list.

Read-only. The figures come from ``reporting/insights.py``, the same functions
``chanakya costs`` and the workbook's Product-Wise sheet use, so the desk does
not keep a second copy of the sums. Capability names and offerings come from
the knowledge base file named in the config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from . import server
from .app import open_session
from .config import Settings
from .db import ScanRun
from .llm.budget import price_for
from .pipeline.knowledge import Capability, KnowledgeBase, load_knowledge_base
from .reporting.excel import select_opportunities
from .reporting.insights import (
    RELEVANT_BANDS, Usage, daily_usage, daily_window_start, product_groups, relevant_book, usage_by_model_purpose,
    usage_grouped, usage_total, waiting_for_model,
)
from .reporting.text import plain

router = APIRouter()

CHART_DAYS = 30
TOP_DEFAULT = 5
TOP_MAX = 50

_kb_cache: dict[Path, tuple[float, KnowledgeBase]] = {}


def _knowledge_base(settings: Settings) -> KnowledgeBase:
    """The capability file, re-read only when it changes on disk."""
    path = Path(settings.app.capabilities_file)
    stamp = path.stat().st_mtime
    cached = _kb_cache.get(path)
    if cached is None or cached[0] != stamp:
        cached = (stamp, load_knowledge_base(path))
        _kb_cache[path] = cached
    return cached[1]


def _usage_dict(usage: Usage) -> dict[str, Any]:
    # Eight places: one triage call costs a few thousandths of a cent and must not round to zero.
    return {**usage.as_dict(), "cost_usd": round(usage.cost_usd, 8)}


def _window(days: list[tuple[str, Usage]], length: int) -> dict[str, Any]:
    total = Usage()
    for _, usage in days[-length:]:
        total.add(usage)
    return {"days": length, **_usage_dict(total)}


def _capability(cap: Capability) -> dict[str, Any]:
    return {
        "id": cap.id,
        "name": cap.name,
        "offering": cap.offering,
        "offering_status": cap.offering_status,
        "confidence": cap.confidence,
    }


def _costs(settings: Settings) -> dict[str, Any]:
    with open_session(settings) as session:
        days = daily_usage(session, CHART_DAYS)
        lines = usage_by_model_purpose(session, daily_window_start(CHART_DAYS))
        waiting = waiting_for_model(session)
        last = session.scalar(
            select(ScanRun).where(ScanRun.finished_at.is_not(None)).order_by(ScanRun.started_at.desc())
        )
        last_run = server._run_row(last) if last is not None else None
    mode = settings.llm.mode
    by_model = usage_grouped(lines, "model")
    return plain({
        "demo": server.is_demo(settings),
        "llm_mode": mode,
        "window_days": CHART_DAYS,
        "budget": {
            "max_cost_per_day_usd": settings.budget.max_cost_per_day_usd,
            "max_cost_per_run_usd": settings.budget.max_cost_per_run_usd,
            "max_calls_per_run": settings.budget.max_calls_per_run,
            "max_deep_analyses_per_run": settings.budget.max_deep_analyses_per_run,
        },
        "spend": {
            "today": _window(days, 1),
            "last_7_days": _window(days, 7),
            "last_30_days": _window(days, CHART_DAYS),
        },
        "totals": _usage_dict(usage_total(lines)),
        "by_purpose": [
            {"purpose": purpose, **_usage_dict(usage)} for purpose, usage in usage_grouped(lines, "purpose").items()
        ],
        "by_model": [
            {"model": model, "priced": price_for(model, settings.pricing) is not None, **_usage_dict(usage)}
            for model, usage in by_model.items()
        ],
        "waiting": waiting,
        "last_run": last_run,
        "daily": [{"date": day, **_usage_dict(usage)} for day, usage in days],
    })


def _products(settings: Settings, top: int) -> dict[str, Any]:
    kb = _knowledge_base(settings)
    with open_session(settings) as session:
        relevant = relevant_book(select_opportunities(session))
        groups = []
        grouped = 0
        for cap, items in product_groups(relevant, kb):
            grouped += len(items)
            bands = {band: sum(1 for o, _ in items if o.priority == band) for band in RELEVANT_BANDS}
            groups.append({
                **_capability(cap),
                "total": len(items),
                "bands": bands,
                "notices": [{**server._card(o), "capability_score": match.get("score")} for o, match in items[:top]],
                "more": max(0, len(items) - top),
            })
        return plain({
            "kb_version": kb.kb_version,
            "total": len(relevant),
            "bands": {band: sum(1 for o in relevant if o.priority == band) for band in RELEVANT_BANDS},
            # Active P1 to P3 notices whose leading match is not a capability in the book.
            "ungrouped": len(relevant) - grouped,
            "groups": groups,
        })


@router.get("/api/costs")
def costs() -> dict[str, Any]:
    return _costs(server.desk_settings())


@router.get("/api/products")
def products(top: int = TOP_DEFAULT) -> dict[str, Any]:
    return _products(server.desk_settings(), min(max(top, 1), TOP_MAX))


@router.get("/api/capabilities")
def capabilities() -> dict[str, Any]:
    kb = _knowledge_base(server.desk_settings())
    return plain({"kb_version": kb.kb_version, "capabilities": [_capability(cap) for cap in kb.capabilities]})
