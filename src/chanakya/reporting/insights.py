"""Numbers shared by the CLI, the workbook and the desk.

Model usage and the Product-Wise grouping are each worked out here, once, so
``chanakya costs``, the Excel export and the desk pages cannot disagree.
Nothing in this module formats text for display or talks to FastAPI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db import AIUsageEvent, Opportunity
from ..models import AnalysisState, TenderStatus
from ..pipeline.knowledge import Capability, KnowledgeBase
from ..timeutil import ist_date_str, start_of_ist_day_utc

BLOCKED = "budget_blocked"
RELEVANT_BANDS = ("P1", "P2", "P3")


# ---------------------------------------------------------------- model usage
@dataclass
class Usage:
    """One bucket of ``ai_usage_events`` rows.

    ``calls`` are requests that reached a model. A cache hit and a call the
    budget guard refused are counted on their own, never as calls.
    """

    calls: int = 0
    cache_hits: int = 0
    blocked: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> "Usage":
        self.calls += other.calls
        self.cache_hits += other.cache_hits
        self.blocked += other.blocked
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.cost_usd += other.cost_usd
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _usage_columns() -> list[Any]:
    hit = AIUsageEvent.cache_hit.is_(True)
    blocked = AIUsageEvent.result == BLOCKED
    return [
        func.sum(case((hit, 0), (blocked, 0), else_=1)),
        func.sum(case((hit, 1), else_=0)),
        func.sum(case((blocked, 1), else_=0)),
        func.coalesce(func.sum(AIUsageEvent.input_tokens), 0),
        func.coalesce(func.sum(AIUsageEvent.output_tokens), 0),
        func.coalesce(func.sum(AIUsageEvent.cached_tokens), 0),
        func.coalesce(func.sum(AIUsageEvent.est_cost_usd), 0.0),
    ]


def _usage(values: Iterable[Any]) -> Usage:
    calls, hits, blocked, tin, tout, cached, cost = values
    return Usage(int(calls or 0), int(hits or 0), int(blocked or 0), int(tin or 0), int(tout or 0),
                 int(cached or 0), float(cost or 0.0))


def usage_by_model_purpose(session: Session, since: datetime) -> list[tuple[str, str, Usage]]:
    """Usage since ``since`` (naive UTC), one entry per model and purpose."""
    rows = session.execute(
        select(AIUsageEvent.model, AIUsageEvent.purpose, *_usage_columns())
        .where(AIUsageEvent.created_at >= since)
        .group_by(AIUsageEvent.model, AIUsageEvent.purpose)
        .order_by(AIUsageEvent.model, AIUsageEvent.purpose)
    ).all()
    return [(model, purpose, _usage(rest)) for model, purpose, *rest in rows]


def usage_total(lines: Iterable[tuple[str, str, Usage]]) -> Usage:
    total = Usage()
    for _, _, usage in lines:
        total.add(usage)
    return total


def usage_grouped(lines: Iterable[tuple[str, str, Usage]], by: str) -> dict[str, Usage]:
    """Fold the model and purpose lines by ``"model"`` or ``"purpose"``."""
    index = 0 if by == "model" else 1
    out: dict[str, Usage] = {}
    for line in lines:
        out.setdefault(line[index], Usage()).add(line[2])
    return out


def daily_window_start(days: int, now: datetime | None = None) -> datetime:
    """Start (naive UTC) of a window of ``days`` IST days that ends with today."""
    return start_of_ist_day_utc(now) - timedelta(days=days - 1)


def daily_usage(session: Session, days: int = 30, now: datetime | None = None) -> list[tuple[str, Usage]]:
    """One bucket per IST day, oldest first, today last. Days with no usage are kept as zeros.

    The budget guard counts a day from 00:00 IST, so the last bucket is the
    figure it compares with ``max_cost_per_day_usd``.
    """
    start = daily_window_start(days, now)
    buckets: dict[str, Usage] = {ist_date_str(start + timedelta(days=offset)): Usage() for offset in range(days)}
    rows = session.execute(
        select(AIUsageEvent.created_at, AIUsageEvent.cache_hit, AIUsageEvent.result, AIUsageEvent.input_tokens,
               AIUsageEvent.output_tokens, AIUsageEvent.cached_tokens, AIUsageEvent.est_cost_usd)
        .where(AIUsageEvent.created_at >= start)
    )
    for created_at, cache_hit, result, tin, tout, cached, cost in rows:
        bucket = buckets.get(ist_date_str(created_at))
        if bucket is None:  # stamped after ``now``: not part of this window
            continue
        if cache_hit:
            bucket.cache_hits += 1
        elif result == BLOCKED:
            bucket.blocked += 1
        else:
            bucket.calls += 1
        bucket.input_tokens += tin or 0
        bucket.output_tokens += tout or 0
        bucket.cached_tokens += cached or 0
        bucket.cost_usd += cost or 0.0
    return list(buckets.items())


def waiting_for_model(session: Session) -> dict[str, int]:
    """Active notices whose model call has not happened yet, by the reason it is waiting."""
    def count(state: AnalysisState) -> int:
        return int(session.scalar(
            select(func.count()).select_from(Opportunity)
            .where(Opportunity.analysis_state == state.value, Opportunity.status == TenderStatus.ACTIVE.value)
        ) or 0)

    return {"budget": count(AnalysisState.SKIPPED_BUDGET), "documents": count(AnalysisState.DEFERRED)}


# ---------------------------------------------------------------- product-wise
def relevant_book(opps: Iterable[Opportunity]) -> list[Opportunity]:
    """The active P1 to P3 notices, in the order given."""
    return [o for o in opps if o.status == TenderStatus.ACTIVE.value and o.priority in RELEVANT_BANDS]


def product_groups(relevant: Iterable[Opportunity],
                   kb: KnowledgeBase) -> list[tuple[Capability, list[tuple[Opportunity, dict[str, Any]]]]]:
    """Group notices under the capability that leads each one's match, in knowledge-base order.

    Every capability is returned, including those with no notice. Inside a
    group the strongest relevance score comes first. Each item carries the
    notice and its stored match entry (``id``, ``score``, ``offering``).
    """
    by_cap: dict[str, list[tuple[Opportunity, dict[str, Any]]]] = {cap.id: [] for cap in kb.capabilities}
    for o in relevant:
        lead = (o.top_capabilities or [])[:1]
        if lead and lead[0].get("id") in by_cap:
            by_cap[lead[0]["id"]].append((o, lead[0]))
    return [(cap, sorted(by_cap[cap.id], key=lambda item: -(item[0].relevance_score or 0))) for cap in kb.capabilities]
