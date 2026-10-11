from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from chanakya.config import HttpSettings, SourceConfig
from chanakya.connectors import CPPPConnector, DiscoveryContext, GePNICConnector, StructureChangedError
from chanakya.connectors.cppp import decode_page_number, page_url
from chanakya.connectors.manual import ManualImportConnector
from chanakya.http import BlockedError, FetchError, PoliteClient
from chanakya.models import OpportunityType
from chanakya.timeutil import parse_portal_datetime

from .conftest import FIXTURES, ist, write_records

CPPP_HTML = (FIXTURES / "html" / "cppp_central_p1.cppp.html").read_text(encoding="utf-8")


def cppp(feed="cpppdata", client=None):
    return CPPPConnector(SourceConfig(id="cppp_central", type="cppp", feed=feed), client)


# ---------------------------------------------------------------- CPPP parsing
def test_cppp_parses_observed_listing():
    page = cppp().parse_listing(CPPP_HTML, "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata")
    assert len(page.records) == 10
    assert page.total_reported == 29625
    assert decode_page_number(page.next_url) == 2
    bpcl = next(r for r in page.records if r.source_tender_id == "2026_BPCL_26936")
    assert bpcl.title.startswith("Manufacturing, packaging and supplying MAK REDIKOOL PRO")
    assert bpcl.ref_no == "1000466502"
    assert bpcl.organisation == "Bharat Petroleum Corporation Limited"
    assert bpcl.published_at == parse_portal_datetime("09-Oct-2026 09:07 PM")
    assert bpcl.closing_at.hour == 18 and str(bpcl.closing_at.tzinfo) == "Asia/Kolkata"
    assert bpcl.detail_url_expires is True and bpcl.official_url.endswith("/cpppdata")
    assert bpcl.documents[0].access == "captcha_required"
    pg = next(r for r in page.records if r.source_tender_id == "28776")
    assert pg.ref_no == "CC/T/W-TW/DOM/A01/26/13885"  # slashes inside reference numbers are preserved
    cpwd = page.records[0]
    assert cpwd.source_tender_id == "171861" and cpwd.raw["title_cell_is_ref"] is True
    assert all(not r.has_corrigendum for r in page.records)


def test_cppp_page_url_roundtrip():
    assert decode_page_number(page_url("cpppdata", 7)) == 7


def test_cppp_gem_feed_marks_bids():
    page = cppp("gemdata").parse_listing(CPPP_HTML, "https://eprocure.gov.in/cppp/latestactivetendersnew/gemdata")
    assert all(r.opportunity_type in (OpportunityType.BID, OpportunityType.CORRIGENDUM) for r in page.records)


def test_cppp_captcha_only_page_is_blocked():
    html = "<html><body><form><img alt='Image CAPTCHA'><p>What code is in the image?</p></form></body></html>"
    with pytest.raises(BlockedError):
        cppp().parse_listing(html, "https://eprocure.gov.in/cppp/x")


def test_cppp_changed_layout_raises():
    html = "<html><body><div class='new-design'>Tenders moved to cards</div></body></html>"
    with pytest.raises(StructureChangedError):
        cppp().parse_listing(html, "https://eprocure.gov.in/cppp/x")


def test_cppp_empty_results():
    page = cppp().parse_listing("<html><body><p>No Tenders found.</p></body></html>", "https://eprocure.gov.in/cppp/x")
    assert page.records == []


def test_cppp_skips_malformed_rows():
    html = CPPP_HTML.replace("<td>1.</td>", "<td>1.</td>", 1).replace(
        "<tbody>", "<tbody><tr><td>0.</td><td>bad</td></tr><tr><td>x</td><td>09-Oct-2026</td><td></td><td></td><td></td><td>Org</td><td>--</td></tr>")
    page = cppp().parse_listing(html, "https://eprocure.gov.in/cppp/x")
    assert len(page.records) == 10


def _client(handler) -> PoliteClient:
    return PoliteClient(HttpSettings(min_interval_s=0, max_retries=2, backoff_base_s=0, respect_robots_txt=True),
                        transport=httpx.MockTransport(handler), sleep=lambda s: None, resolve_dns=False)


