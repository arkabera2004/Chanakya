"""Canonical data contracts shared by every module.

Connectors produce ``TenderRecord`` objects; nothing downstream knows which
portal a record came from except through ``source_id``/``platform``.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class OpportunityType(StrEnum):
    TENDER = "Tender"
    RFP = "RFP"
    RFQ = "RFQ"
    RFI = "RFI"
    EOI = "EOI"
    EMPANELMENT = "Empanelment"
    BID = "Bid"  # GeM bid / reverse auction
    RATE_CONTRACT = "Rate Contract"
    CORRIGENDUM = "Corrigendum"
    UNKNOWN = "Unknown"


class TenderStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    WITHDRAWN = "withdrawn"
    AWARDED = "awarded"
    RETENDERED = "retendered"
    UNKNOWN = "unknown"


class EligibilityStatus(StrEnum):
    VERIFIED_ELIGIBLE = "Verified eligible"
    POTENTIALLY_ELIGIBLE = "Potentially eligible"
    PARTNER_REQUIRED = "Partner required"
    LIKELY_INELIGIBLE = "Likely ineligible"
    UNKNOWN = "Unknown — human review required"


class Priority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class FilterDecision(StrEnum):
    REJECTED = "rejected"          # clearly unrelated (deterministic)
    LOW = "low"                    # kept for record, no further work
    CANDIDATE = "candidate"        # worth semantic + possible LLM
    STRONG = "strong"              # strong deterministic match


class AnalysisState(StrEnum):
    NOT_NEEDED = "not_needed"
    PENDING = "pending"
    DONE = "done"
    CACHED = "cached"
    SKIPPED_BUDGET = "skipped_budget"
    SKIPPED_DISABLED = "skipped_llm_disabled"
    DEFERRED = "deferred_no_documents"  # strong/confirmed match but only listing-level text — waiting for documents
    FAILED = "failed"


class ReviewStatus(StrEnum):
    NEW = "New"
    REVIEWING = "Reviewing"
    SHORTLISTED = "Shortlisted"
    QUALIFIED = "Qualified"
    PARTNER_NEEDED = "Partner needed"
    PURSUING = "Pursuing"
    REJECTED = "Rejected"
    EXPIRED = "Expired"
    WON = "Won"
    LOST = "Lost"


class DocumentRef(BaseModel):
    url: str | None = None
    filename: str | None = None
    local_path: str | None = None
    kind: str = "attachment"  # nit | boq | corrigendum | attachment
    access: str = "unknown"  # direct | captcha_required | login_required | manual


class TenderRecord(BaseModel):
    """Canonical, source-agnostic opportunity as extracted (no LLM involved)."""

    source_id: str
    platform: str
    source_record_key: str
    source_tender_id: str | None = None
    ref_no: str | None = None
    title: str
    organisation: str | None = None
    department: str | None = None
    state: str | None = None
    location: str | None = None
    opportunity_type: OpportunityType = OpportunityType.UNKNOWN
    category: str | None = None
    tender_category: str | None = None  # Goods / Works / Services
    published_at: datetime | None = None
    closing_at: datetime | None = None
    opening_at: datetime | None = None
    estimated_value_inr: float | None = None
    emd_inr: float | None = None
    description: str | None = None
    official_url: str | None = None
    detail_url: str | None = None
    detail_url_expires: bool = False
    documents: list[DocumentRef] = Field(default_factory=list)
    has_corrigendum: bool = False
    corrigenda: list[str] = Field(default_factory=list)
    status: TenderStatus = TenderStatus.ACTIVE
    evidence: str | None = None  # human-readable provenance line
    fetched_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    MATERIAL_FIELDS: ClassVar[tuple[str, ...]] = (
        "title", "ref_no", "organisation", "department", "published_at", "closing_at",
        "opening_at", "estimated_value_inr", "emd_inr", "description", "has_corrigendum",
        "corrigenda", "status", "category",
    )

    def content_hash(self) -> str:
        """Hash of the material fields only (so re-fetch timestamps don't count as changes)."""
        payload = {}
        for f in self.MATERIAL_FIELDS:
            v = getattr(self, f)
            if isinstance(v, datetime):
                v = v.isoformat()
            elif isinstance(v, str):
                v = normalize_space(v)
            payload[f] = v
        payload["documents"] = sorted(d.filename or d.url or "" for d in self.documents)
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def text_for_matching(self) -> str:
        parts = [self.title, self.category or "", self.tender_category or "",
                 self.organisation or "", self.department or "", self.description or ""]
        return normalize_space(" . ".join(p for p in parts if p))


class ListingPage(BaseModel):
    records: list[TenderRecord]
    next_url: str | None = None
    page_no: int = 1
    total_reported: int | None = None


class HealthStatus(BaseModel):
    source_id: str
    ok: bool
    detail: str
    latency_ms: int | None = None
    sample_count: int = 0


class AwardRecord(BaseModel):
    """One published award-of-contract line. Kept apart from ``TenderRecord``: an
    award is an outcome, not an opportunity. Bidder name and value are stored as
    published; a missing value stays ``None`` (reported as "Not stated")."""

    source_id: str
    tender_id: str | None = None
    ref_no: str | None = None
    title: str
    organisation: str | None = None
    awarded_to: str | None = None          # winning bidder name, exactly as the page states it
    awarded_value_inr: float | None = None
    award_date: datetime | None = None     # aware IST
    source_url: str | None = None
    evidence: str | None = None            # human-readable provenance line
    fetched_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    source: str  # "listing" | "detail" | "document" | "manual"
    url: str | None = None
    document: str | None = None
    page: int | None = None
    section: str | None = None
    quote: str | None = None

    def label(self) -> str:
        bits = [self.source]
        if self.document:
            bits.append(self.document)
        if self.page:
            bits.append(f"p.{self.page}")
        if self.section:
            bits.append(f"§{self.section}")
        return " ".join(bits)


class EligibilityFinding(BaseModel):
    criterion: str  # turnover | experience | certification | oem_authorization | consortium | emd | local_presence | mse_exemption | other
    requirement_text: str
    value: float | None = None
    unit: str | None = None
    explicit: bool = True
    source: str = "rule"  # rule | llm
    assessment: str = "unknown"  # meets | does_not_meet | partner | unknown | not_applicable
    evidence: EvidenceRef | None = None


_ws = re.compile(r"\s+")


def normalize_space(text: str | None) -> str:
    return _ws.sub(" ", text or "").strip()


def stable_hash(*parts: Any, length: int = 16) -> str:
    blob = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(blob.encode()).hexdigest()[:length]
