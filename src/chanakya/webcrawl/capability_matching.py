"""Matches an extracted WebsiteTender against the real Wayam capability book, by building a
``TenderRecord`` from its fields and running it through the existing
``pipeline.classify.RuleClassifier`` — the same engine every other source uses. No second
matching engine.
"""

from __future__ import annotations

from ..config import FilteringSettings
from ..models import TenderRecord
from ..pipeline.classify import RuleClassifier, RuleResult
from ..pipeline.knowledge import KnowledgeBase

SOURCE_ID = "website_intelligence"


def _field_value(fields: dict, name: str) -> object | None:
    entry = fields.get(name)
    return entry.get("value") if entry else None


def build_tender_record(source_url: str, fields: dict, text: str) -> TenderRecord:
    title = _field_value(fields, "tender_id") or (text.strip().splitlines()[0][:200] if text.strip() else source_url)
    return TenderRecord(
        source_id=SOURCE_ID, platform="website_intelligence", source_record_key=source_url,
        ref_no=_field_value(fields, "tender_id"), title=title,
        organisation=_field_value(fields, "organisation"), state=_field_value(fields, "state"),
        estimated_value_inr=_field_value(fields, "estimated_value"), emd_inr=_field_value(fields, "emd"),
        description=text[:2000] if text else None, official_url=source_url, detail_url=source_url,
    )


def match_capabilities(record: TenderRecord, kb: KnowledgeBase, settings: FilteringSettings) -> RuleResult:
    return RuleClassifier(kb, settings).classify(record)
