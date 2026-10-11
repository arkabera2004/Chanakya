"""Deterministic eligibility extraction and assessment.

Requirements are pulled from tender text with regular expressions (cheap and
auditable). They are then compared with Wayam's *configured* company profile.
Anything the profile does not state is assessed as ``unknown`` — Chanakya never
assumes Wayam qualifies.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..config import CompanyProfile
from ..models import EligibilityFinding, EligibilityStatus, EvidenceRef, normalize_space

_AMOUNT = r"(?:rs\.?|inr|₹)?\s*([\d][\d,]*(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?|lakh)?"

PATTERNS = {
    "turnover": re.compile(r"(average\s+)?annual\s+(?:financial\s+)?turn\s?over[^.;]{0,160}?" + _AMOUNT, re.I),
    "emd": re.compile(r"(?:earnest money(?: deposit)?|\bemd\b|bid security)[^.;]{0,80}?(?:rs\.?|inr|₹)\s*([\d][\d,]*(?:\.\d+)?)\s*(crores?|lakhs?|lakh)?", re.I),
    "experience_years": re.compile(r"(?:at least|minimum(?: of)?|not less than)\s+(\d{1,2})\s+years?(?: of)?\s+(?:experience|existence|operation)", re.I),
    "similar_works": re.compile(r"(?:three|two|one|3|2|1)\s+similar\s+(?:completed\s+)?(?:works?|projects?|assignments?)[^.;]{0,160}", re.I),
    "govt_experience": re.compile(r"(?:experience|work orders?|projects?)\s+(?:with|for|in)\s+(?:any\s+)?(?:central|state)?\s*(?:government|govt\.?|psu|public sector)", re.I),
    "certification": re.compile(r"\b(iso\s*/?\s*(?:iec\s*)?(?:9001|27001|20000(?:-1)?|14001|45001)|cmmi(?:\s*(?:dev|svc))?(?:\s*(?:level|ml)\s*[2-5])?|cert-?in\s+empanel(?:l)?ed|stqc)\b", re.I),
    "oem_authorization": re.compile(r"(manufacturer'?s?\s+authori[sz]ation(?:\s+form)?|\bmaf\b|oem\s+authori[sz]ation|authori[sz]ation\s+from\s+(?:the\s+)?oem|authori[sz]ed\s+(?:dealer|distributor|partner)\s+of\s+(?:the\s+)?oem)", re.I),
    "consortium_no": re.compile(r"(consortium|joint venture|jv)[^.;]{0,40}?\b(not\s+(?:be\s+)?(?:allowed|permitted|acceptable)|shall not be)\b", re.I),
    "consortium_yes": re.compile(r"(consortium|joint venture|jv)[^.;]{0,40}?\b(?<!not )(allowed|permitted|acceptable)\b", re.I),
    "mse_exemption": re.compile(r"\b(mse|msme|micro and small enterprises?|start-?ups?)\b[^.;]{0,100}\bexempt", re.I),
    # amount must carry a currency marker or a crore/lakh unit ("last 3 years" is not an amount)
    "net_worth": re.compile(r"(positive)\s+net\s*worth|net\s*worth[^.;]{0,80}?(?:(positive)|"
                            r"(?:rs\.?|inr|₹)\s*([\d][\d,]*(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?|lakh)?|"
                            r"([\d][\d,]*(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?|lakh)\b)", re.I),
}


def _to_crore(value: str, unit: str | None) -> float | None:
    try:
        v = float(value.replace(",", ""))
    except ValueError:
        return None
    u = (unit or "").lower()
    if u.startswith("cr"):
        return v
    if u.startswith("la"):
        return v / 100.0
    return v / 1e7 if v >= 1e5 else None  # plain rupees → crore; tiny numbers are ambiguous


def _sentence(text: str, start: int, end: int) -> str:
    s = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    e_candidates = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    e = min(e_candidates) if e_candidates else len(text)
    return normalize_space(text[s:e + 1])[:400]


def extract_requirements(chunks: Iterable[tuple[str, EvidenceRef]]) -> list[EligibilityFinding]:
    findings: list[EligibilityFinding] = []
    seen: set[tuple[str, str, str]] = set()

    def add(f: EligibilityFinding) -> None:
        key = (f.criterion, f.unit or "", (f.requirement_text or "")[:120].lower())
        if key not in seen:
            seen.add(key)
            findings.append(f)

    for text, ev in chunks:
        if not text:
            continue
        for m in PATTERNS["turnover"].finditer(text):
            crore = _to_crore(m.group(2), m.group(3))
            if crore:
                add(EligibilityFinding(criterion="turnover", requirement_text=_sentence(text, m.start(), m.end()),
                                       value=round(crore, 4), unit="INR crore",
                                       evidence=ev.model_copy(update={"quote": m.group(0)[:200]})))
        for m in PATTERNS["net_worth"].finditer(text):
            # Not in company_profile → always "unknown" (human review), never assumed.
            positive = bool(m.group(1) or m.group(2))
            amount, unit = (m.group(3), m.group(4)) if m.group(3) else (m.group(5), m.group(6))
            if not positive and not amount:
                continue
            nw = None if positive else _to_crore(amount, unit)
            add(EligibilityFinding(criterion="net_worth", requirement_text=_sentence(text, m.start(), m.end()),
                                   value=nw, unit="positive" if positive else ("INR crore" if nw else None),
                                   evidence=ev.model_copy(update={"quote": m.group(0)[:200]})))
        for m in PATTERNS["emd"].finditer(text):
            crore = _to_crore(m.group(1), m.group(2))
            rupees = crore * 1e7 if crore else None
            add(EligibilityFinding(criterion="emd", requirement_text=_sentence(text, m.start(), m.end()),
                                   value=rupees, unit="INR", assessment="not_applicable",
                                   evidence=ev.model_copy(update={"quote": m.group(0)[:200]})))
        for m in PATTERNS["experience_years"].finditer(text):
            add(EligibilityFinding(criterion="experience", requirement_text=_sentence(text, m.start(), m.end()),
                                   value=float(m.group(1)), unit="years", evidence=ev.model_copy(update={"quote": m.group(0)})))
        for key in ("similar_works", "govt_experience"):
            for m in PATTERNS[key].finditer(text):
                add(EligibilityFinding(criterion="experience", requirement_text=_sentence(text, m.start(), m.end()),
                                       unit="government" if key == "govt_experience" else "similar works",
                                       evidence=ev.model_copy(update={"quote": m.group(0)[:200]})))
        for m in PATTERNS["certification"].finditer(text):
            add(EligibilityFinding(criterion="certification", requirement_text=_sentence(text, m.start(), m.end()),
                                   unit=normalize_space(m.group(1)).upper(), evidence=ev.model_copy(update={"quote": m.group(0)})))
        for m in PATTERNS["oem_authorization"].finditer(text):
            add(EligibilityFinding(criterion="oem_authorization", requirement_text=_sentence(text, m.start(), m.end()),
                                   assessment="partner", evidence=ev.model_copy(update={"quote": m.group(0)})))
        for m in PATTERNS["consortium_no"].finditer(text):
            add(EligibilityFinding(criterion="consortium", requirement_text=_sentence(text, m.start(), m.end()),
                                   unit="not allowed", assessment="not_applicable", evidence=ev.model_copy(update={"quote": m.group(0)})))
        for m in PATTERNS["consortium_yes"].finditer(text):
            if not PATTERNS["consortium_no"].search(m.group(0)):
                add(EligibilityFinding(criterion="consortium", requirement_text=_sentence(text, m.start(), m.end()),
                                       unit="allowed", assessment="not_applicable", evidence=ev.model_copy(update={"quote": m.group(0)})))
        for m in PATTERNS["mse_exemption"].finditer(text):
            add(EligibilityFinding(criterion="mse_exemption", requirement_text=_sentence(text, m.start(), m.end()),
                                   assessment="not_applicable", evidence=ev.model_copy(update={"quote": m.group(0)[:200]})))
    return findings


def assess(findings: list[EligibilityFinding], profile: CompanyProfile) -> list[EligibilityFinding]:
    out = []
    certs = {c.upper().replace(" ", "") for c in profile.certifications}
    turnover = profile.average_turnover_3y_inr_crore or profile.annual_turnover_inr_crore
    for f in findings:
        a = f.assessment
        if f.criterion == "turnover" and f.value is not None:
            a = "unknown" if turnover is None else ("meets" if turnover >= f.value else "does_not_meet")
        elif f.criterion == "experience":
            if f.unit == "years" and f.value is not None:
                a = "unknown" if profile.years_in_operation is None else (
                    "meets" if profile.years_in_operation >= f.value else "does_not_meet")
            elif f.unit == "government":
                a = "unknown" if profile.past_government_projects is None else (
                    "does_not_meet" if profile.past_government_projects == 0 else "unknown")
            else:
                a = "unknown"  # similarity of past work always needs human judgement
        elif f.criterion == "certification":
            name = (f.unit or "").replace(" ", "")
            if "CERT" in name:
                a = "unknown" if profile.cert_in_empanelled is None else ("meets" if profile.cert_in_empanelled else "does_not_meet")
            elif not certs:
                a = "unknown"
            else:
                a = "meets" if any(name.startswith(c) or c.startswith(name) for c in certs) else "does_not_meet"
        out.append(f.model_copy(update={"assessment": a}))
    return out


def overall_status(findings: list[EligibilityFinding], hardware_heavy: bool = False) -> EligibilityStatus:
    gating = [f for f in findings if f.assessment not in ("not_applicable",)]
    if any(f.assessment == "does_not_meet" for f in gating):
        return EligibilityStatus.LIKELY_INELIGIBLE
    if any(f.assessment == "partner" for f in gating) or hardware_heavy:
        return EligibilityStatus.PARTNER_REQUIRED
    if not gating:
        return EligibilityStatus.UNKNOWN
    if all(f.assessment == "meets" for f in gating):
        return EligibilityStatus.VERIFIED_ELIGIBLE
    if any(f.assessment == "meets" for f in gating):
        return EligibilityStatus.POTENTIALLY_ELIGIBLE
    return EligibilityStatus.UNKNOWN