def test_cppp_discover_follows_pagination_and_stops_at_high_water_mark():
    page2 = CPPP_HTML.replace("09-Oct-2026", "01-Oct-2026").replace("Next »", "Previous")

    def handler(req: httpx.Request):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if "url=" in str(req.url):
            return httpx.Response(200, text=page2)
        return httpx.Response(200, text=CPPP_HTML)

    with _client(handler) as client:
        ctx = DiscoveryContext(since=parse_portal_datetime("05-Oct-2026"), max_pages=5)
        recs = list(cppp(client=client).discover(ctx))
    assert len(recs) == 10 and ctx.pages_fetched == 2 and ctx.stopped_reason == "reached high-water mark"


def test_cppp_discover_keeps_resume_url_when_page_cap_hits():
    def handler(req: httpx.Request):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=CPPP_HTML)

    with _client(handler) as client:
        ctx = DiscoveryContext(max_pages=1)
        recs = list(cppp(client=client).discover(ctx))
    assert len(recs) == 10
    assert ctx.resume_url
    assert ctx.stopped_reason and "coverage may be partial" in ctx.stopped_reason


def test_http_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "1"}) if calls["n"] == 1 else httpx.Response(200, text=CPPP_HTML)

    with _client(handler) as client:
        page = cppp(client=client).fetch_listing(None, 1)
    assert len(page.records) == 10 and calls["n"] == 2 and client.stats.retries == 1


def test_http_robots_disallow_is_respected():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /", headers={"content-type": "text/plain"})
        raise AssertionError("must not fetch a disallowed page")

    with _client(handler) as client, pytest.raises(BlockedError, match="robots"):
        cppp(client=client).fetch_listing(None, 1)


def test_http_403_and_5xx():
    with _client(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(403)) as c:
        with pytest.raises(BlockedError):
            c.get_text("https://eprocure.gov.in/cppp/x")
    with _client(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(503)) as c:
        with pytest.raises(FetchError):
            c.get_text("https://eprocure.gov.in/cppp/x")


def test_http_redirect_off_allowlist_blocked():
    from chanakya.security import SecurityError

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})

    with _client(handler) as c, pytest.raises(SecurityError):
        c.get_text("https://eprocure.gov.in/cppp/x")


# ---------------------------------------------------------------- GePNIC
def gep():
    return GePNICConnector(SourceConfig(id="gep", type="gepnic", base_url="https://eprocure.gov.in/eprocure/app", fetch_detail=True))


def test_gepnic_active_listing():
    html = (FIXTURES / "html" / "gepnic_active.gepnic.html").read_text(encoding="utf-8")
    page = gep().parse_listing(html, "https://eprocure.gov.in/eprocure/app?page=FrontEndLatestActiveTenders&service=page")
    assert [r.source_tender_id for r in page.records] == ["2026_SYNRLY_900001_1", "2026_SYNCIV_900002_1", "2026_SYNIT_900003_2"]
    r = page.records[0]
    assert r.title == "Implementation of AI based video analytics for railway station CCTV network"
    assert r.ref_no == "SYN/RLY/VA/2026/01"
    assert r.organisation == "Synthetic Railway Zone" and "Signal and Telecom" in r.department
    assert page.next_url and "linkFwd" in page.next_url


def test_gepnic_headerless_home_widget():
    html = """<html><body>
    <table><tr class="odd"><td><a href="/app?component=DirectLink&amp;page=Home">8. E tender for Baggage Wrapping Contract</a></td>
    <td>AAI/CBE/R-5</td><td>31-Oct-2026 04:00 PM</td><td>02-Nov-2026 11:30 AM</td></tr>
    <tr><td>Latest Tenders updates every 15 mins.</td><td><a href="/search">More...</a></td></tr></table>
    </body></html>"""
    page = gep().parse_listing(html, "https://etenders.gov.in/eprocure/app")
    assert len(page.records) == 1
    assert page.records[0].title.startswith("E tender for Baggage")
    assert page.records[0].ref_no == "AAI/CBE/R-5"
    assert page.next_url is None


