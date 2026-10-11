"""Stage 4 — deterministic, zero-cost classification.

Scores every record against every capability with weighted phrase matching,
applies global exclusions (non-technology procurement) and decides whether the
record deserves further (still free) semantic scoring or eventual LLM review.

Design rule: weak context words ("monitoring", "camera") can never on their own
push a record past the candidate threshold, and a generic technology signal
keeps a record alive for the semantic stage even with no capability keyword —
precision from rules, recall from the high-recall net.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import FilteringSettings
from ..models import FilterDecision, TenderRecord
from .knowledge import KnowledgeBase
from .textnorm import expand_synonyms, normalize, phrase_regex

W_STRONG, W_MEDIUM, W_WEAK, W_NEG = 0.45, 0.20, 0.06, 0.30
TITLE_BOOST = 1.5


@dataclass
class CapabilityHit:
    capability_id: str
    score: float
    strong: list[str] = field(default_factory=list)
    medium: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    negative: list[str] = field(default_factory=list)

    @property
    def terms(self) -> list[str]:
        return self.strong + self.medium + self.weak


@dataclass
class RuleResult:
    decision: FilterDecision
    best_score: float
    hits: list[CapabilityHit]
    exclusions: list[str]
    tech_signals: list[str]
    reasons: list[str]
    hardware_supply: bool = False

    @property
    def top(self) -> list[CapabilityHit]:
        return [h for h in self.hits if h.score > 0][:3]


def _find(phrases: list[str], text: str) -> list[str]:
    return [p for p in phrases if phrase_regex(p).search(text)]


def _dedupe(tiers: list[list[str]], synonyms: dict[str, str]) -> list[list[str]]:
    """Count each concept once: drop a phrase contained in a longer matched phrase,
    and drop an abbreviation when its expansion also matched (e.g. cctv / closed
    circuit television). Higher tiers win."""
    syn = {normalize(k): normalize(v) for k, v in synonyms.items()}
    seen: list[str] = []
    out: list[list[str]] = []
    for tier in tiers:
        kept = []
        for p in sorted(tier, key=len, reverse=True):
            n = normalize(p)
            if any(n != q and n in q for q in seen):
                continue
            if any(syn.get(n) == q or syn.get(q) == n for q in seen):
                continue
            kept.append(p)
            seen.append(n)
        out.append(kept)
    return out


HARDWARE_LEAD = re.compile(r"^(?:\W*)(?:rate contract for |procurement of |purchase of )?(?:supply|supplying|procurement|purchase)\b")
SOFTWARE_TERMS = ("software", "analytics", "artificial intelligence", "ai based", "ai-based", "platform", "application",
                  "development", "solution", "implementation", "integration", "system integrator", "services")


class RuleClassifier:
    def __init__(self, kb: KnowledgeBase, settings: FilteringSettings) -> None:
        self.kb = kb
        self.settings = settings

    def prepare(self, rec: TenderRecord) -> tuple[str, str]:
        syn = self.kb.global_.synonyms
        title = expand_synonyms(normalize(rec.title), syn)
        body = expand_synonyms(normalize(rec.text_for_matching()), syn)
        return title, body

    def score_capabilities(self, title: str, body: str) -> list[CapabilityHit]:
        hits: list[CapabilityHit] = []
        for cap in self.kb.capabilities:
            kw = cap.keywords
            strong, medium, weak = _dedupe([_find(kw.strong, body), _find(kw.medium, body), _find(kw.weak, body)],
                                           self.kb.global_.synonyms)
            negative = _find(cap.negative, body)

            def w(terms: list[str], base: float) -> float:
                return sum(base * (TITLE_BOOST if phrase_regex(t).search(title) else 1.0) for t in terms)

            raw = w(strong, W_STRONG) + w(medium, W_MEDIUM) + w(weak, W_WEAK) - W_NEG * len(negative)
            # Weak-only matches are capped below the candidate threshold.
            if not strong and not medium:
                raw = min(raw, self.settings.candidate_threshold - 0.01)
            score = max(0.0, min(1.0, raw))
            hits.append(CapabilityHit(cap.id, round(score, 4), strong, medium, weak, negative))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits

    def classify(self, rec: TenderRecord) -> RuleResult:
        title, body = self.prepare(rec)
        hits = self.score_capabilities(title, body)
        hardware = bool(HARDWARE_LEAD.search(title)) and not any(t in body for t in SOFTWARE_TERMS) \
            and not any(h.strong for h in hits if h.score > 0)
        if hardware:
            # Equipment supply without a software/analytics component: never a "strong" fit on its own;
            # it is a partner/SI route at best, so cap it into the triage band.
            for h in hits:
                h.score = min(h.score, self.settings.strong_threshold - 0.05)
            hits.sort(key=lambda h: h.score, reverse=True)
        best = hits[0].score if hits else 0.0
        any_strong = any(h.strong and h.score > 0 for h in hits)
        any_medium = any(h.medium and h.score > 0 for h in hits)
        g = self.kb.global_
        exclusions = _find(g.exclusion_terms, title + " " + normalize(rec.category or ""))
        tech = _find(g.tech_signals, body)
        cat = normalize(rec.category or "")
        high_recall = (
            any(normalize(c) == cat or (len(c) > 3 and normalize(c) in cat) for c in self.settings.high_recall_categories if cat)
            or rec.opportunity_type.value.lower() in self.settings.high_recall_types
        )
        reasons: list[str] = []
        s = self.settings
        if hardware:
            reasons.append("equipment supply without a software/analytics component — partner/SI route at best")
        if exclusions and not any_strong:
            if any_medium:
                decision = FilterDecision.LOW
                reasons.append(f"non-technology terms in title ({', '.join(exclusions[:3])}) but technology terms present — semantic check")
            else:
                decision = FilterDecision.REJECTED
                reasons.append(f"non-technology procurement: {', '.join(exclusions[:3])}")
        elif best >= s.strong_threshold:
            decision = FilterDecision.STRONG
        elif best >= s.candidate_threshold:
            decision = FilterDecision.CANDIDATE
        elif best > 0 or tech or high_recall:
            decision = FilterDecision.LOW
            if tech:
                reasons.append(f"generic technology signals ({', '.join(tech[:3])}) — kept for semantic check")
            elif high_recall:
                reasons.append("technology category / EOI-RFP type — kept for semantic check")
        else:
            decision = FilterDecision.REJECTED
            reasons.append("no capability or technology signal in title/category/description")
        for h in hits[:3]:
            if h.score > 0:
                reasons.append(f"{h.capability_id} {h.score:.2f} via {', '.join((h.strong + h.medium + h.weak)[:4])}"
                               + (f"; negative: {', '.join(h.negative[:2])}" if h.negative else ""))
        return RuleResult(decision, round(best, 4), hits, exclusions, tech, reasons, hardware)
