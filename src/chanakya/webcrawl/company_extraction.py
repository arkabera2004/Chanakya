"""Deterministic, evidence-backed company-fact extraction — the company-website analogue of
``tender_extraction``. No LLM: list-valued fields (industries, technology, certifications,
locations) come from a single labeled line split on commas; scalar fields come from a labeled
line or, for ``company_name``, the page's own ``<title>``. A field the text doesn't contain
stays ``None`` — never a fabricated placeholder. An absent capability is not evidence that the
company lacks it (brief's explicit instruction): this module only ever asserts presence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\-\s]{7,}\d")

_LIST_LABELS = {
    "industries": re.compile(r"(?im)^.*\bindustries\s*[:\-]\s*(.+)$"),
    "technology_capabilities": re.compile(r"(?im)^.*\btechnology\s*[:\-]\s*(.+)$"),
    "certifications": re.compile(r"(?im)^.*\bcertifications?\s*[:\-]\s*(.+)$"),
    "locations": re.compile(r"(?im)^.*\blocations?\s*[:\-]\s*(.+)$"),
}


@dataclass
class ExtractedField:
    value: object | None
    excerpt: str | None = None
    method: str = "rule_based"


def _list_field(text: str, field: str) -> ExtractedField:
    match = _LIST_LABELS[field].search(text)
    if match is None:
        return ExtractedField(value=None)
    items = [item.strip() for item in match.group(1).split(",") if item.strip()]
    return ExtractedField(value=items or None, excerpt=match.group(0).strip())


def _contact(text: str, pattern: re.Pattern[str]) -> ExtractedField:
    match = pattern.search(text)
    if match is None:
        return ExtractedField(value=None)
    return ExtractedField(value=match.group(0).strip(), excerpt=match.group(0).strip())


def extract_company_fields(text: str, *, page_title: str | None) -> dict[str, ExtractedField]:
    fields: dict[str, ExtractedField] = {name: _list_field(text, name) for name in _LIST_LABELS}
    fields["company_name"] = ExtractedField(value=page_title, excerpt=page_title) if page_title else ExtractedField(value=None)
    fields["contact_email"] = _contact(text, _EMAIL)
    fields["contact_phone"] = _contact(text, _PHONE)
    return fields