def test_gem_listing_reads_bid_table_and_walks_backward():
    html = """<table>
    <tr><th>Sl.No</th><th>Bid Start Date</th><th>Bid End Date</th><th>Bid Number/Total Quantity</th>
    <th>Product Category</th><th>Organisation Name</th><th>Department Name</th></tr>
    <tr><td>1.</td><td>09-Oct-2026 11:56 PM</td><td>19-Oct-2026 09:00 AM</td><td>GEM/2026/B/8131353 /655</td>
    <td>Desktop Computers</td><td>Synthetic Health Department</td><td>Procurement</td></tr>
    </table>
    <a href="?page=3628">« Previous</a>"""
    page = cppp(feed="gemdata").parse_listing(html, "https://eprocure.gov.in/cppp/latestactivetendersnew/gemdata")
    assert len(page.records) == 1
    rec = page.records[0]
    assert rec.source_tender_id == "GEM/2026/B/8131353"
    assert rec.title == "Desktop Computers"
    assert rec.opportunity_type.value == "Bid"
    assert page.next_url and "page=3628" in page.next_url


def test_gepnic_home_variant_from_observed_rows():
    html = (FIXTURES / "html" / "gepnic_home.gepnic.html").read_text(encoding="utf-8")
    page = gep().parse_listing(html, "https://eprocure.gov.in/eprocure/app")
    assert len(page.records) == 4
    assert page.records[1].title == "Repair and maintenance of Gym Hall at SHQ BSF Bikaner"
    assert page.records[0].ref_no == "28/EE/EMD-6/DDA/2026-27"
    assert page.records[0].closing_at == parse_portal_datetime("16-Oct-2026 03:00 PM")


def test_gepnic_detail_enrichment():
    listing = gep().parse_listing((FIXTURES / "html" / "gepnic_active.gepnic.html").read_text(encoding="utf-8"), "https://eprocure.gov.in/eprocure/app")
    rec = gep().apply_detail(listing.records[0], (FIXTURES / "html" / "gepnic_detail.html").read_text(encoding="utf-8"))
    assert rec.estimated_value_inr == 42500000 and rec.emd_inr == 850000
    assert rec.category == "Information Technology (IT)" and rec.tender_category == "Services"
    assert "abandoned object" in rec.description and rec.location.startswith("Lucknow")


# ---------------------------------------------------------------- manual import
def test_manual_import_yaml_csv_and_malformed(tmp_path):
    d = tmp_path / "inbox"
    write_records(d, [{"title": "AI chatbot for citizen services", "organisation": "Synthetic Dept", "closing_at": ist(10),
                       "estimated_value_inr": "2.5 crore", "document_paths": "docs/rfp.pdf"},
                      {"organisation": "missing title"}])
    (d / "extra.csv").write_text("title,organisation,closing_at,tender_id\nVAPT of portals,Synthetic IT,%s,GEM/2026/B/1\n" % ist(5))
    conn = ManualImportConnector(SourceConfig(id="manual", type="manual", path=d))
    ctx = DiscoveryContext()
    recs = list(conn.discover(ctx))
    assert len(recs) == 2 and ctx.malformed == 1
    yaml_rec = next(r for r in recs if r.title.startswith("AI chatbot"))
    assert yaml_rec.estimated_value_inr == 25_000_000
    assert yaml_rec.documents[0].access == "manual" and yaml_rec.documents[0].local_path.endswith("rfp.pdf")
    csv_rec = next(r for r in recs if r.source_tender_id == "GEM/2026/B/1")
    assert csv_rec.opportunity_type == OpportunityType.BID


def test_manual_import_xlsx(tmp_path):
    from openpyxl import Workbook
    d = tmp_path / "inbox"
    d.mkdir()
    wb = Workbook()
    ws = wb.active
    ws.append(["title", "organisation", "closing_at", "tender_id"])
    ws.append(["Drone thermography of solar plants", "Synthetic Gen Co", ist(12), "NREL-SYN-1"])
    wb.save(d / "gem_export.xlsx")
    recs = list(ManualImportConnector(SourceConfig(id="m", type="manual", path=d)).discover(DiscoveryContext()))
    assert recs[0].title.startswith("Drone thermography") and recs[0].closing_at is not None


def test_relative_dates_helper():
    assert parse_portal_datetime(ist(1)) - parse_portal_datetime(ist(0)) >= timedelta(hours=23)
