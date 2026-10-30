"""Deterministic, evidence-backed tender field extraction. No LLM — every value traces back
to a labeled line in the source text, or is explicitly absent ("Not found in source" is the
caller's responsibility to render; this module returns None for an absent field).
"""

from __future__ import annotations

from chanakya.webcrawl.tender_extraction import extract_tender_fields

SAMPLE = """
National Highways Authority of India
Tender No: NHAI/2026/TN-045
Organisation: National Highways Authority of India
State: Tamil Nadu
Last Date of Submission: 15/12/2026
Publication Date: 01/11/2026
Pre-Bid Meeting Date: 10/11/2026
Bid Opening Date: 16/12/2026
Estimated Cost: Rs. 25 Crore
EMD Amount: Rs. 10 Lakh
Tender Fee: Rs. 5000
"""


def test_extracts_all_fields_with_excerpts_when_present():
    fields = extract_tender_fields(SAMPLE)
    assert fields["tender_id"].value == "NHAI/2026/TN-045"
    assert fields["tender_id"].excerpt is not None and "Tender No" in fields["tender_id"].excerpt
    assert fields["state"].value == "Tamil Nadu"
    assert fields["submission_deadline"].value == "15/12/2026"
    assert fields["publication_date"].value == "01/11/2026"
    assert fields["pre_bid_meeting_date"].value == "10/11/2026"
    assert fields["opening_date"].value == "16/12/2026"
    assert fields["estimated_value"].value == 25e7
    assert fields["emd"].value == 10e5
    assert fields["tender_fee"].value == 5000.0
    for field in fields.values():
        assert field.method == "rule_based"


def test_missing_fields_are_none_not_fabricated():
    fields = extract_tender_fields("Just some unrelated page text with no tender information.")
    assert fields["tender_id"].value is None
    assert fields["tender_id"].excerpt is None
    assert fields["submission_deadline"].value is None
    assert fields["estimated_value"].value is None


def test_organisation_falls_back_to_labeled_line():
    fields = extract_tender_fields("Department: Public Works Department\n")
    assert fields["organisation"].value == "Public Works Department"
