"""GeM-CPPP "Result of Tenders" (award of contract) connector.

Awards are outcomes, not opportunities, so this connector is deliberately NOT a
``TenderSourceConnector`` and is not in the source registry: it yields
``AwardRecord`` objects and never feeds the tender pipeline.

VERIFIED on 2026-10-10, through ``PoliteClient`` with the example configuration
(robots.txt honoured, 3 s gap), five requests in total:
- ``https://eprocure.gov.in/robots.txt`` returned 404 (no robots policy published).
- ``https://eprocure.gov.in/cppp/`` links "Result of Tenders" and "Bid Awards" to
  ``/cppp/resultoftendersnew``.
- ``/cppp/resultoftendersnew`` redirects to ``/cppp/resultoftendersnew/cpppdata``
  ("Results Of Tenders - Central"; a second tab points at ``.../mmpdata`` for states).
- That page is a SEARCH FORM ONLY: organisation, keyword, tender id, location, AOC
  published year, AOC status (Published AOC / Canceled AOC) and an image CAPTCHA
  ("What code is in the image?"). It carries no results table and no pager. Results
  are shown only after the form is submitted with the CAPTCHA answer.

Chanakya never submits that form. On the page as it is today ``parse_page`` raises
``BlockedError`` and the scan reports the source as restricted.

ASSUMED (not observed, because nothing can be listed without the CAPTCHA):
- the layout of the results table. The parser is header-driven and is tested only
  against a SYNTHETIC fixture (``tests/fixtures/html/cppp_awards_results.synthetic.html``)
  modelled on the CPPP active-tender table plus award columns;
- that bidder name and contract value appear in the listing at all (on the real
  portal they may sit on a per-tender AOC page). Missing columns give ``None``;
- that paging uses a "Next" link as the active-tender listings do.

The compliant way to get real award data in is ``chanakya awards import``: a person
runs the search in a browser, saves the result page (or a CSV), and the same parser
reads the saved file. The connector stays ``verification_status: candidate`` and
disabled until a real result page has been parsed.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..config import AwardsSettings
from ..models import AwardRecord, HealthStatus, normalize_space
from ..timeutil import now_utc, parse_portal_datetime
from .base import StructureChangedError, cell_text, has_captcha, norm_header, parse_inr_amount
from .cppp import split_title_ref

PLATFORM = "GeM-CPPP Results of Tenders"

# Logical column -> header texts (lower case). Order matters: the combined title cell
# is tested before the plain "tender id" and "reference" columns.
AWARD_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "serial": ("sl.no", "s.no", "sl no", "s no", "sr. no", "serial"),
    "award_date": ("aoc date", "award date", "date of award", "awarded date", "contract date", "aoc published date"),
    "published": ("e-published date", "e-publish date", "published date", "publish date"),
    "title_ref": ("title/ref.no./tender id", "title and ref.no./tender id", "title / ref.no. / tender id",
                  "tender title", "title"),
    "tender_id": ("tender id",),
    "reference": ("reference no", "ref no", "ref.no.", "tender reference number", "tender ref"),
    "organisation": ("organisation name", "organisation chain", "name of dept./orgn.", "organisation",
                     "organization name", "department"),
    "bidder": ("name of the selected bidder", "name of selected bidder", "selected bidder", "awarded bidder",
               "awarded to", "successful bidder", "name of bidder", "bidder name", "contractor name"),
    "value": ("contract value", "awarded value", "award value", "awarded price", "awarded amount",
              "contract amount", "value of contract"),
}
AWARD_COLUMNS = ("award_date", "bidder", "value")  # at least one must be present
_EMPTY = {"", "-", "--", "na", "n/a", "nil", "null", "not available"}
_NO_RESULTS = ("no records found", "no record found", "no tenders found", "no results found", "no data found")


@dataclass
class AwardPage:
    records: list[AwardRecord]
    next_url: str | None = None
    page_no: int = 1
    total_reported: int | None = None
    malformed: int = 0


@dataclass
class AwardScanContext:
    max_pages: int = 3
    pages_fetched: int = 0
    malformed: int = 0
    stopped_reason: str | None = None
    notes: list[str] = field(default_factory=list)


def map_award_headers(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, h in enumerate(headers):
        nh = norm_header(h)
        for key, aliases in AWARD_HEADER_ALIASES.items():
            if key in mapping:
                continue
            if any(nh == a or nh.startswith(a) for a in aliases):
                mapping[key] = idx
                break
    return mapping


def find_award_table(soup: BeautifulSoup) -> tuple[Tag, dict[str, int], int] | None:
    """First table with a title column and at least one award column. An active-tender
    table (no award column) is not accepted, so a swapped page cannot pass as awards."""
    for table in soup.find_all("table"):
        for i, row in enumerate(table.find_all("tr")[:3]):
            cells = row.find_all(["th", "td"], recursive=False) or row.find_all(["th", "td"])
            mapping = map_award_headers([c.get_text(" ", strip=True) for c in cells])
            if "title_ref" in mapping and any(c in mapping for c in AWARD_COLUMNS):
                return table, mapping, i
    return None


def _clean(text: str) -> str | None:
    t = normalize_space(text)
    return None if t.lower() in _EMPTY else t


def award_evidence(tender_id: str | None, ref_no: str | None, url: str | None, fetched, *, origin: str = "listing") -> str:
    ident = f"Tender ID {tender_id}" if tender_id else f"Ref {ref_no}" if ref_no else "no tender id stated"
    return f"{PLATFORM} {origin} ({url or 'no URL'}), {ident}, captured {fetched:%Y-%m-%d %H:%M} UTC"


class CPPPAwardsConnector:
    """Reads award listings. All HTTP goes through ``PoliteClient``; nothing is submitted."""

    def __init__(self, settings: AwardsSettings, client=None) -> None:
        self.settings = settings
        self.client = client

    @property
    def source_id(self) -> str:
        return self.settings.source_id

    def listing_url(self) -> str:
        return self.settings.url

    # ------------------------------------------------------------ fetch
    def fetch_page(self, url: str | None, page_no: int) -> AwardPage:
        if self.client is None:
            raise RuntimeError("CPPP awards connector needs an HTTP client")
        final_url, html = self.client.get_text(url or self.listing_url())
        return self.parse_page(html, final_url, page_no)

    def discover(self, ctx: AwardScanContext) -> Iterator[AwardRecord]:
        url: str | None = None
        page_no = 1
        while page_no <= ctx.max_pages:
            page = self.fetch_page(url, page_no)
            ctx.pages_fetched += 1
            ctx.malformed += page.malformed
            if not page.records:
                ctx.stopped_reason = "empty page"
                return
            yield from page.records
            if not page.next_url:
                ctx.stopped_reason = "last page"
                return
            url, page_no = page.next_url, page_no + 1
        ctx.stopped_reason = f"max_pages ({ctx.max_pages}) reached, coverage may be partial"
        ctx.notes.append(ctx.stopped_reason)

    def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        try:
            page = self.fetch_page(None, 1)
            ok = len(page.records) > 0
            detail = "award listing parsed" if ok else "award listing parsed but empty"
            return HealthStatus(source_id=self.source_id, ok=ok, detail=detail,
                                latency_ms=int((time.monotonic() - t0) * 1000), sample_count=len(page.records))
        except Exception as exc:  # noqa: BLE001 - health check reports any failure
            return HealthStatus(source_id=self.source_id, ok=False, detail=f"{type(exc).__name__}: {exc}",
                                latency_ms=int((time.monotonic() - t0) * 1000))

    # ------------------------------------------------------------ parse
    def parse_page(self, html: str, page_url: str | None, page_no: int = 1, *, origin: str = "listing") -> AwardPage:
        soup = BeautifulSoup(html, "lxml")
        found = find_award_table(soup)
        if found is None:
            text = soup.get_text(" ", strip=True).lower()
            if any(marker in text for marker in _NO_RESULTS):
                return AwardPage(records=[], page_no=page_no)
            if has_captcha(soup):
                from ..http import BlockedError
                raise BlockedError("CPPP Result of Tenders shows only a CAPTCHA search form; not submitting it")
            raise StructureChangedError("CPPP award results table not found (portal layout may have changed)")
        table, cols, header_idx = found
        fetched = now_utc()
        records: list[AwardRecord] = []
        malformed = 0
        for row in table.find_all("tr")[header_idx + 1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            if len(cells) <= max(cols.values()):
                malformed += 1
                continue
            rec = self._row_to_record(cells, cols, page_url, fetched, origin)
            if rec is None:
                malformed += 1
            else:
                records.append(rec)
        total = None
        m = re.search(r"Total\s+(?:Tenders|Results|Records|AOCs?)\s*:\s*([\d,]+)", soup.get_text(" ", strip=True), re.I)
        if m:
            total = int(m.group(1).replace(",", ""))
        next_url = None
        if page_url:
            for a in soup.find_all("a"):
                label = normalize_space(a.get_text(" ", strip=True)).lower()
                href = a.get("href")
                if href and "next" in label and "prev" not in label:
                    next_url = urljoin(page_url, href)
                    break
        return AwardPage(records=records, next_url=next_url, page_no=page_no, total_reported=total, malformed=malformed)

    def _row_to_record(self, cells, cols: dict[str, int], page_url: str | None, fetched, origin: str) -> AwardRecord | None:
        def col(name: str) -> str | None:
            return _clean(cell_text(cells[cols[name]])) if name in cols else None

        title, ref_no, tender_id, _href = split_title_ref(cells[cols["title_ref"]])
        title = _clean(title) or ""
        tender_id = col("tender_id") or tender_id
        ref_no = col("reference") or ref_no
        if not title:
            return None
        bidder = col("bidder")
        award_date = parse_portal_datetime(col("award_date"))
        value = parse_inr_amount(col("value"))
        if bidder is None and award_date is None and value is None:
            return None  # a row that states nothing about an award is not an award line
        return AwardRecord(
            source_id=self.source_id,
            tender_id=tender_id,
            ref_no=ref_no,
            title=title,
            organisation=col("organisation"),
            awarded_to=bidder,
            awarded_value_inr=value,
            award_date=award_date,
            source_url=page_url or self.listing_url(),
            evidence=award_evidence(tender_id, ref_no, page_url or self.listing_url(), fetched, origin=origin),
            fetched_at=fetched,
            raw={"listing_page": page_url, "value_text": col("value"), "award_date_text": col("award_date")},
        )
