"""Award-notice monitoring (roadmap Phase 7). No network: httpx.MockTransport only.

The results-table fixture is SYNTHETIC (the real page is a CAPTCHA search form, see
the fixture headers); the CAPTCHA-form fixture is a trimmed real capture.
"""

from __future__ import annotations

import httpx
import pytest
import yaml
from sqlalchemy import select
from typer.testing import CliRunner

from chanakya.awards import (
    award_key, build_report, format_inr, format_report, match_awards, records_from_file, scan_awards, store_awards,
)
from chanakya.cli import app as cli_app
from chanakya.config import AwardsSettings, HttpSettings, load_settings
from chanakya.connectors import StructureChangedError
from chanakya.connectors.cppp_awards import AwardScanContext, CPPPAwardsConnector
from chanakya.db import Award, Opportunity
from chanakya.http import BlockedError, PoliteClient
from chanakya.models import AwardRecord
from chanakya.timeutil import parse_portal_datetime

from .conftest import FIXTURES, ROOT

RESULTS_URL = "https://eprocure.gov.in/cppp/resultoftendersnew/cpppdata"
SYNTHETIC_HTML = (FIXTURES / "html" / "cppp_awards_results.synthetic.html").read_text(encoding="utf-8")
REAL_CAPTCHA_HTML = (FIXTURES / "html" / "cppp_results_captcha_form.real.html").read_text(encoding="utf-8")
ACTIVE_TENDERS_HTML = (FIXTURES / "html" / "cppp_central_p1.cppp.html").read_text(encoding="utf-8")


def connector(client=None, **kw) -> CPPPAwardsConnector:
    return CPPPAwardsConnector(AwardsSettings(**kw), client)


def _client(handler) -> PoliteClient:
    return PoliteClient(HttpSettings(min_interval_s=0, max_retries=1, backoff_base_s=0, respect_robots_txt=True),
                        transport=httpx.MockTransport(handler), sleep=lambda s: None, resolve_dns=False)


def _opp(session, oid: str, title: str, *, tender_id: str | None = None, ref_no: str | None = None,
         organisation: str | None = None, priority: str = "P2") -> Opportunity:
    o = Opportunity(id=oid, dedup_key=f"k:{oid}", source_id="fixture", platform="Fixture portal",
                    source_record_key=oid, source_tender_id=tender_id, ref_no=ref_no, title=title,
                    organisation=organisation, content_hash="x" * 64, priority=priority)
    session.add(o)
    session.commit()
    return o


# ---------------------------------------------------------------- fixtures are labelled
def test_fixture_provenance_is_stated():
    assert "SYNTHETIC fixture. NOT a capture of any real page" in SYNTHETIC_HTML[:400]
    assert "REAL capture, trimmed" in REAL_CAPTCHA_HTML[:300] and "PoliteClient" in REAL_CAPTCHA_HTML[:800]


# ---------------------------------------------------------------- parsing
def test_happy_path_parses_award_rows():
    page = connector().parse_page(SYNTHETIC_HTML, RESULTS_URL)
    assert len(page.records) == 6 and page.malformed == 0
    assert page.total_reported == 12
    assert page.next_url == RESULTS_URL + "?page=2"
    first = page.records[0]
    assert first.tender_id == "2026_SYNSC_000101_1"
    assert first.ref_no == "SYN/SC/VA/01"          # slashes inside the reference survive
    assert first.title.startswith("Implementation of AI-based video analytics")
    assert first.organisation == "Synthetic Smart City Ltd"
    assert first.awarded_to == "Synthetic Vision Systems Pvt Ltd"
    assert first.awarded_value_inr == 42000000.0
    assert first.award_date == parse_portal_datetime("08-Oct-2026") and str(first.award_date.tzinfo) == "Asia/Kolkata"
    assert first.source_url == RESULTS_URL and first.source_id == "cppp_awards"
    assert "Tender ID 2026_SYNSC_000101_1" in first.evidence and RESULTS_URL in first.evidence


def test_unstated_value_and_date_stay_none():
    page = connector().parse_page(SYNTHETIC_HTML, RESULTS_URL)
    by_id = {r.tender_id: r for r in page.records}
    assert by_id["2026_SYNDEP_000311_1"].awarded_value_inr is None      # "NA" is not a value
    assert by_id["2026_SYNCAT_000106_1"].award_date is None             # "--" is not a date
    assert by_id["2026_SYNCAT_000106_1"].awarded_to == "Synthetic Caterers"


