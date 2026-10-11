"""Award-notice monitoring: storage, manual import and the BD report.

Deterministic only: no model calls. Winning bidder names and values are public
award data and are stored and printed exactly as published. This module makes no
statement about any company beyond "the page says this bidder was awarded this".
"""

from __future__ import annotations

import csv
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .connectors.base import parse_inr_amount
from .connectors.cppp_awards import AwardScanContext, CPPPAwardsConnector, award_evidence
from .db import Award, Opportunity
from .models import AwardRecord, FilterDecision, TenderRecord, normalize_space, stable_hash
from .pipeline.classify import RuleClassifier
from .pipeline.knowledge import KnowledgeBase
from .timeutil import now_utc, parse_portal_datetime, to_storage

NOT_STATED = "Not stated"


# ---------------------------------------------------------------- storage
def award_key(rec: AwardRecord) -> str:
    """One row per (tender, bidder): a contract split between bidders gives several rows."""
    bidder = normalize_space(rec.awarded_to).casefold()
    if rec.tender_id:
        return f"tid:{rec.tender_id}|{stable_hash(bidder, length=12)}"[:300]
    return "h:" + stable_hash(rec.ref_no, rec.organisation, rec.title, bidder, length=24)


@dataclass
class StoreCounts:
    seen: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0


def store_awards(session: Session, records: Iterable[AwardRecord]) -> StoreCounts:
    """Upsert by (source_id, award_key). The caller commits."""
    counts = StoreCounts()
    for rec in records:
        counts.seen += 1
        key = award_key(rec)
        values = {
            "tender_id": (rec.tender_id or None) and rec.tender_id[:120],
            "ref_no": (rec.ref_no or None) and rec.ref_no[:300],
            "title": rec.title,
            "organisation": (rec.organisation or None) and rec.organisation[:400],
            "awarded_to": (rec.awarded_to or None) and rec.awarded_to[:600],
            "awarded_value_inr": rec.awarded_value_inr,
            "award_date": to_storage(rec.award_date),
            "source_url": (rec.source_url or None) and rec.source_url[:2000],
        }
        row = session.scalar(select(Award).where(Award.source_id == rec.source_id, Award.award_key == key))
        if row is None:
            session.add(Award(source_id=rec.source_id, award_key=key, evidence=rec.evidence,
                              fetched_at=rec.fetched_at or now_utc(), **values))
            session.flush()
            counts.new += 1
            continue
        if all(getattr(row, k) == v for k, v in values.items()):
            counts.unchanged += 1
            continue
        for k, v in values.items():
            setattr(row, k, v)
        row.evidence, row.fetched_at = rec.evidence, rec.fetched_at or now_utc()
        counts.updated += 1
    return counts


# ---------------------------------------------------------------- scan / import
@dataclass
class AwardScanResult:
    status: str = "ok"            # ok | blocked | failed
    counts: StoreCounts = field(default_factory=StoreCounts)
    pages: int = 0
    malformed: int = 0
    error: str | None = None
    note: str | None = None


def scan_awards(settings: Settings, session: Session, client, *, max_pages: int | None = None) -> AwardScanResult:
    """Walk the public award listing and store what it states. A CAPTCHA, login or
    robots disallow ends the scan as ``blocked``; nothing is retried or worked around."""
    from .connectors.base import ConnectorError
    from .http import BlockedError, FetchError

    conn = CPPPAwardsConnector(settings.awards, client)
    ctx = AwardScanContext(max_pages=max_pages or settings.awards.max_pages)
    result = AwardScanResult()
    try:
        batch: list[AwardRecord] = []
        for rec in conn.discover(ctx):
            batch.append(rec)
        result.counts = store_awards(session, batch)
        session.commit()
    except BlockedError as exc:
        session.rollback()
        result.status, result.error = "blocked", str(exc)
    except (FetchError, ConnectorError) as exc:
        session.rollback()
        result.status, result.error = "failed", f"{type(exc).__name__}: {exc}"
    result.pages, result.malformed, result.note = ctx.pages_fetched, ctx.malformed, ctx.stopped_reason
    return result


