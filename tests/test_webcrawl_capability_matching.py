"""Wires an extracted WebsiteTender into the existing Wayam capability-matching engine
(pipeline.classify.RuleClassifier) — no duplicate matching engine. Builds a minimal
TenderRecord from the extracted fields/text, exactly as any other source would.
"""

from __future__ import annotations

from chanakya.pipeline.classify import RuleClassifier
from chanakya.pipeline.knowledge import load_knowledge_base
from chanakya.webcrawl.capability_matching import build_tender_record, match_capabilities

KB_PATH = "config/wayam_capabilities.yaml"


def test_build_tender_record_maps_extracted_fields():
    fields = {
        "tender_id": {"value": "NHAI/2026/1", "excerpt": "Tender No: NHAI/2026/1", "method": "rule_based"},
        "organisation": {"value": "NHAI", "excerpt": None, "method": "rule_based"},
        "state": {"value": "Tamil Nadu", "excerpt": "Tamil Nadu", "method": "rule_based"},
        "estimated_value": {"value": 25e7, "excerpt": None, "method": "rule_based"},
        "emd": {"value": 10e5, "excerpt": None, "method": "rule_based"},
    }
    record = build_tender_record("https://example.gov.in/tenders/1", fields, "AI based traffic surveillance system")
    assert record.ref_no == "NHAI/2026/1"
    assert record.organisation == "NHAI"
    assert record.state == "Tamil Nadu"
    assert record.estimated_value_inr == 25e7
    assert record.emd_inr == 10e5
    assert record.official_url == "https://example.gov.in/tenders/1"
    assert record.source_id == "website_intelligence"


def test_match_capabilities_reuses_real_rule_classifier(settings):
    kb = load_knowledge_base(settings.app.capabilities_file)
    record = build_tender_record(
        "https://example.gov.in/tenders/1", {},
        "Supply, installation and commissioning of AI based video analytics and surveillance system",
    )
    result = match_capabilities(record, kb, settings.filtering)
    assert isinstance(result, type(RuleClassifier(kb, settings.filtering).classify(record)))
    assert result.best_score >= 0.0