def test_column_order_and_missing_award_columns_are_tolerated():
    html = """<table><tr><th>Organisation Name</th><th>Tender ID</th><th>Tender Title</th><th>Award Date</th></tr>
    <tr><td>Synthetic Org</td><td>2026_SYN_1_1</td><td>Synthetic title</td><td>01-Sep-2026</td></tr></table>"""
    page = connector().parse_page(html, RESULTS_URL)
    rec = page.records[0]
    assert (rec.tender_id, rec.title, rec.organisation) == ("2026_SYN_1_1", "Synthetic title", "Synthetic Org")
    assert rec.awarded_to is None and rec.awarded_value_inr is None and rec.award_date is not None
    assert page.next_url is None


def test_empty_page():
    html = "<html><body><form><img alt='Image CAPTCHA'></form><p>No Records Found.</p></body></html>"
    page = connector().parse_page(html, RESULTS_URL)
    assert page.records == [] and page.next_url is None


def test_real_captcha_form_page_raises_blocked():
    """The page as CPPP serves it today: a search form with an image CAPTCHA, no table."""
    with pytest.raises(BlockedError, match="CAPTCHA"):
        connector().parse_page(REAL_CAPTCHA_HTML, RESULTS_URL)


def test_layout_change_raises_structure_error():
    html = "<html><body><div class='cards'>Awards moved to cards</div></body></html>"
    with pytest.raises(StructureChangedError):
        connector().parse_page(html, RESULTS_URL)


def test_active_tender_table_is_not_accepted_as_awards():
    """A table with titles but no award column must not be read as award lines."""
    html = ACTIVE_TENDERS_HTML.replace("captcha", "x").replace("CAPTCHA", "x")
    with pytest.raises(StructureChangedError):
        connector().parse_page(html, RESULTS_URL)


def test_malformed_rows_are_skipped_and_counted():
    bad = ("<tbody><tr><td>0.</td><td>short row</td></tr>"
           "<tr><td>x</td><td>01-Oct-2026</td><td></td><td></td><td>Org</td><td>Bidder</td><td>1</td></tr>"   # no title
           "<tr><td>y</td><td>--</td><td></td><td><a>Title only</a>/R/2026_SYN_9_1</td><td>Org</td><td>--</td><td>NA</td></tr>")
    page = connector().parse_page(SYNTHETIC_HTML.replace("<tbody>", bad, 1), RESULTS_URL)
    assert len(page.records) == 6 and page.malformed == 3


# ---------------------------------------------------------------- fetching
def _paged_handler(calls: list[str]):
    page2 = SYNTHETIC_HTML.replace("2026_SYN", "2025_SYN").replace("Next &raquo;", "Last")

    def handler(req: httpx.Request):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(str(req.url))
        return httpx.Response(200, text=page2 if req.url.params.get("page") == "2" else SYNTHETIC_HTML)
    return handler


def test_pagination_follows_next_link_to_last_page():
    calls: list[str] = []
    with _client(_paged_handler(calls)) as client:
        ctx = AwardScanContext(max_pages=5)
        recs = list(connector(client).discover(ctx))
    assert calls == [RESULTS_URL, RESULTS_URL + "?page=2"]
    assert len(recs) == 12 and ctx.pages_fetched == 2 and ctx.stopped_reason == "last page"
    assert {r.tender_id[:4] for r in recs} == {"2026", "2025"}


def test_pagination_stops_at_max_pages():
    calls: list[str] = []
    with _client(_paged_handler(calls)) as client:
        ctx = AwardScanContext(max_pages=1)
        recs = list(connector(client).discover(ctx))
    assert len(recs) == 6 and len(calls) == 1 and "coverage may be partial" in ctx.stopped_reason


