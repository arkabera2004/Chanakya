"""Shared deterministic extraction helpers, reusable by every acquisition provider.

``extract_by_selectors`` is for providers that return raw HTML (scrapling, crawlee, scrapy,
playwright, browserless, zyte): CSS selectors via BeautifulSoup, already a core project
dependency (every CPPP/GePNIC connector parses HTML with it).

``extract_by_labels`` is for providers that return Markdown (crawl4ai, firecrawl): their
output has no DOM to select against, so fields are read from "label: value" lines (plain,
bold, or a two-column table row) instead. No LLM is involved in either path — see
``chanakya.llm`` if LLM-assisted extraction is ever wanted; it should reuse that module's
budget/provider machinery rather than a provider's own (e.g. Crawl4AI's) LLM strategy.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..connectors.base import parse_inr_amount
from ..models import TenderRecord
from ..timeutil import parse_portal_datetime


def extract_by_selectors(
    html: str,
    selectors: dict[str, str],
    *,
    source_id: str,
    url: str,
    platform: str = "acquisition",
    provider_id: str = "acquisition",
) -> tuple[list[TenderRecord], dict[str, str | None]]:
    """CSS-selector field extraction into Chanakya's canonical TenderRecord.

    Returns ``(records, fields_needing_review)``. A missing ``title`` (the one field this
    helper treats as required) is never guessed — the extracted values are returned for
    manual review instead of a fabricated record, matching the adaptive-selector validation
    rule the Scrapling adapter already followed before this refactor.
    """
    soup = BeautifulSoup(html, "lxml")
    extracted: dict[str, str | None] = {}
    for field_name, css in selectors.items():
        node = soup.select_one(css)
        extracted[field_name] = node.get_text(strip=True) if node is not None else None

    if not extracted.get("title"):
        return [], extracted

    record = TenderRecord(
        source_id=source_id,
        platform=platform,
        source_record_key=extracted.get("tender_id") or url,
        source_tender_id=extracted.get("tender_id"),
        ref_no=extracted.get("ref_no"),
        title=extracted["title"],
        organisation=extracted.get("organisation"),
        detail_url=url,
        evidence=f"{provider_id} selector extraction of {url}",
        raw=extracted,
    )
    return [record], {}


# Matches a "label: value" line in three common Markdown shapes:
#   Title: Supply of Widgets
#   **Title:** Supply of Widgets
#   | Title | Supply of Widgets |
_LABEL_LINE_RE = re.compile(
    r"^\s*\|?\s*\*{0,2}([A-Za-z][A-Za-z0-9 /().'-]{1,60}?)\*{0,2}\s*[:|]\s*\*{0,2}(.+?)\*{0,2}\s*\|?\s*$"
)

# Fields parsed through an existing normalizer instead of kept as raw text.
_DATE_FIELDS = {"published_at", "closing_at", "opening_at"}
_AMOUNT_FIELDS = {"estimated_value_inr", "emd_inr"}


def _markdown_label_values(markdown: str) -> dict[str, str]:
    """One pass over the Markdown collecting every "label: value" line found, keyed by a
    lowercased, whitespace-collapsed label so callers can match against synonyms."""
    values: dict[str, str] = {}
    for line in markdown.splitlines():
        m = _LABEL_LINE_RE.match(line)
        if not m:
            continue
        label = re.sub(r"\s+", " ", m.group(1)).strip().lower()
        value = m.group(2).strip()
        if label and value and label not in values:  # first occurrence wins
            values[label] = value
    return values


def extract_by_labels(
    markdown: str,
    label_synonyms: dict[str, list[str]],
    *,
    source_id: str,
    url: str,
    platform: str = "acquisition",
    provider_id: str = "acquisition",
) -> tuple[list[TenderRecord], dict[str, str | None]]:
    """Deterministic "label: value" field extraction from Markdown into a canonical
    TenderRecord. ``label_synonyms`` maps a TenderRecord field name to the label text(s)
    that identify it in the source's Markdown (case-insensitive), e.g.
    ``{"title": ["title", "work name"], "ref_no": ["tender reference number", "ref no"]}``.

    Returns ``(records, fields_needing_review)``, matching ``extract_by_selectors``: a
    missing ``title`` is never guessed, and unparseable date/amount fields are surfaced for
    review rather than silently dropped or defaulted.
    """
    found = _markdown_label_values(markdown)
    extracted: dict[str, str | None] = {}
    needs_review: dict[str, str | None] = {}

    for field_name, labels in label_synonyms.items():
        raw_value = next((found[label.lower()] for label in labels if label.lower() in found), None)
        if field_name in _DATE_FIELDS:
            parsed = parse_portal_datetime(raw_value) if raw_value else None
            extracted[field_name] = parsed
            if raw_value and parsed is None:
                needs_review[field_name] = raw_value
        elif field_name in _AMOUNT_FIELDS:
            parsed = parse_inr_amount(raw_value) if raw_value else None
            extracted[field_name] = parsed
            if raw_value and parsed is None:
                needs_review[field_name] = raw_value
        else:
            extracted[field_name] = raw_value

    if not extracted.get("title"):
        needs_review["title"] = extracted.get("title")
        return [], needs_review

    record = TenderRecord(
        source_id=source_id,
        platform=platform,
        source_record_key=extracted.get("tender_id") or extracted.get("ref_no") or url,
        source_tender_id=extracted.get("tender_id"),
        ref_no=extracted.get("ref_no"),
        title=extracted["title"],
        organisation=extracted.get("organisation"),
        department=extracted.get("department"),
        location=extracted.get("location"),
        published_at=extracted.get("published_at"),
        closing_at=extracted.get("closing_at"),
        opening_at=extracted.get("opening_at"),
        estimated_value_inr=extracted.get("estimated_value_inr"),
        emd_inr=extracted.get("emd_inr"),
        detail_url=url,
        evidence=f"{provider_id} label extraction of {url}",
        raw={k: v for k, v in found.items()},
    )
    return [record], needs_review
