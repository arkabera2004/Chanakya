"""Classification, semantics, eligibility and scoring."""

from __future__ import annotations

import pytest

from chanakya.config import CompanyProfile, FilteringSettings, ScoringSettings
from chanakya.models import EligibilityFinding, EligibilityStatus, EvidenceRef, FilterDecision, OpportunityType, Priority, TenderRecord
from chanakya.pipeline.classify import RuleClassifier
from chanakya.pipeline.eligibility import assess, extract_requirements, overall_status
from chanakya.pipeline.knowledge import load_knowledge_base
from chanakya.pipeline.scoring import ScoreInput, Scorer
from chanakya.pipeline.semantic import LexicalSemanticScorer
from chanakya.timeutil import now_utc


def rec(title, **kw) -> TenderRecord:
    return TenderRecord(source_id="t", platform="t", source_record_key=title[:30], title=title, **kw)


@pytest.fixture
def clf(kb):
    return RuleClassifier(kb, FilteringSettings())


# ---------------------------------------------------------------- deterministic classification
def test_road_resurfacing_with_monitoring_is_not_a_candidate(clf):
    r = clf.classify(rec("Road resurfacing work with installation of traffic monitoring signboards"))
    assert r.decision in (FilterDecision.REJECTED, FilterDecision.LOW)
    assert "resurfacing" in r.exclusions


def test_plain_monitoring_word_never_enough(clf):
    r = clf.classify(rec("Monitoring of attendance registers"))
    assert r.best_score < FilteringSettings().candidate_threshold


def test_video_analytics_is_strong_for_arjuna(clf):
    r = clf.classify(rec("Implementation of AI-based video analytics on existing CCTV cameras"))
    assert r.decision == FilterDecision.STRONG and r.hits[0].capability_id == "CAP-CV-VA"


def test_strong_term_rescues_excluded_title(clf):
    r = clf.classify(rec("Construction of ICCC including video analytics and civil works"))
    assert r.decision in (FilterDecision.STRONG, FilterDecision.CANDIDATE)


def test_civil_construction_of_command_centre_not_strong(clf):
    r = clf.classify(rec("Construction of Integrated Command and Control Centre building including civil works"))
    assert r.decision == FilterDecision.LOW


def test_synonym_and_overlap_counted_once(clf):
    r = clf.classify(rec("Supply of CCTV camera spares and cables for police stations"))
    assert r.decision != FilterDecision.STRONG and r.hardware_supply


def test_plural_matching(clf):
    r = clf.classify(rec("Solution to measure dwell time of visitors using existing cameras"))
    assert r.hits[0].capability_id == "CAP-CV-VA" and r.decision in (FilterDecision.STRONG, FilterDecision.CANDIDATE)


def test_hardware_supply_capped_below_strong(clf):
    r = clf.classify(rec("Rate contract for supplying, installing and commissioning of CCTV surveillance cameras"))
    assert r.hardware_supply and r.decision == FilterDecision.CANDIDATE


def test_high_recall_net_keeps_tech_category_without_keywords(clf):
    r = clf.classify(rec("Selection of agency for state portal", category="Computer Software/Web Site"))
    assert r.decision != FilterDecision.REJECTED


def test_eoi_type_kept_for_semantics(clf):
    r = clf.classify(rec("Expression of interest for innovation challenge", opportunity_type=OpportunityType.EOI))
    assert r.decision != FilterDecision.REJECTED


def test_kb_validation_rejects_bad_status(tmp_path):
    p = tmp_path / "kb.yaml"
    p.write_text("kb_version: x\ncapabilities:\n  - {id: CAP-X, name: n, offering: o, offering_status: maybe, confidence: high, description: d}\n")
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        load_knowledge_base(p)


# ---------------------------------------------------------------- semantics
def test_lexical_semantic_prefers_right_capability(kb):
    sem = LexicalSemanticScorer(kb)
    s = sem.score("Automated analysis of recorded citizen calls to identify complaints and escalate them")
    assert max(s, key=s.get) == "CAP-VOICE"
    s2 = sem.score("Aerial infrared survey of photovoltaic modules to find hotspots")
    assert max(s2, key=s2.get) == "CAP-SOLAR-INSP"


# ---------------------------------------------------------------- eligibility
TEXT = ("The bidder should have an average annual turnover of Rs. 10 crore during the last three financial years. "
        "EMD of Rs. 5,00,000 shall be submitted. Bidder must be ISO 27001 certified and CERT-In empanelled. "
        "Manufacturer's Authorization Form (MAF) from the OEM is mandatory. Consortium is not allowed. "
        "MSEs and Startups are exempted from EMD. At least 3 years of experience in similar projects with Government.")