def test_scan_stores_awards_and_is_idempotent(settings, session):
    calls: list[str] = []
    with _client(_paged_handler(calls)) as client:
        first = scan_awards(settings, session, client, max_pages=5)
        second = scan_awards(settings, session, client, max_pages=5)
    assert first.status == "ok" and (first.counts.seen, first.counts.new, first.pages) == (12, 12, 2)
    assert (second.counts.new, second.counts.unchanged) == (0, 12)
    rows = session.scalars(select(Award)).all()
    assert len(rows) == 12
    row = next(r for r in rows if r.tender_id == "2026_SYNSC_000101_1")
    assert row.awarded_to == "Synthetic Vision Systems Pvt Ltd" and row.awarded_value_inr == 42000000.0
    assert row.award_date.tzinfo is None and row.fetched_at is not None      # stored as naive UTC
    assert row.evidence and row.source_url == RESULTS_URL


def test_scan_of_captcha_page_is_blocked_and_stores_nothing(settings, session):
    calls: list[str] = []

    def handler(req: httpx.Request):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(req.method)
        return httpx.Response(200, text=REAL_CAPTCHA_HTML)

    with _client(handler) as client:
        res = scan_awards(settings, session, client)
    assert res.status == "blocked" and "CAPTCHA" in res.error
    assert calls == ["GET"]                                   # one read, no form submission, no retry
    assert session.scalars(select(Award)).all() == []


def test_scan_respects_robots_disallow(settings, session):
    def handler(req: httpx.Request):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /cppp/resultoftendersnew\n",
                                  headers={"content-type": "text/plain"})
        raise AssertionError("a disallowed page must not be requested")

    with _client(handler) as client:
        res = scan_awards(settings, session, client)
    assert res.status == "blocked" and "robots.txt" in res.error


def test_changed_value_updates_the_same_row(settings, session):
    rec = AwardRecord(source_id="cppp_awards", tender_id="2026_SYN_5_1", title="Synthetic", awarded_to="Synthetic  Bidder Ltd",
                      awarded_value_inr=100.0)
    same_bidder = rec.model_copy(update={"awarded_to": "synthetic bidder ltd", "awarded_value_inr": 250.0})
    other_bidder = rec.model_copy(update={"awarded_to": "Synthetic Second Ltd"})
    assert award_key(rec) == award_key(same_bidder) != award_key(other_bidder)
    assert store_awards(session, [rec]).new == 1
    counts = store_awards(session, [same_bidder, other_bidder])
    session.commit()
    assert (counts.updated, counts.new) == (1, 1)
    assert sorted(r.awarded_value_inr for r in session.scalars(select(Award))) == [100.0, 250.0]


# ---------------------------------------------------------------- matching and report
def test_award_matches_existing_notice_by_tender_id_or_reference(settings, session):
    by_tid = _opp(session, "CHK-aaa", "Video analytics for ICCC", tender_id="2026_SYNSC_000101_1", priority="P1")
    by_ref = _opp(session, "CHK-bbb", "Speech analytics for helpline", ref_no="syn/hlt/call/03",
                  organisation="SYNTHETIC HEALTH DEPARTMENT")
    _opp(session, "CHK-ccc", "Same reference, other organisation", ref_no="SYN/PWD/ROAD/02", organisation="Another Org")
    store_awards(session, connector().parse_page(SYNTHETIC_HTML, RESULTS_URL).records)
    # The health award is matched by reference only: drop its tender id.
    health = session.scalar(select(Award).where(Award.tender_id == "2026_SYNHLT_000103_1"))
    health.tender_id = None
    session.commit()
    matches = match_awards(session, list(session.scalars(select(Award))))
    found = {m.opportunity.id: m for m in matches}
    assert set(found) == {by_tid.id, by_ref.id}
    assert found[by_tid.id].basis == "tender id" and found[by_tid.id].award.awarded_to == "Synthetic Vision Systems Pvt Ltd"
    assert found[by_ref.id].basis == "reference"


