"""Win/loss tracking (roadmap Phase 7). Read-only.

BD records the outcome of a pursuit with the review statuses ``Won`` and ``Lost``
(``chanakya review <id> --status Won`` or the desk). This module only *reads*
those decisions:

- ``winloss_summary`` groups pursued, won and lost notices by buyer organisation,
  by best-matched capability and by platform. It returns plain dicts and lists so
  the desk API can serve it later without a second implementation.
- ``buyer_win_count`` is the one number the scorer may use when
  ``scoring.win_history_enabled`` is true (see ``scoring.py`` and doc 10).

Definitions:

- pursued = review status Pursuing, Won or Lost (a won or lost notice was pursued).
- open    = still Pursuing (no outcome recorded yet).
- win rate = won / (won + lost), or None until at least one outcome exists.

Buyers are grouped by their normalised organisation name (lower case, punctuation
removed), so "NTPC Limited" and "NTPC  Limited." are one buyer, but two different
spellings of a ministry are two buyers. Nothing here guesses that two names are
the same organisation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Opportunity
from ..models import ReviewStatus
from .textnorm import normalize

PURSUED_STATUSES = (ReviewStatus.PURSUING.value, ReviewStatus.WON.value, ReviewStatus.LOST.value)
NO_BUYER = "Not stated"
NO_CAPABILITY = "No capability match"


def buyer_key(organisation: str | None) -> str:
    """Stable grouping key for a buyer organisation."""
    return normalize(organisation or "")


def _best_capability(o: Opportunity) -> str:
    top = o.top_capabilities or []
    if top and isinstance(top[0], dict) and top[0].get("id"):
        return str(top[0]["id"])
    return NO_CAPABILITY


def _bucket(groups: dict[str, dict[str, Any]], key: str, label: str, status: str) -> None:
    row = groups.setdefault(key, {"key": label, "pursued": 0, "open": 0, "won": 0, "lost": 0})
    row["pursued"] += 1
    if status == ReviewStatus.WON.value:
        row["won"] += 1
    elif status == ReviewStatus.LOST.value:
        row["lost"] += 1
    else:
        row["open"] += 1


def _finish(groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in groups.values():
        decided = row["won"] + row["lost"]
        row["win_rate"] = round(row["won"] / decided, 3) if decided else None
        rows.append(row)
    return sorted(rows, key=lambda r: (-r["pursued"], -r["won"], r["key"].lower()))


def winloss_summary(session: Session) -> dict[str, Any]:
    """Pursued/won/lost by buyer, capability and platform. Reads only; never writes."""
    rows = session.scalars(
        select(Opportunity).where(Opportunity.review_status.in_(PURSUED_STATUSES)).order_by(Opportunity.id)
    ).all()
    buyers: dict[str, dict[str, Any]] = {}
    caps: dict[str, dict[str, Any]] = {}
    platforms: dict[str, dict[str, Any]] = {}
    total: dict[str, dict[str, Any]] = {}
    for o in rows:
        org = (o.organisation or "").strip()
        _bucket(buyers, buyer_key(org) or NO_BUYER, org or NO_BUYER, o.review_status)
        cap = _best_capability(o)
        _bucket(caps, cap, cap, o.review_status)
        platform = (o.platform or "").strip() or "Not stated"
        _bucket(platforms, platform, platform, o.review_status)
        _bucket(total, "all", "All", o.review_status)
    totals = _finish(total)[0] if total else {"key": "All", "pursued": 0, "open": 0, "won": 0, "lost": 0,
                                              "win_rate": None}
    return {"totals": totals, "by_buyer": _finish(buyers), "by_capability": _finish(caps),
            "by_platform": _finish(platforms)}


def buyer_win_count(session: Session, organisation: str | None, exclude_id: str | None = None) -> int:
    """How many *other* notices from this buyer are recorded as Won.

    The notice being scored is excluded, so recording a win on a notice never
    changes that notice's own score. A buyer with no stated name has no history.
    """
    key = buyer_key(organisation)
    if not key:
        return 0
    won = session.execute(
        select(Opportunity.id, Opportunity.organisation).where(Opportunity.review_status == ReviewStatus.WON.value)
    ).all()
    return sum(1 for oid, org in won if oid != exclude_id and buyer_key(org) == key)


def _table(title: str, heading: str, rows: list[dict[str, Any]], limit: int | None) -> list[str]:
    lines = [f"## {title}", "", f"| {heading} | Pursued | Open | Won | Lost | Win rate |", "|---|---|---|---|---|---|"]
    shown = rows if limit is None else rows[:limit]
    for r in shown:
        rate = "n/a" if r["win_rate"] is None else f"{r['win_rate']:.0%}"
        lines.append(f"| {r['key']} | {r['pursued']} | {r['open']} | {r['won']} | {r['lost']} | {rate} |")
    if not rows:
        lines.append("| (none) | 0 | 0 | 0 | 0 | n/a |")
    if limit is not None and len(rows) > limit:
        lines.append(f"\n{len(rows) - limit} more not shown.")
    return lines + [""]


def format_winloss(summary: dict[str, Any], limit: int | None = 20) -> str:
    """Markdown summary for the console or a file."""
    t = summary["totals"]
    rate = "n/a" if t["win_rate"] is None else f"{t['win_rate']:.0%}"
    lines = ["# Chanakya win/loss summary", "",
             f"Pursued {t['pursued']} (open {t['open']}, won {t['won']}, lost {t['lost']}). "
             f"Win rate on decided pursuits: {rate}.", ""]
    if not t["pursued"]:
        lines += ["No notice has the review status Pursuing, Won or Lost yet. Record outcomes with "
                  "`chanakya review <id> --status Won` (or Lost, Pursuing).", ""]
    lines += _table("By buyer organisation", "Buyer", summary["by_buyer"], limit)
    lines += _table("By capability (best match)", "Capability", summary["by_capability"], limit)
    lines += _table("By platform", "Platform", summary["by_platform"], limit)
    lines += ["Limits: counts come only from review statuses that BD recorded. A win rate on a handful of "
              "pursuits is an anecdote, not a rate. Buyers are grouped by exact normalised name, so different "
              "spellings of one organisation appear as separate rows.", ""]
    return "\n".join(lines)
