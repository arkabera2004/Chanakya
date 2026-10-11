"""NIC GePNIC eProcurement connector (eprocure.gov.in/eprocure/app,
etenders.gov.in/eprocure/app and the ~30 state portals running the same
software, e.g. mahatenders, tntenders, eproc.punjab).

The public list is the home-page "Latest Tenders" widget (about ten rows, no
header row on the live portal). ``FrontEndLatestActiveTenders`` and the search
form ask for a CAPTCHA; Chanakya does not submit them. Links are Tapestry
``DirectLink`` URLs bound to a server session (``session=T``) — they are not
permanent, so the stable reference is the portal plus the tender reference.
Document downloads also require a CAPTCHA and are not fetched.

Structure assumptions that could not be fetched from this environment are
isolated in the parsing functions below and covered by fixture tests; run
``chanakya sources test --source <id>`` to validate against the live portal.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import DocumentRef, ListingPage, TenderRecord, TenderStatus, normalize_space, stable_hash
from ..timeutil import now_utc, parse_portal_datetime
from .base import (
    GEPNIC_TENDER_ID, StructureChangedError, TenderSourceConnector, cell_text, find_data_table, has_captcha,
    infer_opportunity_type, infer_state, parse_inr_amount,
)

ACTIVE_PAGE = "?page=FrontEndLatestActiveTenders&service=page"

DETAIL_LABELS = {
    "organisation chain": "org_chain",
    "tender reference number": "ref_no",
    "tender id": "tender_id",
    "tender type": "tender_type",
    "tender category": "tender_category",
    "form of contract": "form_of_contract",
    "title": "title",
    "work description": "description",
    "tender value in ₹": "value",
    "tender value in rs.": "value",
    "tender value": "value",
    "emd amount in ₹": "emd",
    "emd amount in rs.": "emd",
    "emd amount": "emd",
    "product category": "category",
    "location": "location",
    "pincode": "pincode",
    "published date": "published",
    "bid submission end date": "closing",
    "bid opening date": "opening",
    "nda/pre qualification": "prequalification",
    "independent external monitor/remarks": "remarks",
}


def parse_title_cell(cell) -> tuple[str, str | None, str | None, str | None]:
    """GePNIC title cells look like ``[Title] [Ref.No.][Tender ID]`` with the
    title wrapped in a link. Returns (title, ref_no, tender_id, href)."""
    anchor = cell.find("a")
    href = anchor.get("href") if anchor else None
    full = normalize_space(cell.get_text(" ", strip=True))
    groups = [normalize_space(g) for g in re.findall(r"\[([^\[\]]*)\]", full)]
    title = ref_no = tender_id = None
    if groups:
        if GEPNIC_TENDER_ID.match(groups[-1]):
            tender_id = groups[-1]
            groups = groups[:-1]
        if groups:
            title = groups[0]
        if len(groups) >= 2:
            ref_no = groups[1]
    if anchor is not None:
        anchor_text = normalize_space(anchor.get_text(" ", strip=True)).strip("[]").strip()
        if anchor_text and not GEPNIC_TENDER_ID.match(anchor_text):
            title = anchor_text if not title or len(anchor_text) > len(title) else title
    if not title:
        title = re.sub(r"\[[^\]]*\]", "", full).strip() or full
    title = re.sub(r"^\d+\.\s*", "", title)  # home-page rows are numbered "1. Title"
    return title, ref_no, tender_id, href


class GePNICConnector(TenderSourceConnector):
    supports_detail = True
    supports_documents = False

    @property
    def base(self) -> str:
        if not self.config.base_url:
            raise ValueError(f"source {self.source_id}: base_url is required for gepnic")
        return self.config.base_url.rstrip("/")

    @property
    def platform(self) -> str:
        return self.config.platform_label or f"GePNIC ({self.base.split('/')[2]})"

    def listing_url(self) -> str:
        # Home page widget only. The active-tenders page is a CAPTCHA search.
        return self.base

    def fetch_listing(self, url: str | None, page_no: int) -> ListingPage:
        if self.client is None:
            raise RuntimeError("GePNIC connector needs an HTTP client")
        final_url, html = self.client.get_text(url or self.listing_url())
        return self.parse_listing(html, final_url, page_no)

    def parse_listing(self, html: str, page_url_: str, page_no: int = 1) -> ListingPage:
        soup = BeautifulSoup(html, "lxml")
        found = find_data_table(soup, ("published", "title_ref"))
        home_mode = False
        if found is None:
            # Home page variant: Tender Title | Reference No | Closing Date | Bid Opening Date
            found = find_data_table(soup, ("title_ref", "closing"))
            home_mode = found is not None
        if found is None:
            headerless = self._headerless_home_records(soup, page_url_)
            if headerless:
                return ListingPage(records=headerless, page_no=page_no)
            text = soup.get_text(" ", strip=True).lower()
            if "no tenders found" in text or "no records found" in text:
                return ListingPage(records=[], page_no=page_no)
            if has_captcha(soup):
                from ..http import BlockedError
                raise BlockedError("GePNIC page requires CAPTCHA; not bypassing")
            raise StructureChangedError("GePNIC tender table not found")
        table, cols, header_idx = found
        fetched = now_utc()
        records: list[TenderRecord] = []
        for row in table.find_all("tr")[header_idx + 1:]:
            cells = row.find_all("td")
            if not cells or len(cells) <= max(cols.values()):
                continue
            title, ref_no, tender_id, href = parse_title_cell(cells[cols["title_ref"]])
            if not title:
                continue
            if home_mode and "reference" in cols:
                ref_no = cell_text(cells[cols["reference"]]) or ref_no
            org_chain = cell_text(cells[cols["organisation"]]) if "organisation" in cols else ""
            org_parts = [p.strip() for p in org_chain.split("||") if p.strip()]
            published = parse_portal_datetime(cell_text(cells[cols["published"]])) if "published" in cols else None
            closing = parse_portal_datetime(cell_text(cells[cols["closing"]])) if "closing" in cols else None
            opening = parse_portal_datetime(cell_text(cells[cols["opening"]])) if "opening" in cols else None
            key = tender_id or f"h:{stable_hash(ref_no, org_chain, title, closing)}"
            records.append(TenderRecord(
                source_id=self.source_id,
                platform=self.platform,
                source_record_key=key,
                source_tender_id=tender_id,
                ref_no=ref_no,
                title=title,
                organisation=org_parts[0] if org_parts else None,
                department=" > ".join(org_parts[1:]) or None,
                state=infer_state(org_chain, title),
                opportunity_type=infer_opportunity_type(title, ref_no),
                published_at=published,
                closing_at=closing,
                opening_at=opening,
                official_url=self.listing_url(),
                detail_url=urljoin(page_url_, href) if href else None,
                detail_url_expires=True,
                documents=[DocumentRef(kind="nit", access="captcha_required")],
                status=TenderStatus.ACTIVE,
                evidence=f"{self.platform} active tenders ({self.listing_url()}), Tender ID {tender_id or 'n/a'}, "
                         f"Ref {ref_no or 'n/a'}, captured {fetched:%Y-%m-%d %H:%M} UTC",
                fetched_at=fetched,
                raw={"org_chain": org_chain, "home_mode": home_mode},
            ))
        next_url = None
        for a in soup.find_all("a"):
            label = normalize_space(a.get_text(" ", strip=True)).lower()
            if a.get("id") in ("linkFwd", "loadNext") or label in ("next", "next >", ">", "next»", "next »"):
                if a.get("href"):
                    next_url = urljoin(page_url_, a["href"])
                    break
        return ListingPage(records=records, next_url=next_url, page_no=page_no)

    def _headerless_home_records(self, soup: BeautifulSoup, page_url_: str) -> list[TenderRecord]:
        """Live home pages omit the header row. Each tender row is title, reference, closing, opening."""
        fetched = now_utc()
        records: list[TenderRecord] = []
        seen: set[str] = set()
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td", recursive=False)
                if len(cells) < 3 or cells[0].find("a") is None:
                    continue
                title, ref_no, tender_id, href = parse_title_cell(cells[0])
                if not title or len(title) < 8:
                    continue
                closing = parse_portal_datetime(cell_text(cells[2]))
                if closing is None:
                    continue
                opening = parse_portal_datetime(cell_text(cells[3])) if len(cells) > 3 else None
                ref = cell_text(cells[1]) or ref_no
                key = tender_id or f"h:{stable_hash(ref, title, closing)}"
                if key in seen:
                    continue
                seen.add(key)
                records.append(TenderRecord(
                    source_id=self.source_id,
                    platform=self.platform,
                    source_record_key=key,
                    source_tender_id=tender_id,
                    ref_no=ref,
                    title=title,
                    opportunity_type=infer_opportunity_type(title, ref),
                    closing_at=closing,
                    opening_at=opening,
                    official_url=self.listing_url(),
                    detail_url=urljoin(page_url_, href) if href else None,
                    detail_url_expires=True,
                    documents=[DocumentRef(kind="nit", access="captcha_required")],
                    status=TenderStatus.ACTIVE,
                    evidence=f"{self.platform} home-page latest tenders ({self.listing_url()}), "
                             f"Ref {ref or 'n/a'}, captured {fetched:%Y-%m-%d %H:%M} UTC",
                    fetched_at=fetched,
                    raw={"home_mode": True, "headerless": True},
                ))
        return records

    # ------------------------------------------------------------- detail
    def fetch_detail(self, record: TenderRecord) -> TenderRecord:
        if not (self.config.fetch_detail and record.detail_url and self.client):
            return record
        _, html = self.client.get_text(record.detail_url)
        return self.apply_detail(record, html)

    @staticmethod
    def parse_detail(html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "lxml")
        if "captcha" in soup.get_text(" ", strip=True).lower() and not soup.find(string=re.compile("Tender ID", re.I)):
            from ..http import BlockedError
            raise BlockedError("detail page requires CAPTCHA")
        fields: dict[str, str] = {}
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            for i in range(0, len(cells) - 1, 2):
                label = normalize_space(cells[i].get_text(" ", strip=True)).lower().rstrip(":").strip()
                value = normalize_space(cells[i + 1].get_text(" ", strip=True))
                key = DETAIL_LABELS.get(label)
                if key and value and key not in fields:
                    fields[key] = value
        return fields

    def apply_detail(self, record: TenderRecord, html: str) -> TenderRecord:
        f = self.parse_detail(html)
        data = record.model_dump()
        if f.get("description"):
            data["description"] = f["description"]
        if f.get("category"):
            data["category"] = f["category"]
        if f.get("tender_category"):
            data["tender_category"] = f["tender_category"]
        if f.get("location"):
            data["location"] = f["location"] + (f" ({f['pincode']})" if f.get("pincode") else "")
        if v := parse_inr_amount(f.get("value")):
            data["estimated_value_inr"] = v
        if v := parse_inr_amount(f.get("emd")):
            data["emd_inr"] = v
        if f.get("tender_id") and not record.source_tender_id:
            data["source_tender_id"] = f["tender_id"]
        if f.get("closing"):
            data["closing_at"] = parse_portal_datetime(f["closing"]) or record.closing_at
        data["opportunity_type"] = infer_opportunity_type(record.title, f.get("tender_type"), f.get("form_of_contract"))
        data["raw"] = {**record.raw, "detail": f}
        data["state"] = record.state or infer_state(f.get("location"), f.get("org_chain"))
        return TenderRecord.model_validate(data)