_CSV_ALIASES = {
    "tender_id": ("tender_id", "tender id"),
    "ref_no": ("ref_no", "reference", "reference no", "ref no"),
    "title": ("title", "tender title"),
    "organisation": ("organisation", "organization", "organisation name"),
    "awarded_to": ("awarded_to", "awarded to", "bidder", "selected bidder", "name of selected bidder"),
    "awarded_value_inr": ("awarded_value_inr", "awarded value", "contract value", "value"),
    "award_date": ("award_date", "award date", "aoc date", "contract date"),
    "source_url": ("source_url", "url"),
}


def records_from_file(path: Path, settings: Settings) -> tuple[list[AwardRecord], int]:
    """Read awards from a file a person saved: a CPPP result page (HTML) or a CSV.
    Returns (records, malformed). No network."""
    source_id = settings.awards.source_id
    if path.suffix.lower() in (".html", ".htm"):
        conn = CPPPAwardsConnector(settings.awards)
        page = conn.parse_page(path.read_text(encoding="utf-8", errors="replace"), None, origin=f"saved page {path.name}")
        return page.records, page.malformed
    if path.suffix.lower() != ".csv":
        raise ValueError("awards import reads .html, .htm or .csv files")
    fetched = now_utc()
    records: list[AwardRecord] = []
    malformed = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            row = {normalize_space(k).lower(): normalize_space(v) for k, v in raw.items() if k}
            got = {name: next((row[a] for a in aliases if row.get(a)), None) for name, aliases in _CSV_ALIASES.items()}
            if not got["title"] or not (got["awarded_to"] or got["award_date"] or got["awarded_value_inr"]):
                malformed += 1
                continue
            records.append(AwardRecord(
                source_id=source_id, tender_id=got["tender_id"], ref_no=got["ref_no"], title=got["title"],
                organisation=got["organisation"], awarded_to=got["awarded_to"],
                awarded_value_inr=parse_inr_amount(got["awarded_value_inr"]),
                award_date=parse_portal_datetime(got["award_date"]), source_url=got["source_url"],
                evidence=award_evidence(got["tender_id"], got["ref_no"], got["source_url"], fetched,
                                        origin=f"manual import {path.name}"),
                fetched_at=fetched))
    return records, malformed


# ---------------------------------------------------------------- report
@dataclass
class AwardMatch:
    award: Award
    opportunity: Opportunity
    basis: str  # "tender id" | "reference"


@dataclass
class WinnerLine:
    name: str
    awards: int
    with_value: int
    min_value: float | None
    max_value: float | None


@dataclass
class CapabilityLine:
    capability_id: str
    awards: int
    with_value: int
    min_value: float | None
    median_value: float | None
    max_value: float | None


@dataclass
class AwardsReport:
    total_awards: int = 0
    matches: list[AwardMatch] = field(default_factory=list)
    capability_awards: int = 0
    winners: list[WinnerLine] = field(default_factory=list)
    capabilities: list[CapabilityLine] = field(default_factory=list)


def _norm(text: str | None) -> str:
    return normalize_space(text).casefold()


def match_awards(session: Session, awards: list[Award]) -> list[AwardMatch]:
    """Awards whose Tender ID or reference equals a notice already in the book.
    A reference match also needs the same organisation when both sides state one,
    because short reference numbers repeat across organisations."""
    tids = {a.tender_id for a in awards if a.tender_id}
    refs = {_norm(a.ref_no) for a in awards if a.ref_no}
    by_tid: dict[str, Opportunity] = {}
    by_ref: dict[str, list[Opportunity]] = {}
    if tids:
        for o in session.scalars(select(Opportunity).where(Opportunity.source_tender_id.in_(tids))):
            by_tid.setdefault(o.source_tender_id, o)
    if refs:
        for o in session.scalars(select(Opportunity).where(func.lower(Opportunity.ref_no).in_({r.lower() for r in refs}))):
            by_ref.setdefault(_norm(o.ref_no), []).append(o)
    out: list[AwardMatch] = []
    for a in awards:
        if a.tender_id and a.tender_id in by_tid:
            out.append(AwardMatch(a, by_tid[a.tender_id], "tender id"))
            continue
        for o in by_ref.get(_norm(a.ref_no), []) if a.ref_no else []:
            if a.organisation and o.organisation and _norm(a.organisation) != _norm(o.organisation):
                continue
            out.append(AwardMatch(a, o, "reference"))
            break
    return out