def findings():
    return extract_requirements([(TEXT, EvidenceRef(source="document", document="RFP.pdf", page=4, section="Eligibility"))])


def test_extract_requirements():
    f = {x.criterion: x for x in findings()}
    assert f["turnover"].value == 10 and f["turnover"].evidence.page == 4
    assert f["emd"].value == 500000
    assert {"certification", "oem_authorization", "consortium", "mse_exemption", "experience"} <= set(f)
    certs = {x.unit for x in findings() if x.criterion == "certification"}
    assert "ISO 27001" in certs and any("CERT" in c for c in certs)


def test_net_worth_extracted_and_left_for_review():
    ev = EvidenceRef(source="document", document="RFP.pdf", page=5, section="Financial")
    pos = extract_requirements([("The bidder shall have a positive net worth in each of the last 3 years.", ev)])
    amt = extract_requirements([("Net worth of the bidder should be at least Rs. 2 crore as on 31.03.2026.", ev)])
    (p,), (a,) = [x for x in pos if x.criterion == "net_worth"], [x for x in amt if x.criterion == "net_worth"]
    assert p.unit == "positive" and p.value is None
    assert a.value == 2 and a.unit == "INR crore"
    assert all(x.assessment == "unknown" for x in assess([p, a], CompanyProfile(average_turnover_3y_inr_crore=50)))


def test_unknown_profile_never_assumes_eligibility():
    out = assess(findings(), CompanyProfile())
    assert all(x.assessment in ("unknown", "partner", "not_applicable") for x in out)
    assert overall_status(out) == EligibilityStatus.PARTNER_REQUIRED  # OEM authorisation present


def test_profile_drives_assessment():
    prof = CompanyProfile(average_turnover_3y_inr_crore=4, certifications=["ISO 27001"], cert_in_empanelled=False,
                          years_in_operation=5)
    out = {(x.criterion, x.unit): x.assessment for x in assess(findings(), prof)}
    assert out[("turnover", "INR crore")] == "does_not_meet"
    assert overall_status(assess(findings(), prof)) == EligibilityStatus.LIKELY_INELIGIBLE


def test_overall_status_variants():
    meets = EligibilityFinding(criterion="turnover", requirement_text="x", assessment="meets")
    unknown = EligibilityFinding(criterion="experience", requirement_text="y", assessment="unknown")
    assert overall_status([]) == EligibilityStatus.UNKNOWN
    assert overall_status([meets]) == EligibilityStatus.VERIFIED_ELIGIBLE
    assert overall_status([meets, unknown]) == EligibilityStatus.POTENTIALLY_ELIGIBLE
    assert overall_status([unknown]) == EligibilityStatus.UNKNOWN


# ---------------------------------------------------------------- scoring
def base_input(**kw) -> ScoreInput:
    from datetime import timedelta
    d = dict(filter_decision=FilterDecision.STRONG, best_capability="CAP-CV-VA", capability_score=0.9,
             eligibility=EligibilityStatus.UNKNOWN, status="active", closing_at_utc=now_utc() + timedelta(days=20),
             estimated_value_inr=6e7, organisation="Synthetic Smart City Ltd", opportunity_type="RFP",
             triage_relevant="yes", deep_fit="existing_offering", completeness=0.8)
    d.update(kw)
    return ScoreInput(**d)


def test_score_breakdown_sums_and_flags_unverified_eligibility(kb):
    r = Scorer(ScoringSettings(), kb).score(base_input())
    assert r.total == pytest.approx(sum(c["points"] for c in r.components.values()), abs=0.05)
    assert set(r.components) == {"capability", "eligibility", "technical", "deadline", "commercial", "strategic"}
    assert r.priority == Priority.P1
    assert any("ELIGIBILITY UNVERIFIED" in f for f in r.flags)


def test_expired_and_rejected_are_p4(kb):
    sc = Scorer(ScoringSettings(), kb)
    assert sc.score(base_input(status="expired")).priority == Priority.P4
    assert sc.score(base_input(filter_decision=FilterDecision.REJECTED)).priority == Priority.P4


def test_irrelevant_cannot_rank_high_on_deadline_and_value(kb):
    r = Scorer(ScoringSettings(), kb).score(base_input(capability_score=0.05, triage_relevant=None, deep_fit=None,
                                                     filter_decision=FilterDecision.LOW))
    assert r.priority == Priority.P4


