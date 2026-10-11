"""GeM-CPPP (Central Public Procurement Portal) connector.

Verified on 2026-10-10 (see docs/02_GOVERNMENT_SOURCE_RESEARCH.md):
- ``/cppp/latestactivetendersnew/{cpppdata|mmpdata}`` lists active tenders as
  server-rendered HTML, 10 rows per page, newest first, with no login and no
  CAPTCHA for browsing. ``gemdata`` is the same public table but oldest first,
  so that feed starts at the last page and follows "Previous".
- The *search form* on the same page has a CAPTCHA; Chanakya never submits it.
- Pagination links carry a base64-encoded ``url`` parameter; we follow the
  link the page provides instead of constructing URLs.
- Columns: Sl.No | e-Published Date | Bid Submission Closing Date | Tender
  Opening Date | Title/Ref.No./Tender Id | Organisation Name | Corrigendum.
- Detail links (``/cppp/tendersfullview/...``) embed a timestamp token and stop
  working later ("Invalid Url"), so they are stored as *expiring* links. The
  stable identifier is the Tender ID; the stable official reference is the feed
  page plus the Tender ID.
- Tender documents require the source portal's CAPTCHA, so documents are not
  fetched automatically (manual import path instead).
"""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import DocumentRef, ListingPage, OpportunityType, TenderRecord, TenderStatus, normalize_space, stable_hash
from ..timeutil import now_utc, parse_portal_datetime
from .base import (
    GEPNIC_TENDER_ID, StructureChangedError, TenderSourceConnector, cell_text, find_data_table, has_captcha,
    infer_opportunity_type, infer_state,
)

CPPP_BASE = "https://eprocure.gov.in/cppp/"
FEED_LABELS = {
    "cpppdata": "GeM-CPPP — Central active tenders",
    "mmpdata": "GeM-CPPP — State active tenders",
    "gemdata": "GeM-CPPP — GeM active bids",
}


def page_url(feed: str, page: int) -> str:
    inner = f"{CPPP_BASE}latestactivetendersnew/{feed}?page={page}"
    token = base64.b64encode(inner.encode()).decode()
    return f"{CPPP_BASE}latestactivetendersnew/{feed}?url={token}"


def _last_page_href(html: str, page_url_: str) -> str | None:
    """Largest numbered pagination link. gemdata is oldest-first, so this is the newest page."""
    soup = BeautifulSoup(html, "lxml")
    best_n = 0
    best: str | None = None
    for a in soup.find_all("a"):
        label = normalize_space(a.get_text(" ", strip=True))
        href = a.get("href")
        if href and label.isdigit():
            n = int(label)
            if n > best_n:
                best_n, best = n, urljoin(page_url_, href)
    return best


def decode_page_number(url: str) -> int | None:
    try:
        token = parse_qs(urlsplit(url).query).get("url", [None])[0]
        if not token:
            return None
        inner = base64.b64decode(token + "=" * (-len(token) % 4)).decode()
        m = re.search(r"[?&]page=(\d+)", inner)
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001 - defensive decode of untrusted data
        return None


def split_title_ref(cell) -> tuple[str, str | None, str | None, str | None]:
    """Return (title, ref_no, tender_id, detail_href) from a CPPP title cell.

    Observed format: ``<a href=...>Title</a>/Ref.No./TenderId``. Reference numbers
    themselves contain slashes, so the tender id is the *last* segment."""
    anchor = cell.find("a")
    full = normalize_space(cell.get_text(" ", strip=True))
    href = anchor.get("href") if anchor else None
    if anchor:
        title = normalize_space(anchor.get_text(" ", strip=True))
        tail = full[len(title):] if full.startswith(title) else full.replace(title, "", 1)
    else:
        title, tail = full, ""
    tail = tail.strip().strip("/").strip()
    ref_no = tender_id = None
    if tail:
        if "/" in tail:
            ref_no, tender_id = (s.strip() for s in tail.rsplit("/", 1))
        else:
            tender_id = tail
    if not anchor and tender_id is None:
        # No anchor: try "title/ref/id" using the GePNIC id pattern for the last token.
        parts = [p.strip() for p in full.split("/")]
        if len(parts) >= 2 and GEPNIC_TENDER_ID.match(parts[-1]):
            tender_id = parts[-1]
    return title, ref_no or None, tender_id or None, href


