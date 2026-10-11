"""Stage 8 — deterministic, explainable 0 to 100 scoring.

No LLM is used to compute, weight or sort scores. Each component is a 0 to 1
value with a written reason; the total is the weighted sum. Relevance, eligibility
certainty and data completeness are reported separately so a highly relevant
tender with unverified eligibility is flagged rather than presented as qualified.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ScoringSettings
from ..models import EligibilityStatus, FilterDecision, Priority, TenderStatus
from ..timeutil import days_remaining
from .knowledge import KnowledgeBase
from .textnorm import normalize

OFFERING_MULTIPLIER = {"existing_offering": 1.0, "custom_build": 0.8, "partner": 0.65}
ELIGIBILITY_SCORE = {
    EligibilityStatus.VERIFIED_ELIGIBLE: (1.0, "eligibility verified against company profile"),
    EligibilityStatus.POTENTIALLY_ELIGIBLE: (0.7, "some criteria met, others unverified"),
    EligibilityStatus.PARTNER_REQUIRED: (0.45, "partner/OEM/consortium needed"),
    EligibilityStatus.UNKNOWN: (0.5, "eligibility not verifiable from available data"),
    EligibilityStatus.LIKELY_INELIGIBLE: (0.1, "a stated criterion is not met by the configured profile"),
}
FIT_SCORE = {"existing_offering": 1.0, "custom_build": 0.65, "partner_needed": 0.45, "not_fit": 0.1}
CLOSED_STATUSES = {TenderStatus.EXPIRED.value, TenderStatus.CLOSED.value, TenderStatus.CANCELLED.value,
                   TenderStatus.WITHDRAWN.value, TenderStatus.AWARDED.value, TenderStatus.RETENDERED.value}


@dataclass
class ScoreInput:
    filter_decision: FilterDecision
    best_capability: str | None
    capability_score: float          # combined rule+semantic for the best capability (0 to 1)
    eligibility: EligibilityStatus
    status: str
    closing_at_utc: object | None
    estimated_value_inr: float | None
    organisation: str | None
    opportunity_type: str
    triage_relevant: str | None = None   # yes | no | unclear | None
    deep_fit: str | None = None
    deep_hardware: bool | None = None
    analysis_state: str = "not_needed"
    completeness: float = 0.0
    hardware_supply: bool = False
    buyer_wins: int = 0              # other notices from this buyer recorded as Won (used only when enabled)


@dataclass
class ScoreResult:
    total: float
    priority: Priority
    components: dict[str, dict] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def breakdown(self) -> dict:
        return {"total": self.total, "priority": self.priority.value, "components": self.components, "flags": self.flags}


def completeness(opp) -> float:
    checks = [
        bool(opp.source_tender_id or opp.ref_no), bool(opp.organisation), bool(opp.published_at), bool(opp.closing_at),
        opp.estimated_value_inr is not None, opp.emd_inr is not None, bool(opp.description),
        bool(opp.category), bool(opp.intelligence.get("documents_parsed")) if opp.intelligence else False,
    ]
    return round(sum(checks) / len(checks), 2)


class Scorer:
    def __init__(self, settings: ScoringSettings, kb: KnowledgeBase) -> None:
        self.settings, self.kb = settings, kb

    def score(self, s: ScoreInput) -> ScoreResult:
        comps: dict[str, dict] = {}
        flags: list[str] = []
        cap = self.kb.get(s.best_capability) if s.best_capability else None

        # capability match
        c = min(1.0, s.capability_score / 0.6) if s.capability_score else 0.0
        reason = f"best match {s.best_capability} ({s.capability_score:.2f})" if cap else "no capability match"
        if cap:
            mult = OFFERING_MULTIPLIER.get(cap.offering_status, 0.8)
            c *= mult
            if mult < 1:
                reason += f"; {cap.offering_status.replace('_', ' ')} ×{mult}"
        if s.triage_relevant == "yes":
            c = min(1.0, c + 0.1)
            reason += "; AI triage: relevant"
        elif s.triage_relevant == "no":
            c *= 0.4
            reason += "; AI triage: not relevant"
        if s.deep_fit == "not_fit":
            c *= 0.3
            reason += "; AI analysis: not a fit"
        if s.filter_decision == FilterDecision.REJECTED:
            c, reason = 0.0, "filtered as unrelated by deterministic rules"
        elif s.filter_decision == FilterDecision.LOW and c > 0.25:
            c, reason = 0.25, reason + "; below candidate threshold (capped 0.25)"
        comps["capability"] = {"score": round(c, 3), "reason": reason}

        # eligibility feasibility
        e, ereason = ELIGIBILITY_SCORE[s.eligibility]
        comps["eligibility"] = {"score": e, "reason": ereason}

        # technical scope / delivery fit
        if s.deep_fit:
            t = FIT_SCORE[s.deep_fit]
            treason = f"AI analysis fit: {s.deep_fit.replace('_', ' ')}"
            if s.deep_hardware:
                t *= 0.8
                treason += "; hardware in scope ×0.8"
        elif cap and s.hardware_supply:
            t, treason = 0.35, "equipment supply without a stated software/analytics component"
        elif cap:
            t = {"existing_offering": 0.7, "custom_build": 0.5}.get(cap.offering_status, 0.45)
            treason = f"inferred from offering status ({cap.offering_status.replace('_', ' ')}); scope not analysed"
        else:
            t, treason = 0.2, "no matching offering"
        comps["technical"] = {"score": round(t, 3), "reason": treason}

        # deadline feasibility
        days = days_remaining(s.closing_at_utc) if s.closing_at_utc else None
        if days is None:
            d, dreason = 0.5, "closing date not stated"
        elif days < 0:
            d, dreason = 0.0, "closed"
        elif days < 3:
            d, dreason = 0.15, f"{days:.1f} days left"
        elif days < self.settings.min_days_to_prepare:
            d, dreason = 0.4, f"{days:.1f} days left (< {self.settings.min_days_to_prepare} to prepare)"
        elif days < 10:
            d, dreason = 0.75, f"{days:.0f} days left"
        else:
            d, dreason = 1.0, f"{days:.0f} days left"
        comps["deadline"] = {"score": d, "reason": dreason}

        # commercial opportunity
        v = s.estimated_value_inr
        if v is None:
            m, mreason = 0.4, "value not stated"
        else:
            crore = v / 1e7
            m = 1.0 if crore >= 50 else 0.8 if crore >= 5 else 0.6 if crore >= 1 else 0.4 if crore >= 0.25 else 0.25
            mreason = f"stated value ₹{crore:,.2f} crore"
        comps["commercial"] = {"score": m, "reason": mreason}

        # strategic alignment
        org = normalize(s.organisation or "")
        hit = next((k for k in self.settings.strategic_org_keywords if normalize(k) in org), None)
        st = 0.8 if hit else 0.3
        sreason = f"strategic buyer ({hit})" if hit else "non-priority buyer"
        if s.opportunity_type in self.kb.global_.strategic_opportunity_types:
            st = min(1.0, st + 0.2)
            sreason += f"; {s.opportunity_type} (entry route)"
        comps["strategic"] = {"score": st, "reason": sreason}
        # Win history (off unless scoring.win_history_enabled): a bounded, stated bonus for a buyer BD has
        # already won with. Wins only; a loss never lowers the score. The count comes from stored review
        # statuses, so the same book always gives the same score.
        if self.settings.win_history_enabled and s.buyer_wins > 0:
            bonus = min(self.settings.win_history_max_bonus, s.buyer_wins * self.settings.win_history_bonus_per_win)
            applied = round(min(1.0, st + bonus) - st, 3)
            if applied > 0:
                wins = "1 win" if s.buyer_wins == 1 else f"{s.buyer_wins} wins"
                comps["strategic"] = {"score": round(st + applied, 3),
                                      "reason": f"{sreason}; {wins} with this buyer (+{applied:.2f})",
                                      "history_bonus": applied}

        weights = self.settings.weights
        wsum = sum(weights.values())
        # Relevance gate: deadline, value, eligibility etc. only count in proportion to the
        # capability match, so an unrelated notice cannot score highly on logistics alone.
        gate = round(min(1.0, comps["capability"]["score"] / 0.5), 3)
        total = 0.0
        for name, comp in comps.items():
            w = weights.get(name, 0.0)
            g = 1.0 if name == "capability" else gate
            comp["weight"] = w
            comp["points"] = round(comp["score"] * w / wsum * 100 * g, 2)
            if g < 1.0:
                comp["reason"] += f" (× relevance gate {g:.2f})"
            total += comp["points"]
        total = round(total, 1)

        th = self.settings.priority_thresholds
        if s.status in CLOSED_STATUSES:
            prio = Priority.P4
            flags.append(f"NOT ACTIVE ({s.status})")
        elif s.filter_decision in (FilterDecision.REJECTED, FilterDecision.LOW) or comps["capability"]["score"] < 0.3:
            prio = Priority.P4
        elif total >= th["P1"] and comps["capability"]["score"] >= 0.5:
            prio = Priority.P1
        elif total >= th["P2"]:
            prio = Priority.P2
        elif total >= th["P3"]:
            prio = Priority.P3
        else:
            prio = Priority.P4

        if s.hardware_supply and s.deep_fit not in ("existing_offering", "custom_build") and prio in (Priority.P1, Priority.P2):
            prio = Priority.P3
            flags.append("EQUIPMENT SUPPLY — monitor for SI/partner route")
        if prio in (Priority.P1, Priority.P2):
            if s.eligibility in (EligibilityStatus.UNKNOWN, EligibilityStatus.POTENTIALLY_ELIGIBLE):
                flags.append("ELIGIBILITY UNVERIFIED — human review required")
        if s.eligibility == EligibilityStatus.PARTNER_REQUIRED:
            flags.append("PARTNER / OEM REQUIRED")
        if s.eligibility == EligibilityStatus.LIKELY_INELIGIBLE:
            flags.append("LIKELY INELIGIBLE")
        if days is not None and 0 <= days < self.settings.closing_soon_days:
            flags.append("CLOSING SOON")
        if s.analysis_state == "skipped_budget":
            flags.append("AI ANALYSIS SKIPPED (budget limit)")
        elif s.analysis_state == "failed":
            flags.append("AI ANALYSIS FAILED")
        elif s.analysis_state == "skipped_llm_disabled" and prio != Priority.P4:
            flags.append("AI ANALYSIS NOT RUN (LLM disabled)")
        elif s.analysis_state == "deferred_no_documents" and prio != Priority.P4:
            flags.append("DEEP ANALYSIS WAITING FOR DOCUMENTS")
        if s.completeness < 0.5 and prio != Priority.P4:
            flags.append("LISTING-LEVEL DATA ONLY — download documents")
        return ScoreResult(total, prio, comps, flags)
