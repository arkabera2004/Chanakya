"""Deterministic, evidence-backed company-fact extraction — the company-website analogue of
tender_extraction.py. Same discipline: a value traces to an exact excerpt, or is absent.
"""

from __future__ import annotations

from chanakya.webcrawl.company_extraction import extract_company_fields

SAMPLE = """
Acme Robotics Pvt Ltd

About Us
Acme Robotics builds AI-based video analytics and surveillance platforms for smart cities
and industrial safety.

Industries: Smart Cities, Industrial Safety, Transportation
Technology: Computer Vision, Edge AI, IoT Sensors
Certifications: ISO 9001:2015, ISO 27001:2013
Locations: Bengaluru, Pune, Dubai
Contact: info@acmerobotics.example, +91-80-1234-5678
"""


def test_extracts_company_facts_with_excerpts():
    fields = extract_company_fields(SAMPLE, page_title="Acme Robotics Pvt Ltd")
    assert fields["company_name"].value == "Acme Robotics Pvt Ltd"
    assert fields["industries"].value == ["Smart Cities", "Industrial Safety", "Transportation"]
    assert fields["technology_capabilities"].value == ["Computer Vision", "Edge AI", "IoT Sensors"]
    assert fields["certifications"].value == ["ISO 9001:2015", "ISO 27001:2013"]
    assert fields["locations"].value == ["Bengaluru", "Pune", "Dubai"]
    assert fields["contact_email"].value == "info@acmerobotics.example"
    assert fields["contact_phone"].value == "+91-80-1234-5678"
    for field in fields.values():
        if field.value is not None:
            assert field.method == "rule_based"


def test_missing_fields_are_none_not_fabricated():
    fields = extract_company_fields("Just some unrelated page text.", page_title=None)
    assert fields["company_name"].value is None
    assert fields["contact_email"].value is None
    assert fields["industries"].value is None