class CPPPConnector(TenderSourceConnector):
    supports_detail = False
    supports_documents = False

    @property
    def feed(self) -> str:
        return self.config.feed or "cpppdata"

    @property
    def platform(self) -> str:
        return self.config.platform_label or FEED_LABELS.get(self.feed, "GeM-CPPP")

    def listing_url(self) -> str:
        return f"{CPPP_BASE}latestactivetendersnew/{self.feed}"

    def fetch_listing(self, url: str | None, page_no: int) -> ListingPage:
        if self.client is None:
            raise RuntimeError("CPPP connector needs an HTTP client")
        target = url or self.listing_url()
        final_url, html = self.client.get_text(target)
        if url is None and self.feed == "gemdata":
            last = _last_page_href(html, final_url)
            if last and last != final_url:
                final_url, html = self.client.get_text(last)
        return self.parse_listing(html, final_url, page_no)

    def parse_listing(self, html: str, page_url_: str, page_no: int = 1) -> ListingPage:
        soup = BeautifulSoup(html, "lxml")
        found = find_data_table(soup, ("published", "title_ref"))
        gem_mode = False
        if found is None:
            found = find_data_table(soup, ("bid_start", "bid_no"))
            gem_mode = found is not None
        if found is None:
            text = soup.get_text(" ", strip=True).lower()
            if "no tenders found" in text or "no records found" in text:
                return ListingPage(records=[], page_no=page_no)
            if has_captcha(soup) and "total tenders" not in text:
                from ..http import BlockedError
                raise BlockedError("CPPP listing replaced by a CAPTCHA page; not bypassing")
            raise StructureChangedError("CPPP results table not found (portal layout may have changed)")
        table, cols, header_idx = found
        records: list[TenderRecord] = []
        fetched = now_utc()
        for row in table.find_all("tr")[header_idx + 1:]:
            cells = row.find_all("td")
            if len(cells) <= max(cols.values()):
                continue
            try:
                rec = (self._gem_row_to_record(cells, cols, page_url_, fetched) if gem_mode
                       else self._row_to_record(cells, cols, page_url_, fetched))
            except ValueError:
                continue
            if rec is not None:
                records.append(rec)
        total = None
        m = re.search(r"Total\s+(?:Tenders|Bids)\s*:\s*([\d,]+)", soup.get_text(" ", strip=True), re.I)
        if m:
            total = int(m.group(1).replace(",", ""))
        next_url = None
        prev_url = None
        for a in soup.find_all("a"):
            label = normalize_space(a.get_text(" ", strip=True)).lower()
            href = a.get("href")
            if not href:
                continue
            if "next" in label and "prev" not in label:
                next_url = next_url or urljoin(page_url_, href)
            elif "prev" in label:
                prev_url = prev_url or urljoin(page_url_, href)
        # gemdata is oldest-first. Walking Previous from the last page reaches new bids
        # without reading the whole archive from August onward.
        if self.feed == "gemdata":
            next_url = prev_url
        return ListingPage(records=records, next_url=next_url, page_no=page_no, total_reported=total)

    def _gem_row_to_record(self, cells, cols, page_url_: str, fetched) -> TenderRecord | None:
        blob = cell_text(cells[cols["bid_no"]])
        match = re.search(r"(GEM/\d{4}/B/\d+)", blob, re.I)
        tender_id = match.group(1) if match else None
        product = cell_text(cells[cols["product"]]) if "product" in cols else ""
        title = product or tender_id or blob
        if not title:
            raise ValueError("missing title")
        published = parse_portal_datetime(cell_text(cells[cols["bid_start"]]))
        closing = parse_portal_datetime(cell_text(cells[cols["bid_end"]])) if "bid_end" in cols else None
        org = cell_text(cells[cols["organisation"]]) if "organisation" in cols else None
        department = cell_text(cells[cols["department"]]) if "department" in cols else None
        key = tender_id or f"h:{stable_hash(blob, org, title, published)}"
        return TenderRecord(
            source_id=self.source_id,
            platform=self.platform,
            source_record_key=key,
            source_tender_id=tender_id,
            ref_no=tender_id,
            title=title,
            organisation=org or None,
            department=department or None,
            state=infer_state(org, title),
            opportunity_type=OpportunityType.BID,
            category=product or None,
            published_at=published,
            closing_at=closing,
            official_url=self.listing_url(),
            documents=[DocumentRef(url=None, kind="nit", access="captcha_required")],
            status=TenderStatus.ACTIVE,
            evidence=f"{self.platform} listing ({self.listing_url()}), Bid {tender_id or 'n/a'}, "
                     f"captured {fetched:%Y-%m-%d %H:%M} UTC",
            fetched_at=fetched,
            raw={"feed": self.feed, "listing_page": page_url_, "bid_cell": blob},
        )

    def _row_to_record(self, cells, cols, page_url_: str, fetched) -> TenderRecord | None:
        title, ref_no, tender_id, href = split_title_ref(cells[cols["title_ref"]])
        if not title:
            raise ValueError("missing title")
        published = parse_portal_datetime(cell_text(cells[cols["published"]]))
        closing = parse_portal_datetime(cell_text(cells[cols["closing"]])) if "closing" in cols else None
        opening = parse_portal_datetime(cell_text(cells[cols["opening"]])) if "opening" in cols else None
        org = cell_text(cells[cols["organisation"]]) if "organisation" in cols else None
        corr_text = cell_text(cells[cols["corrigendum"]]) if "corrigendum" in cols else ""
        has_corr = bool(corr_text) and corr_text not in {"--", "-", "NA", "N/A"}
        key = tender_id or f"h:{stable_hash(ref_no, org, title, published)}"
        detail_url = urljoin(page_url_, href) if href else None
        title_is_ref = ref_no is not None and normalize_space(title) == normalize_space(ref_no)
        otype = infer_opportunity_type(title, ref_no)
        if self.feed == "gemdata" and otype in (OpportunityType.TENDER, OpportunityType.UNKNOWN):
            otype = OpportunityType.BID
        evidence = (
            f"{self.platform} listing ({self.listing_url()}), Tender ID {tender_id or 'n/a'}, "
            f"captured {fetched:%Y-%m-%d %H:%M} UTC"
        )
        return TenderRecord(
            source_id=self.source_id,
            platform=self.platform,
            source_record_key=key,
            source_tender_id=tender_id,
            ref_no=ref_no,
            title=title,
            organisation=org or None,
            state=infer_state(org, title) if self.feed == "mmpdata" else infer_state(title),
            opportunity_type=otype,
            published_at=published,
            closing_at=closing,
            opening_at=opening,
            official_url=self.listing_url(),
            detail_url=detail_url,
            detail_url_expires=True,
            documents=[DocumentRef(url=None, kind="nit", access="captcha_required")],
            has_corrigendum=has_corr,
            corrigenda=[corr_text] if has_corr else [],
            status=TenderStatus.ACTIVE,
            evidence=evidence,
            fetched_at=fetched,
            raw={"title_cell_is_ref": title_is_ref, "feed": self.feed, "listing_page": page_url_},
        )
