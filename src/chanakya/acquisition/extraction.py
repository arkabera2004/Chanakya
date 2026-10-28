"""Shared selector-based HTML -> TenderRecord extraction, reusable by every acquisition
provider that returns raw HTML (currently: scrapling, crawlee, scrapy, playwright,
browserless, zyte). Crawl4AI and Firecrawl return Markdown, not HTML, so this helper does
not apply to them — a provider whose output is already-cleaned text needs a different
(not-yet-built) field-extraction strategy, not CSS selectors.

Uses BeautifulSoup, already a core project dependency (every CPPP/GePNIC connector parses
HTML with it), rather than a provider-specific parser — one extraction implementation for
every HTML-returning provider instead of six near-identical copies.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from ..models import TenderRecord


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