def test_report_lists_matches_winners_and_value_ranges(settings, session, kb):
    _opp(session, "CHK-aaa", "Video analytics for ICCC", tender_id="2026_SYNSC_000101_1", priority="P1")
    store_awards(session, connector().parse_page(SYNTHETIC_HTML, RESULTS_URL).records)
    session.commit()
    report = build_report(session, kb, settings)
    assert report.total_awards == 6 and len(report.matches) == 1
    # Road work and catering do not match a capability, so their winners are not listed.
    names = [w.name for w in report.winners]
    assert names[0] == "Synthetic Vision Systems Pvt Ltd" and report.winners[0].awards == 2
    assert (report.winners[0].min_value, report.winners[0].max_value) == (18550000.0, 42000000.0)
    assert "Synthetic Roads and Infra Ltd" not in names and "Synthetic Caterers" not in names
    assert report.capability_awards == sum(c.awards for c in report.capabilities) >= 3
    edge = next(w for w in report.winners if w.name == "Synthetic Edge Analytics Pvt Ltd")
    assert edge.min_value is None and edge.with_value == 0
    text = "\n".join(format_report(report))
    assert "CHK-aaa [P1]" in text and "matched by tender id: 2026_SYNSC_000101_1" in text
    assert "awarded to: Synthetic Vision Systems Pvt Ltd | value: Rs. 4.20 crore" in text
    assert f"{format_inr(18550000.0)} to Rs. 4.20 crore" in text and "Not stated" in text
    assert chr(0x2013) not in text and chr(0x2014) not in text      # no en or em dashes in the copy


def test_empty_report_and_money_format(settings, session, kb):
    text = "\n".join(format_report(build_report(session, kb, settings)))
    assert "Awards stored: 0" in text and text.count("none") == 2
    assert format_inr(None) == "Not stated" and format_inr(95000.0) == "Rs. 95,000"
    assert format_inr(9500000.0) == "Rs. 95.00 lakh"


# ---------------------------------------------------------------- manual import and CLI
def test_import_reads_saved_page_and_csv(settings, tmp_path):
    saved = tmp_path / "saved_results.html"
    saved.write_text(SYNTHETIC_HTML, encoding="utf-8")
    records, malformed = records_from_file(saved, settings)
    assert len(records) == 6 and malformed == 0 and "saved page saved_results.html" in records[0].evidence
    csv_path = tmp_path / "awards.csv"
    csv_path.write_text("Tender ID,Title,Organisation,Awarded To,Contract Value,AOC Date\n"
                        "2026_SYN_7_1,Synthetic chatbot platform,Synthetic Org,Synthetic Bots Ltd,Rs. 5 Lakh,03-Oct-2026\n"
                        ",,Synthetic Org,Nobody,,\n", encoding="utf-8")
    records, malformed = records_from_file(csv_path, settings)
    assert malformed == 1 and records[0].awarded_value_inr == 500000.0 and records[0].award_date is not None
    with pytest.raises(BlockedError):
        blocked = tmp_path / "landing.html"
        blocked.write_text(REAL_CAPTCHA_HTML, encoding="utf-8")
        records_from_file(blocked, settings)
    with pytest.raises(ValueError):
        records_from_file(tmp_path / "awards.pdf", settings)


def test_cli_scan_is_off_by_default_and_import_then_report(settings, tmp_path):
    cfg = tmp_path / "config" / "chanakya.yaml"
    cfg.parent.mkdir()
    data = settings.model_dump(mode="json", exclude={"config_path"})
    data["app"]["capabilities_file"] = str(settings.app.capabilities_file)
    cfg.write_text(yaml.safe_dump(data), encoding="utf-8")
    saved = tmp_path / "saved_results.html"
    saved.write_text(SYNTHETIC_HTML, encoding="utf-8")
    runner = CliRunner()
    off = runner.invoke(cli_app, ["--config", str(cfg), "awards", "scan"])
    assert off.exit_code == 1 and "awards.enabled: false" in off.output      # no client built, no request made
    imported = runner.invoke(cli_app, ["--config", str(cfg), "awards", "import", str(saved)])
    assert imported.exit_code == 0 and "new=6" in imported.output, imported.output
    again = runner.invoke(cli_app, ["--config", str(cfg), "awards", "import", str(saved)])
    assert "new=0" in again.output and "unchanged=6" in again.output
    rep = runner.invoke(cli_app, ["--config", str(cfg), "awards", "report"])
    assert rep.exit_code == 0 and "Awards stored: 6" in rep.output and "Synthetic Vision Systems Pvt Ltd" in rep.output


@pytest.mark.parametrize("name", ["chanakya.example.yaml", "chanakya.yaml"])
def test_shipped_configs_keep_awards_disabled_and_candidate(name):
    path = ROOT / "config" / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    st = load_settings(path)
    assert st.awards.enabled is False and st.awards.verification_status == "candidate"
    assert st.awards.url == RESULTS_URL
    assert "eprocure.gov.in" in st.http.allowed_domains      # no new host needed
