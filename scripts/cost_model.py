"""Reproducible LLM cost model for docs/19_COST_ESTIMATES.md.

Prompt sizes are *measured* by building real prompts with the production prompt
builders (Analyzer._payload / Analyzer._messages) for a representative,
clearly synthetic notice. Prices come from config/chanakya.example.yaml
(Ollama Cloud price list). Volumes and rates are labelled assumptions.

    python scripts/cost_model.py            # prints a markdown report
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from chanakya.config import load_settings
from chanakya.documents.extract import Section, build_context
from chanakya.llm.base import estimate_tokens
from chanakya.llm.budget import cost_usd
from chanakya.pipeline.analyze import DEEP_SCHEMA_TEXT, TRIAGE_SCHEMA_TEXT, Analyzer
from chanakya.pipeline.knowledge import load_knowledge_base
from chanakya.pipeline.runner import excerpt_chars

ROOT = Path(__file__).resolve().parents[1]

# Output tokens per call (assumption; the benchmark uses the same figures).
OUT_TRIAGE, OUT_DEEP = 160, 700
# Funnel assumptions (labelled in the report).
CANDIDATE_RATE = 0.05     # share of new/changed notices that reach CANDIDATE/STRONG
TRIAGE_SHARE = 0.60       # share of candidates that fall in the triage grey band
DEEP_PER_DAY = 8          # deep analyses per day (bounded by imported documents; cap 15/run)
DAYS = 30


def measure() -> dict:
    s = load_settings(ROOT / "config" / "chanakya.example.yaml")
    kb = load_knowledge_base(ROOT / "config" / "wayam_capabilities.yaml")
    an = Analyzer(provider=None, settings=s, kb=kb, session=None, guard=None, run_id=None)  # payload builders only
    opp = SimpleNamespace(  # SYNTHETIC representative notice (not a real tender)
        title="Selection of agency for AI based video analytics on existing CCTV cameras at 40 bus depots",
        organisation="Synthetic State Road Transport Corporation", department="IT Cell", category="Services",
        opportunity_type="RFP", closing_at=None, estimated_value_inr=None,
        description=("Scope includes people counting, crowd density alerts, intrusion detection and a dashboard for "
                     "depot managers. Integration with the existing VMS is required. ") * 6)
    caps = ["CAP-CV-VA", "CAP-CV-SAFETY", "CAP-AGENT"]
    tri_data, _ = an._payload("triage", opp, caps, 0.35, None, None)
    tri_in = sum(estimate_tokens(m.content) for m in an._messages("triage", TRIAGE_SCHEMA_TEXT, tri_data))
    # Without a funnel there is no rule stage to pick candidates, so the baseline sends the full catalogue.
    all_caps = [cap.id for cap in kb.capabilities]
    listing_data, _ = an._payload("deep", opp, all_caps, 0.0, None, None)
    listing_in = sum(estimate_tokens(m.content) for m in an._messages("deep", DEEP_SCHEMA_TEXT, listing_data))
    line = "The selected agency shall configure analytics rules, alerts and reports for each depot camera feed.\n"
    names = ["scope_of_work", "technical_requirements", "eligibility", "experience", "financial_qualification",
             "emd", "evaluation", "deliverables"]
    secs = [Section(n, "RFP.pdf", i + 3, i + 4, n.replace("_", " ").title(), line * 60) for i, n in enumerate(names)]
    excerpt, cites = build_context(secs, max_chars=excerpt_chars(s.budget.max_input_tokens_per_call))
    deep_data, _ = an._payload("deep", opp, caps, 0.6, excerpt, cites)
    deep_in = sum(estimate_tokens(m.content) for m in an._messages("deep", DEEP_SCHEMA_TEXT, deep_data))
    return {"settings": s, "triage_in": tri_in, "deep_in": deep_in, "listing_deep_in": listing_in}


def report() -> str:
    m = measure()
    s = m["settings"]
    fast, deep = s.llm.fast_model, s.llm.deep_model
    p = s.pricing

    def c(model, tin, tout):
        return cost_usd(model, tin, tout, 0, p, "cloud")

    c_tri = c(fast, m["triage_in"], OUT_TRIAGE)
    c_deep = c(deep, m["deep_in"], OUT_DEEP)
    c_all = c(deep, m["listing_deep_in"], OUT_DEEP)
    b = s.budget
    worst_deep = c(deep, b.max_input_tokens_per_call, s.llm.max_output_tokens_deep)
    worst_tri = c(fast, m["triage_in"], s.llm.max_output_tokens_fast)
    worst_run = b.max_deep_analyses_per_run * worst_deep + (b.max_calls_per_run - b.max_deep_analyses_per_run) * worst_tri
    lines = [
        "### Measured prompt sizes and per-call cost",
        "",
        f"Prices: {fast} ${p[fast].input}/M in, ${p[fast].output}/M out; {deep} ${p[deep].input}/M in, "
        f"${p[deep].output}/M out (Ollama Cloud list, standard rate).",
        "",
        "| Call | Input tokens (measured) | Output tokens (assumed) | Cost per call (USD) |",
        "|---|---|---|---|",
        f"| Fast triage ({fast}) | {m['triage_in']:,} | {OUT_TRIAGE} | {c_tri:.6f} |",
        f"| Deep analysis with document excerpt ({deep}) | {m['deep_in']:,} | {OUT_DEEP} | {c_deep:.6f} |",
        f"| Baseline: deep model per notice, listing data + full {len(load_knowledge_base(ROOT / 'config' / 'wayam_capabilities.yaml').capabilities)}-capability catalogue ({deep}) | {m['listing_deep_in']:,} | {OUT_DEEP} | {c_all:.6f} |",
        f"| Worst case deep call at the caps ({b.max_input_tokens_per_call:,} in / {s.llm.max_output_tokens_deep:,} out) "
        f"| {b.max_input_tokens_per_call:,} | {s.llm.max_output_tokens_deep:,} | {worst_deep:.6f} |",
        "",
        f"Worst case for one run at every cap ({b.max_deep_analyses_per_run} deep + "
        f"{b.max_calls_per_run - b.max_deep_analyses_per_run} triage calls): **${worst_run:.4f}** "
        f"(the configured `max_cost_per_run_usd` is ${b.max_cost_per_run_usd:.2f}).",
        "",
        "### Monthly projection",
        "",
        f"Assumptions: {CANDIDATE_RATE:.0%} of new/changed notices reach CANDIDATE/STRONG; {TRIAGE_SHARE:.0%} of those "
        f"need triage; {DEEP_PER_DAY} deep analyses/day (limited by documents BD imports); {DAYS} days/month; "
        "unchanged notices cost nothing (content-hash cache).",
        "",
        "| New/changed notices per day | Funnel: triage/day | Funnel: deep/day | Funnel USD/month | "
        "LLM on every notice USD/month | Funnel share |",
        "|---|---|---|---|---|---|",
    ]
    for vol in (500, 1500, 6000, 15000):
        tri = vol * CANDIDATE_RATE * TRIAGE_SHARE
        funnel = (tri * c_tri + DEEP_PER_DAY * c_deep) * DAYS
        everything = vol * c_all * DAYS
        lines.append(f"| {vol:,} | {tri:,.0f} | {DEEP_PER_DAY} | {funnel:.2f} | {everything:,.2f} | "
                     f"{funnel / everything:.1%} |")
    lines += ["", "Sensitivity at 6,000 notices/day:", "",
              "| Candidate rate | Deep/day | Funnel USD/month |", "|---|---|---|"]
    for rate, d in ((0.05, 8), (0.10, 15), (0.20, 15), (0.20, 30)):
        tri = 6000 * rate * TRIAGE_SHARE
        lines.append(f"| {rate:.0%} | {d} | {(tri * c_tri + d * c_deep) * DAYS:.2f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(report())
