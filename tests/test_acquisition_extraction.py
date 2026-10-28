"""Shared selector-based extraction helper used by every HTML-returning acquisition provider."""

from __future__ import annotations

from chanakya.acquisition.extraction import extract_by_labels, extract_by_selectors


def test_extracts_mapped_fields_into_a_tender_record():
    html = '<html><body><h1 class="title">Supply of Widgets</h1><span class="org">Synthetic Dept</span></body></html>'
    records, review = extract_by_selectors(
        html, {"title": "h1.title", "organisation": "span.org"},
        source_id="test", url="https://x/", provider_id="crawlee",
    )
    assert review == {}
    assert len(records) == 1
    assert records[0].title == "Supply of Widgets"
    assert records[0].organisation == "Synthetic Dept"
    assert records[0].source_id == "test"
    assert records[0].evidence == "crawlee selector extraction of https://x/"


def test_missing_title_is_kept_for_review_not_guessed():
    html = '<html><body><span class="org">Synthetic Dept</span></body></html>'
    records, review = extract_by_selectors(
        html, {"title": "h1.title", "organisation": "span.org"}, source_id="test", url="https://x/",
    )
    assert records == []
    assert review == {"title": None, "organisation": "Synthetic Dept"}


def test_source_record_key_falls_back_to_url_without_a_tender_id():
    html = '<html><body><h1 class="title">Supply of Widgets</h1></body></html>'
    records, _ = extract_by_selectors(html, {"title": "h1.title"}, source_id="test", url="https://x/tender/42")
    assert records[0].source_record_key == "https://x/tender/42"


def test_tender_id_selector_becomes_the_record_key_when_present():
    html = '<html><body><h1 class="title">Supply of Widgets</h1><span class="tid">T-42</span></body></html>'
    records, _ = extract_by_selectors(
        html, {"title": "h1.title", "tender_id": "span.tid"}, source_id="test", url="https://x/",
    )
    assert records[0].source_record_key == "T-42"
    assert records[0].source_tender_id == "T-42"


_LABELS = {
    "title": ["title", "work name"],
    "ref_no": ["tender reference number", "ref no"],
    "organisation": ["organisation", "department"],
    "closing_at": ["closing date", "bid submission end date"],
    "estimated_value_inr": ["estimated value", "tender value"],
}


def test_extracts_plain_label_lines_into_a_tender_record():
    markdown = "Title: Supply of Widgets\nRef No: T-42\nOrganisation: Synthetic Dept\n"
    records, review = extract_by_labels(markdown, _LABELS, source_id="test", url="https://x/", provider_id="firecrawl")
    assert review == {}
    assert len(records) == 1
    rec = records[0]
    assert rec.title == "Supply of Widgets" and rec.ref_no == "T-42" and rec.organisation == "Synthetic Dept"
    assert rec.source_record_key == "T-42"
    assert rec.evidence == "firecrawl label extraction of https://x/"


def test_extracts_bold_and_table_row_label_lines():
    markdown = "**Title:** Supply of Widgets\n| Tender Reference Number | T-99 |\n"
    records, _ = extract_by_labels(markdown, _LABELS, source_id="test", url="https://x/")
    assert records[0].title == "Supply of Widgets"
    assert records[0].ref_no == "T-99"


def test_date_and_amount_fields_are_parsed_not_kept_raw():
    markdown = "Title: Supply of Widgets\nClosing Date: 31-Dec-2026\nEstimated Value: Rs. 5 Lakh\n"
    records, review = extract_by_labels(markdown, _LABELS, source_id="test", url="https://x/")
    assert review == {}
    rec = records[0]
    assert rec.closing_at is not None and rec.closing_at.year == 2026
    assert rec.estimated_value_inr == 500000.0


def test_unparseable_date_is_kept_for_review_not_dropped_silently():
    markdown = "Title: Supply of Widgets\nClosing Date: whenever we feel like it\n"
    records, review = extract_by_labels(markdown, _LABELS, source_id="test", url="https://x/")
    assert records[0].closing_at is None
    assert review == {"closing_at": "whenever we feel like it"}


def test_missing_title_is_kept_for_review_not_guessed_in_markdown():
    markdown = "Ref No: T-42\n"
    records, review = extract_by_labels(markdown, _LABELS, source_id="test", url="https://x/")
    assert records == []
    assert review == {"title": None}


def test_source_record_key_falls_back_to_ref_no_then_url():
    markdown = "Title: Supply of Widgets\n"
    records, _ = extract_by_labels(markdown, _LABELS, source_id="test", url="https://x/tender/7")
    assert records[0].source_record_key == "https://x/tender/7"
