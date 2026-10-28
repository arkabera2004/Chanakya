"""Shared selector-based extraction helper used by every HTML-returning acquisition provider."""

from __future__ import annotations

from chanakya.acquisition.extraction import extract_by_selectors


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
