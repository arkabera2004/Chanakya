"""Connector contract and shared parsing helpers.

Every procurement source implements ``TenderSourceConnector``. Connectors only
acquire and normalise data; they never filter for relevance, call an LLM or
write to the database. That keeps acquisition independent from inference.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from ..config import SourceConfig
from ..models import DocumentRef, HealthStatus, ListingPage, OpportunityType, TenderRecord, normalize_space


class ConnectorError(Exception):
    pass


class StructureChangedError(ConnectorError):
    """The page no longer matches the expected structure (portal redesign)."""


@dataclass
class DiscoveryContext:
    """Incremental-discovery state handed to a connector."""

    since: datetime | None = None          # stop when published_at (IST-aware) is older than this
    max_pages: int = 30
    run_id: str | None = None
    start_url: str | None = None           # continue a listing already in progress
    notes: list[str] = field(default_factory=list)
    pages_fetched: int = 0
    malformed: int = 0
    stopped_reason: str | None = None
    resume_url: str | None = None          # next listing URL when max_pages stops the walk


class TenderSourceConnector(ABC):
    """Standard interface for all procurement sources."""

    supports_documents: bool = False
    supports_detail: bool = False

    def __init__(self, config: SourceConfig, client=None) -> None:
        self.config = config
        self.client = client

    @property
    def source_id(self) -> str:
        return self.config.id

    # Discovery -----------------------------------------------------------
    def discover(self, ctx: DiscoveryContext) -> Iterator[TenderRecord]:
        """Yield new/recent records, newest first, stopping at ``ctx.since``.

        Default implementation walks listing pages via ``fetch_listing``."""
        url: str | None = ctx.start_url
        page_no = 1
        while page_no <= ctx.max_pages:
            page = self.fetch_listing(url, page_no)
            ctx.pages_fetched += 1
            if not page.records:
                ctx.stopped_reason = "empty page"
                return
            older_seen = 0
            for rec in page.records:
                if ctx.since and rec.published_at and rec.published_at < ctx.since:
                    older_seen += 1
                    continue
                yield rec
            if older_seen == len(page.records):
                ctx.stopped_reason = "reached high-water mark"
                return
            if not page.next_url:
                ctx.stopped_reason = "last page"
                return
            url, page_no = page.next_url, page_no + 1
        ctx.resume_url = url
        ctx.stopped_reason = f"max_pages ({ctx.max_pages}) reached — coverage may be partial"
        ctx.notes.append(ctx.stopped_reason)

    @abstractmethod
    def fetch_listing(self, url: str | None, page_no: int) -> ListingPage:
        ...

    def fetch_detail(self, record: TenderRecord) -> TenderRecord:
        return record

    def fetch_documents(self, record: TenderRecord) -> list[DocumentRef]:
        """Documents that can be fetched *without* bypassing access controls."""
        return [d for d in record.documents if d.access in ("direct", "manual")]

    def normalize(self, raw: dict) -> TenderRecord:  # pragma: no cover - per-connector
        raise NotImplementedError

    def check_updates(self, keys: list[str]) -> dict[str, str]:
        """Return {source_record_key: status} for records whose status can be
        re-verified (e.g. cancelled). Default: not supported.

        No connector implements this, on purpose. CPPP and GePNIC offer no public
        per-notice lookup: single notices sit behind a CAPTCHA search or a
        session-bound detail link, and Chanakya does not bypass either. Changes are
        found by re-reading the listing (see doc 12, "How changes are detected")."""
        return {}

    def health_check(self) -> HealthStatus:
        import time
        t0 = time.monotonic()
        try:
            page = self.fetch_listing(None, 1)
            ok = len(page.records) > 0
            return HealthStatus(source_id=self.source_id, ok=ok,
                                detail="listing parsed" if ok else "listing parsed but empty",
                                latency_ms=int((time.monotonic() - t0) * 1000), sample_count=len(page.records))
        except Exception as exc:  # noqa: BLE001 - health check reports any failure
            return HealthStatus(source_id=self.source_id, ok=False, detail=f"{type(exc).__name__}: {exc}",
                                latency_ms=int((time.monotonic() - t0) * 1000))


# ------------------------------------------------------------------ helpers

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "serial": ("sl.no", "s.no", "sl no", "s no", "sr. no", "serial"),
    "published": ("e-published date", "e-publish date", "published date", "publish date", "e-published"),
    "closing": ("bid submission closing date", "closing date", "bid submission end date", "last date", "bid end date"),
    "opening": ("tender opening date", "opening date", "bid opening date"),
    "title_ref": ("title/ref.no./tender id", "title and ref.no./tender id", "title / ref.no. / tender id",
                  "tender title", "title", "corrigendum title"),
    "reference": ("reference no", "ref no", "ref.no.", "tender reference number"),
    "organisation": ("organisation name", "organisation chain", "name of dept./orgn.", "organisation",
                     "organization name", "department"),
    "corrigendum": ("corrigendum",),
    "bid_start": ("bid start date",),
    "bid_end": ("bid end date",),
    "bid_no": ("bid number/total quantity", "bid number"),
    "product": ("product category",),
    "department": ("department name",),
}


def norm_header(text: str) -> str:
    return normalize_space(text).lower().rstrip(":").strip()


def map_headers(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, h in enumerate(headers):
        nh = norm_header(h)
        for key, aliases in HEADER_ALIASES.items():
            if key in mapping:
                continue
            if any(nh == a or nh.startswith(a) for a in aliases):
                mapping[key] = idx
                break
    return mapping


def find_data_table(soup: BeautifulSoup, required: tuple[str, ...]) -> tuple[Tag, dict[str, int], int] | None:
    """Find the first table whose header row maps all ``required`` logical columns.
    Returns (table, mapping, header_row_index)."""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for i, row in enumerate(rows[:3]):
            cells = row.find_all(["th", "td"], recursive=False) or row.find_all(["th", "td"])
            headers = [c.get_text(" ", strip=True) for c in cells]
            mapping = map_headers(headers)
            if all(r in mapping for r in required):
                return table, mapping, i
    return None


def has_captcha(soup: BeautifulSoup) -> bool:
    """CAPTCHA widgets usually show up in attributes (alt/title/name/id), not text."""
    if "captcha" in soup.get_text(" ", strip=True).lower():
        return True
    for tag in soup.find_all(["img", "input", "div", "form", "iframe"]):
        blob = " ".join(str(tag.get(a, "")) for a in ("alt", "title", "name", "id", "class", "src")).lower()
        if "captcha" in blob:
            return True
    return False


def cell_text(cell: Tag | None) -> str:
    return normalize_space(cell.get_text(" ", strip=True)) if cell is not None else ""


_TYPE_PATTERNS: list[tuple[OpportunityType, re.Pattern[str]]] = [
    (OpportunityType.CORRIGENDUM, re.compile(r"\b(corrigendum|addendum|amendment)\b", re.I)),
    (OpportunityType.EOI, re.compile(r"\b(expression of interest|eoi)\b", re.I)),
    (OpportunityType.EMPANELMENT, re.compile(r"\bempanel(l)?ment\b", re.I)),
    (OpportunityType.RFP, re.compile(r"\b(request for proposals?|rfp)\b", re.I)),
    (OpportunityType.RFI, re.compile(r"\b(request for information|rfi)\b", re.I)),
    (OpportunityType.RFQ, re.compile(r"\b(request for quotations?|rfq|e-?quotation|eq)\b", re.I)),
    (OpportunityType.RATE_CONTRACT, re.compile(r"\brate contract\b", re.I)),
    (OpportunityType.BID, re.compile(r"\bGEM/\d{4}/[BR]/\d+", re.I)),
]


def infer_opportunity_type(*texts: str | None) -> OpportunityType:
    blob = " ".join(t for t in texts if t)
    for otype, pat in _TYPE_PATTERNS:
        if pat.search(blob):
            return otype
    return OpportunityType.TENDER if blob else OpportunityType.UNKNOWN


INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar", "Chandigarh",
    "Dadra and Nagar Haveli", "Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]
_STATE_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in sorted(INDIAN_STATES, key=len, reverse=True)) + r")\b", re.I)


def infer_state(*texts: str | None) -> str | None:
    for t in texts:
        if not t:
            continue
        m = _STATE_RE.search(t)
        if m:
            for s in INDIAN_STATES:
                if s.lower() == m.group(1).lower():
                    return s
    return None


GEPNIC_TENDER_ID = re.compile(r"^\d{4}_[A-Za-z0-9]+_\d+(?:_\d+)?$")


def parse_inr_amount(text: str | None) -> float | None:
    """Parse '₹ 1,23,456.00' / 'Rs. 5 Lakh' / '2.5 crore' into rupees. None if absent."""
    if not text:
        return None
    t = text.replace(",", "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(crore|cr\b|lakh|lac|lakhs|million|mn)?", t)
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "").strip()
    if unit in ("crore", "cr"):
        value *= 1e7
    elif unit in ("lakh", "lac", "lakhs"):
        value *= 1e5
    elif unit in ("million", "mn"):
        value *= 1e6
    return value if value > 0 else None