def _range(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    return min(values), statistics.median(values), max(values)


def build_report(session: Session, kb: KnowledgeBase, settings: Settings, *, top: int = 10) -> AwardsReport:
    awards = list(session.scalars(select(Award).order_by(Award.award_date.desc().nulls_last(), Award.id.desc())))
    report = AwardsReport(total_awards=len(awards), matches=match_awards(session, awards))
    classifier = RuleClassifier(kb, settings.filtering)
    by_winner: dict[str, list[Award]] = {}
    by_cap: dict[str, list[Award]] = {}
    for a in awards:
        # Title only: the same rule classifier that screens notices, so "matched a Wayam
        # capability" means the same thing here as in the book.
        rr = classifier.classify(TenderRecord(source_id=a.source_id, platform="award", source_record_key=a.award_key,
                                              title=a.title))
        if rr.decision not in (FilterDecision.CANDIDATE, FilterDecision.STRONG) or not rr.top:
            continue
        report.capability_awards += 1
        by_cap.setdefault(rr.top[0].capability_id, []).append(a)
        if a.awarded_to:
            by_winner.setdefault(_norm(a.awarded_to), []).append(a)
    for rows in by_winner.values():
        vals = [r.awarded_value_inr for r in rows if r.awarded_value_inr is not None]
        lo, _mid, hi = _range(vals)
        report.winners.append(WinnerLine(rows[0].awarded_to or "", len(rows), len(vals), lo, hi))
    report.winners.sort(key=lambda w: (-w.awards, w.name.casefold()))
    report.winners = report.winners[:top]
    for cap_id, rows in by_cap.items():
        vals = [r.awarded_value_inr for r in rows if r.awarded_value_inr is not None]
        lo, mid, hi = _range(vals)
        report.capabilities.append(CapabilityLine(cap_id, len(rows), len(vals), lo, mid, hi))
    report.capabilities.sort(key=lambda c: (-c.awards, c.capability_id))
    return report


def format_inr(value: float | None) -> str:
    if value is None:
        return NOT_STATED
    if value >= 1e7:
        return f"Rs. {value / 1e7:,.2f} crore"
    if value >= 1e5:
        return f"Rs. {value / 1e5:,.2f} lakh"
    return f"Rs. {value:,.0f}"


def format_report(report: AwardsReport) -> list[str]:
    from .timeutil import format_ist

    lines = [f"Awards stored: {report.total_awards}", "",
             f"A. Awards for notices already in the book ({len(report.matches)})"]
    if not report.matches:
        lines.append("  none")
    for m in report.matches:
        a, o = m.award, m.opportunity
        lines.append(f"  {o.id} [{o.priority or 'unassessed'}] {o.title[:90]}")
        lines.append(f"    awarded to: {a.awarded_to or NOT_STATED} | value: {format_inr(a.awarded_value_inr)} | "
                     f"award date: {format_ist(a.award_date) or NOT_STATED}")
        lines.append(f"    matched by {m.basis}: {a.tender_id if m.basis == 'tender id' else a.ref_no} | "
                     f"desk status: {o.review_status}")
        lines.append(f"    evidence: {a.evidence or NOT_STATED}")
    lines += ["", f"B. Awards whose title matched a Wayam capability ({report.capability_awards})"]
    if not report.capability_awards:
        lines.append("  none")
        return lines
    lines.append("  Most frequent winners (names as published):")
    if not report.winners:
        lines.append("    no bidder name stated on these awards")
    for w in report.winners:
        span = NOT_STATED if w.min_value is None else (
            format_inr(w.min_value) if w.min_value == w.max_value else f"{format_inr(w.min_value)} to {format_inr(w.max_value)}")
        lines.append(f"    {w.awards:>3}  {w.name[:70]}  | value: {span} ({w.with_value} of {w.awards} state a value)")
    lines.append("  Value ranges by capability:")
    for c in report.capabilities:
        if c.min_value is None:
            span = NOT_STATED
        elif c.min_value == c.max_value:
            span = format_inr(c.min_value)
        else:
            span = f"{format_inr(c.min_value)} to {format_inr(c.max_value)}, median {format_inr(c.median_value)}"
        lines.append(f"    {c.capability_id:<22} {c.awards:>3} awards | {span} ({c.with_value} state a value)")
    return lines
