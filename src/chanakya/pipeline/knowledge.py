"""Loads the editable Wayam capability knowledge base (YAML)."""

from __future__ import annotations

import hashlib
import re
from functools import cached_property
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

VALID_STATUS = {"existing_offering", "custom_build", "partner"}
VALID_CONFIDENCE = {"high", "medium", "low"}


class Keywords(BaseModel):
    strong: list[str] = Field(default_factory=list)
    medium: list[str] = Field(default_factory=list)
    weak: list[str] = Field(default_factory=list)


class Capability(BaseModel):
    id: str
    name: str
    offering: str
    offering_status: str
    confidence: str
    description: str
    project_types: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    keywords: Keywords = Field(default_factory=Keywords)
    negative: list[str] = Field(default_factory=list)
    example_requirements: list[str] = Field(default_factory=list)
    matching_indicators: list[str] = Field(default_factory=list)
    exclusion_indicators: list[str] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("offering_status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in VALID_STATUS:
            raise ValueError(f"offering_status must be one of {sorted(VALID_STATUS)}")
        return v

    @field_validator("confidence")
    @classmethod
    def _conf(cls, v: str) -> str:
        if v not in VALID_CONFIDENCE:
            raise ValueError(f"confidence must be one of {sorted(VALID_CONFIDENCE)}")
        return v

    def profile_text(self) -> str:
        kw = self.keywords
        # Strong keywords are included twice to weight them in the lexical profile.
        parts = [self.name, self.description, " ".join(self.project_types), " ".join(self.technologies),
                 " ".join(kw.strong), " ".join(kw.strong), " ".join(kw.medium), " ".join(self.example_requirements)]
        return " . ".join(p for p in parts if p)

    def short_card(self) -> str:
        """Compact description sent to the LLM (kept small on purpose)."""
        return (f"{self.id} | {self.name} | offering: {self.offering} ({self.offering_status}, confidence {self.confidence})"
                f" | {self.description[:260]}")


class GlobalRules(BaseModel):
    exclusion_terms: list[str] = Field(default_factory=list)
    tech_signals: list[str] = Field(default_factory=list)
    synonyms: dict[str, str] = Field(default_factory=dict)
    strategic_opportunity_types: list[str] = Field(default_factory=list)


class KnowledgeBase(BaseModel):
    kb_version: str
    company: str = "Wayam AI"
    verified_on: str | None = None
    company_facts: dict = Field(default_factory=dict)
    global_: GlobalRules = Field(default_factory=GlobalRules, alias="global")
    capabilities: list[Capability]
    content_hash: str = ""

    model_config = {"populate_by_name": True}

    @cached_property
    def by_id(self) -> dict[str, Capability]:
        return {c.id: c for c in self.capabilities}

    @property
    def version(self) -> str:
        """Version key used for cache invalidation: declared version + content hash."""
        return f"{self.kb_version}:{self.content_hash[:8]}"

    def get(self, cap_id: str) -> Capability | None:
        return self.by_id.get(cap_id)


def load_knowledge_base(path: str | Path) -> KnowledgeBase:
    p = Path(path)
    raw = p.read_bytes()
    data = yaml.safe_load(raw.decode("utf-8"))
    kb = KnowledgeBase.model_validate(data)
    kb.content_hash = hashlib.sha256(raw).hexdigest()
    ids = [c.id for c in kb.capabilities]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate capability ids: {sorted(dupes)}")
    for c in kb.capabilities:
        if not re.fullmatch(r"CAP-[A-Z0-9-]+", c.id):
            raise ValueError(f"invalid capability id {c.id!r}")
    return kb