def test_closing_soon_and_ineligible_flags(kb):
    from datetime import timedelta
    r = Scorer(ScoringSettings(), kb).score(base_input(closing_at_utc=now_utc() + timedelta(days=2),
                                                     eligibility=EligibilityStatus.LIKELY_INELIGIBLE))
    assert "CLOSING SOON" in r.flags and "LIKELY INELIGIBLE" in r.flags
    assert r.components["deadline"]["score"] < 0.5


def test_weights_are_configurable(kb):
    s = ScoringSettings(weights={"capability": 100, "eligibility": 0, "technical": 0, "deadline": 0, "commercial": 0, "strategic": 0})
    r = Scorer(s, kb).score(base_input())
    assert r.total == pytest.approx(r.components["capability"]["score"] * 100, abs=0.1)
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ScoringSettings(weights={"capability": -1})


def test_embedding_scorer_caches_vectors(kb):
    from chanakya.pipeline.semantic import EmbeddingSemanticScorer
    store: dict = {}
    calls = {"n": 0}

    def embed(texts):
        calls["n"] += 1
        return [[float(len(t) % 7), 1.0, float("camera" in t.lower())] for t in texts]

    sc = EmbeddingSemanticScorer(kb, embed, "embeddinggemma", lambda h, m: store.get((h, m)),
                                 lambda h, m, v: store.__setitem__((h, m), v))
    first = calls["n"]
    sc.score("video analytics on cameras")
    sc.score("video analytics on cameras")
    assert calls["n"] == first + 1                       # unchanged text is never re-embedded
    EmbeddingSemanticScorer(kb, embed, "embeddinggemma", lambda h, m: store.get((h, m)), lambda h, m, v: None)
    assert calls["n"] == first + 1                       # capability vectors reused from cache


def test_en_and_em_dashes_in_notice_text_are_normalised():
    from chanakya.pipeline.textnorm import normalize

    assert normalize("AI\u2013based video\u2014analytics") == normalize("AI-based video-analytics")


# ---------------------------------------------------------------- false positives seen on the live book (2026-10-10)
@pytest.mark.parametrize("title", [
    "Imp. Dev. Of lanes and Drains by Pdg. RMC from H.No.16 to H.No.41 BI -Block",   # BI is a block, not business intelligence
    "SYP CETIRIZINE 5 MG/5 ML 30 ML",                                                 # ml is millilitres
    "REPAIR/MAINTENANCE OF MES SUB STN, SECURITY LIGHT, SIGNBOARD",                  # MES is Military Engineer Services
    "CUT-RESISTANCE INDUSTRIAL SAFETY GLOVES with polyurethane",
])
def test_abbreviations_and_goods_do_not_reach_candidate(clf, title):
    assert clf.classify(rec(title)).decision in (FilterDecision.REJECTED, FilterDecision.LOW)


def test_device_words_alone_are_never_strong(clf):
    r = clf.classify(rec("CCTV Surveillance System for the new depot"))
    assert r.decision == FilterDecision.CANDIDATE and not r.hits[0].strong


def test_maintenance_of_installed_devices_is_the_equipment_route(clf):
    r = clf.classify(rec("Annual Maintenance Contract (AMC) for CCTV Surveillance System installed at various Sub-Stations"))
    assert r.hardware_supply and r.decision == FilterDecision.CANDIDATE


def test_triage_verdict_bounds_the_priority(kb):
    base = dict(filter_decision=FilterDecision.CANDIDATE, best_capability="CAP-CV-VA", capability_score=0.49,
                eligibility=EligibilityStatus.UNKNOWN, status="active", closing_at_utc=None, estimated_value_inr=None,
                organisation=None, opportunity_type="Tender")
    scorer = Scorer(ScoringSettings(), kb)
    yes = scorer.score(ScoreInput(**base, triage_relevant="yes"))
    assert yes.priority in (Priority.P1, Priority.P2)
    unclear = scorer.score(ScoreInput(**base, triage_relevant="unclear"))
    assert unclear.priority == Priority.P3 and any(f.startswith("AI TRIAGE UNCLEAR") for f in unclear.flags)
    no = scorer.score(ScoreInput(**base, triage_relevant="no"))
    assert no.priority == Priority.P4
    # A document analysis that finds a fit outranks the title-only triage verdict.
    read = scorer.score(ScoreInput(**base, triage_relevant="unclear", deep_fit="existing_offering"))
    assert read.priority in (Priority.P1, Priority.P2)
