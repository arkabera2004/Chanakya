"""Deterministic, evidence-backed tender field extraction from free-text page/document
content. No LLM: every value comes from a regex match against a labeled line in the source
text, so every field is traceable to an exact excerpt — never fabricated. A field the text
doesn't contain stays ``None``; the caller renders that as "Not found in source", this module
never invents a placeholder value.

Reuses ``chanakya.connectors.base``'s generic (not GePNIC-specific) helpers —
``infer_state``/``infer_opportunity_type``/``parse_inr_amount`` — rather than reimplementing
state/amount/type parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..connectors.base import infer_opportunity_type, infer_state, parse_inr_amount

_DATE = r"\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|[A-Za-z]+ \d{1,2},? \d{4}"

_LABEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "tender_id": re.compile(r"(?im)^.*\b(?:tender\s*(?:id|no\.?|number|reference)|nit\s*no\.?)\s*[:\-]\s*(\S.+?)\s*$"),
    "organisation": re.compile(r"(?im)^.*\b(?:organisation|organization|department|authority)\s*[:\-]\s*(\S.+?)\s*$"),
    "publication_date": re.compile(rf"(?im)^.*\bpublication\s*date\s*[:\-]\s*({_DATE})"),
    "submission_deadline": re.compile(
        rf"(?im)^.*\b(?:last date (?:of|for) submission|closing date|submission deadline|bid submission end date)"
        rf"\s*[:\-]\s*({_DATE})",
    ),
    "pre_bid_meeting_date": re.compile(rf"(?im)^.*\bpre[\-\s]?bid meeting date\s*[:\-]\s*({_DATE})"),
    "opening_date": re.compile(rf"(?im)^.*\b(?:bid opening date|opening date|technical bid opening)\s*[:\-]\s*({_DATE})"),
    "estimated_value": re.compile(r"(?im)^.*\b(?:estimated (?:cost|value)|contract value)\s*[:\-]\s*(\S.+?)\s*$"),
    "emd": re.compile(r"(?im)^.*\b(?:emd|earnest money deposit)\s*(?:amount)?\s*[:\-]\s*(\S.+?)\s*$"),
    "tender_fee": re.compile(r"(?im)^.*\btender fee\s*[:\-]\s*(\S.+?)\s*$"),
}
_AMOUNT_FIELDS = {"estimated_value", "emd", "tender_fee"}


@dataclass
class ExtractedField:
    value: str | float | None
    excerpt: str | None = None
    method: str = "rule_based"


def _extract_labeled(text: str, field: str) -> ExtractedField:
    pattern = _LABEL_PATTERNS[field]
    match = pattern.search(text)
    if match is None:
        return ExtractedField(value=None)
    raw = match.group(1).strip()
    value: str | float | None = parse_inr_amount(raw) if field in _AMOUNT_FIELDS else raw
    return ExtractedField(value=value, excerpt=match.group(0).strip())


def extract_tender_fields(text: str) -> dict[str, ExtractedField]:
    fields = {name: _extract_labeled(text, name) for name in _LABEL_PATTERNS}

    state = infer_state(text)
    fields["state"] = ExtractedField(value=state, excerpt=state) if state else ExtractedField(value=None)

    otype = infer_opportunity_type(text)
    fields["opportunity_type"] = ExtractedField(value=otype.value if otype else None)

    return fields
