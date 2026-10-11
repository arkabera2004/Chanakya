"""Stage 3 — deduplication, persistence and change tracking.

Identity rules (in order):
1. GePNIC-style Tender IDs (``2026_BPCL_26936``) are globally unique across
   CPPP, eprocure, etenders and state GePNIC portals → ``tid:<ID>``.
2. Other source IDs are only unique per organisation → ``sid:<org>:<id>``.
3. Reference number + organisation → ``ref:<org>:<ref>``.
4. Fallback content key → ``h:<hash(title, org, closing date)>``.
A fuzzy check (same closing date, ≥0.92 title similarity, same organisation)
catches the same notice published on two portals with different identifiers.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..connectors.base import GEPNIC_TENDER_ID
from ..db import ChangeEvent, Opportunity, OpportunityVersion, RawOpportunity
from ..models import TenderRecord, TenderStatus, normalize_space, stable_hash
from ..timeutil import format_ist, now_utc, to_storage
from .textnorm import normalize

FUZZY_TITLE_THRESHOLD = 0.92


def _norm_key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def dedup_key(rec: TenderRecord) -> str:
    tid = (rec.source_tender_id or "").strip()
    if tid and GEPNIC_TENDER_ID.match(tid):
        return f"tid:{tid.upper()}"
    org = _norm_key(rec.organisation)
    if tid:
        return f"sid:{org}:{_norm_key(tid)}"
    if rec.ref_no and org:
        return f"ref:{org}:{_norm_key(rec.ref_no)}"
    closing = rec.closing_at.date().isoformat() if rec.closing_at else ""
    return f"h:{stable_hash(normalize(rec.title), org, closing)}"


def opportunity_id(key: str) -> str:
    return "CHK-" + stable_hash(key, length=10).upper()


def retender_base(tid: str | None) -> tuple[str, int] | None:
    if tid and GEPNIC_TENDER_ID.match(tid):
        parts = tid.split("_")
        if len(parts) == 4:
            return "_".join(parts[:3]).upper(), int(parts[3])
    return None


@dataclass
class UpsertResult:
    opportunity: Opportunity
    outcome: str  # new | updated | unchanged | duplicate
    changes: list[ChangeEvent] = field(default_factory=list)


def _snapshot(rec: TenderRecord) -> dict:
    data = rec.model_dump(mode="json", exclude={"raw"})
    return data


def _apply(opp: Opportunity, rec: TenderRecord) -> None:
    opp.platform = rec.platform
    opp.source_record_key = rec.source_record_key
    opp.source_tender_id = rec.source_tender_id
    opp.ref_no = rec.ref_no
    opp.title = normalize_space(rec.title)
    opp.organisation = rec.organisation
    opp.department = rec.department
    opp.state = rec.state
    opp.location = rec.location
    opp.opportunity_type = rec.opportunity_type.value
    opp.category = rec.category
    opp.tender_category = rec.tender_category
    opp.published_at = to_storage(rec.published_at)
    opp.closing_at = to_storage(rec.closing_at)
    opp.opening_at = to_storage(rec.opening_at)
    opp.estimated_value_inr = rec.estimated_value_inr
    opp.emd_inr = rec.emd_inr
    opp.description = rec.description
    opp.official_url = rec.official_url
    opp.detail_url = rec.detail_url
    opp.detail_url_expires = rec.detail_url_expires
    opp.document_refs = [d.model_dump() for d in rec.documents]
    opp.has_corrigendum = rec.has_corrigendum
    opp.status = rec.status.value
    opp.evidence = rec.evidence
    opp.content_hash = rec.content_hash()


def _fmt(v) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return format_ist(to_storage(v)) if getattr(v, "tzinfo", None) else format_ist(v)
    return str(v)


def diff_changes(opp: Opportunity, rec: TenderRecord, run_id: str | None) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []

    def ev(ctype: str, fieldname: str, old, new, material: bool = True) -> None:
        events.append(ChangeEvent(opportunity_id=opp.id, run_id=run_id, change_type=ctype, field=fieldname,
                                  old_value=_fmt(old), new_value=_fmt(new), material=material))

    new_closing = to_storage(rec.closing_at)
    if new_closing != opp.closing_at and (new_closing or opp.closing_at):
        ev("deadline_changed", "closing_at", opp.closing_at, new_closing)
    new_opening = to_storage(rec.opening_at)
    if new_opening != opp.opening_at and (new_opening or opp.opening_at):
        ev("deadline_changed", "opening_at", opp.opening_at, new_opening, material=False)
    if rec.estimated_value_inr != opp.estimated_value_inr and rec.estimated_value_inr is not None:
        ev("value_changed", "estimated_value_inr", opp.estimated_value_inr, rec.estimated_value_inr)
    if rec.emd_inr != opp.emd_inr and rec.emd_inr is not None:
        ev("value_changed", "emd_inr", opp.emd_inr, rec.emd_inr)
    if rec.has_corrigendum and not opp.has_corrigendum:
        ev("corrigendum_added", "corrigendum", None, "; ".join(rec.corrigenda) or "corrigendum published")
    if rec.status.value != opp.status:
        ev("status_changed", "status", opp.status, rec.status.value)
    if normalize_space(rec.title) != normalize_space(opp.title):
        ev("details_changed", "title", opp.title, rec.title)
    if (rec.description or None) != (opp.description or None) and rec.description:
        ev("details_changed", "description", (opp.description or "")[:200], rec.description[:200])
    old_docs = sorted((d.get("filename") or d.get("url") or "") for d in (opp.document_refs or []))
    new_docs = sorted((d.filename or d.url or "") for d in rec.documents)
    if old_docs != new_docs and any(new_docs):
        ev("document_changed", "documents", ", ".join(old_docs), ", ".join(new_docs))
    return events


def _find_fuzzy_duplicate(session: Session, rec: TenderRecord) -> Opportunity | None:
    closing = to_storage(rec.closing_at)
    conditions = []
    if rec.ref_no and len(_norm_key(rec.ref_no)) >= 6:
        conditions.append(Opportunity.ref_no == rec.ref_no)
    if closing:
        conditions.append(and_(Opportunity.closing_at >= closing - timedelta(hours=1),
                               Opportunity.closing_at <= closing + timedelta(hours=1)))
    if not conditions:
        return None
    candidates = session.scalars(select(Opportunity).where(or_(*conditions)).limit(200)).all()
    title_n = normalize(rec.title)
    org_n = _norm_key(rec.organisation)
    for c in candidates:
        if c.source_id == rec.source_id and c.source_record_key == rec.source_record_key:
            continue
        same_ref = rec.ref_no and c.ref_no and _norm_key(c.ref_no) == _norm_key(rec.ref_no)
        org_ok = not org_n or not c.organisation or org_n == _norm_key(c.organisation) or \
            difflib.SequenceMatcher(None, org_n, _norm_key(c.organisation)).ratio() > 0.8
        if same_ref and org_ok:
            return c
        sim = difflib.SequenceMatcher(None, title_n, normalize(c.title)).ratio()
        if sim >= FUZZY_TITLE_THRESHOLD and org_ok and closing and c.closing_at and \
                abs((c.closing_at - closing).total_seconds()) <= 3600:
            return c
    return None


def upsert_record(session: Session, rec: TenderRecord, run_id: str | None) -> UpsertResult:
    chash = rec.content_hash()
    exists_raw = session.scalar(select(RawOpportunity.id).where(
        RawOpportunity.source_id == rec.source_id, RawOpportunity.source_record_key == rec.source_record_key,
        RawOpportunity.content_hash == chash))
    if exists_raw is None:
        session.add(RawOpportunity(source_id=rec.source_id, source_record_key=rec.source_record_key, run_id=run_id,
                                   content_hash=chash, payload=rec.model_dump(mode="json")))

    key = dedup_key(rec)
    opp = session.scalar(select(Opportunity).where(Opportunity.dedup_key == key))
    if opp is None and not key.startswith("tid:"):
        # GePNIC Tender IDs are globally unique; a new ID (e.g. a retender "_2") is a new notice.
        dup = _find_fuzzy_duplicate(session, rec)
        if dup is not None:
            opp = dup
    now = now_utc()
    if opp is None:
        opp = Opportunity(id=opportunity_id(key), dedup_key=key, source_id=rec.source_id, version=1,
                          first_seen_at=now, last_seen_at=now, last_verified_at=now, first_seen_run=run_id,
                          last_changed_run=run_id, analysis_state="pending", review_status="New",
                          also_seen_on=[], filter_reasons=[], top_capabilities=[], score_breakdown={}, intelligence={})
        _apply(opp, rec)
        session.add(opp)
        session.flush()
        session.add(OpportunityVersion(opportunity_id=opp.id, version=1, run_id=run_id, content_hash=opp.content_hash,
                                       snapshot=_snapshot(rec)))
        changes = [ChangeEvent(opportunity_id=opp.id, run_id=run_id, change_type="new", field=None,
                               old_value=None, new_value=rec.title[:200])]
        rb = retender_base(rec.source_tender_id)
        if rb and rb[1] > 1:
            prev = session.scalars(select(Opportunity).where(
                Opportunity.source_tender_id.ilike(f"{rb[0]}\\_%", escape="\\"), Opportunity.id != opp.id)).all()
            for p in prev:
                changes.append(ChangeEvent(opportunity_id=opp.id, run_id=run_id, change_type="retendered",
                                           field="source_tender_id", old_value=p.source_tender_id,
                                           new_value=rec.source_tender_id))
                if p.status == TenderStatus.ACTIVE.value:
                    p.status = TenderStatus.RETENDERED.value
        session.add_all(changes)
        return UpsertResult(opp, "new", changes)

    # Existing opportunity
    opp.last_seen_at = now
    opp.last_verified_at = now
    if opp.source_id != rec.source_id:
        seen = list(opp.also_seen_on or [])
        entry = {"source_id": rec.source_id, "platform": rec.platform, "key": rec.source_record_key}
        if entry not in seen:
            seen.append(entry)
            opp.also_seen_on = seen
        # Enrich empty fields from the other source without overwriting.
        for attr in ("description", "estimated_value_inr", "emd_inr", "category", "department", "location"):
            if getattr(opp, attr) in (None, "") and getattr(rec, attr) not in (None, ""):
                setattr(opp, attr, getattr(rec, attr))
        return UpsertResult(opp, "duplicate", [])
    if opp.content_hash == chash:
        return UpsertResult(opp, "unchanged", [])
    changes = diff_changes(opp, rec, run_id)
    _apply(opp, rec)
    opp.version += 1
    opp.last_changed_run = run_id
    session.add(OpportunityVersion(opportunity_id=opp.id, version=opp.version, run_id=run_id,
                                   content_hash=opp.content_hash, snapshot=_snapshot(rec)))
    session.add_all(changes)
    return UpsertResult(opp, "updated", changes)


def mark_expired(session: Session, run_id: str | None) -> int:
    now = now_utc()
    rows = session.scalars(select(Opportunity).where(
        Opportunity.status == TenderStatus.ACTIVE.value, Opportunity.closing_at.is_not(None),
        Opportunity.closing_at < now)).all()
    for o in rows:
        session.add(ChangeEvent(opportunity_id=o.id, run_id=run_id, change_type="status_changed", field="status",
                                old_value=o.status, new_value=TenderStatus.EXPIRED.value, material=False))
        o.status = TenderStatus.EXPIRED.value
    return len(rows)
