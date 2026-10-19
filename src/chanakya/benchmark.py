"""Cost/quality benchmark of three discovery architectures on a labelled set.

1. ``llm_all``     — every notice goes to the deep model with the full capability catalogue.
2. ``keyword_llm`` — any knowledge-base keyword (incl. weak ones) → deep model with the full catalogue.
3. ``chanakya``    — rules + lexical semantics; fast-model triage only in the grey band;
                     deep model only for strong/confirmed matches with document-level context.

The LLM is *simulated*: its relevance verdict is correct with probability
``llm_accuracy`` (deterministic per record), so the benchmark isolates the effect
of the funnel itself. Token counts are estimated from the real prompt sizes each
approach would send; output sizes and per-call latencies are stated assumptions.
Run against a real model by labelling live data and using ``chanakya run``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import Settings
from .connectors.manual import row_to_record
from .llm.base import estimate_tokens
from .llm.budget import cost_usd
from .models import FilterDecision
from .pipeline.analyze import DEEP_SCHEMA_TEXT, SYSTEM_PROMPT, TRIAGE_SCHEMA_TEXT
from .pipeline.classify import RuleClassifier
from .pipeline.knowledge import load_knowledge_base
from .pipeline.semantic import LexicalSemanticScorer
from .pipeline.textnorm import normalize, phrase_regex

ASSUMED_OUTPUT_TOKENS = {"triage": 160, "deep": 700}   # incl. low-effort reasoning tokens
ASSUMED_LATENCY_S = {"triage": 1.5, "deep": 6.0}
SEM_NORM = 0.30


@dataclass
class ApproachResult:
    name: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    calls_fast: int = 0
    calls_deep: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    missed: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


@dataclass
class BenchmarkResult:
    n: int
    n_relevant: int
    llm_accuracy: float
    fast_model: str
    deep_model: str
    approaches: list[ApproachResult]
    repeat_run_calls: int = 0


def _oracle(title: str, truth: bool, accuracy: float) -> bool:
    u = int(hashlib.sha256(title.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return truth if u < accuracy else not truth


def _prompt_tokens(purpose: str, payload: dict) -> int:
    schema = TRIAGE_SCHEMA_TEXT if purpose == "triage" else DEEP_SCHEMA_TEXT
    user = f"Task line. Return JSON exactly in this shape: {schema}\n<tender_data>{json.dumps(payload, ensure_ascii=False)}</tender_data>"
    return estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(user)


def run_benchmark(settings: Settings, dataset: Path, llm_accuracy: float = 0.9) -> BenchmarkResult:
    kb = load_knowledge_base(settings.app.capabilities_file)
    data = yaml.safe_load(Path(dataset).read_text(encoding="utf-8"))
    rows = data["records"] if isinstance(data, dict) else data
    classifier = RuleClassifier(kb, settings.filtering)
    semantic = LexicalSemanticScorer(kb)
    all_cards = [{"id": c.id, "name": c.name, "offering": c.offering, "status": c.offering_status,
                  "summary": c.description[:240]} for c in kb.capabilities]
    all_keywords = [k for c in kb.capabilities for k in (c.keywords.strong + c.keywords.medium + c.keywords.weak)]
    fast, deep = settings.llm.fast_model, settings.llm.deep_model
    approaches = {n: ApproachResult(n) for n in ("llm_all", "keyword_llm", "chanakya")}

    def call(a: ApproachResult, purpose: str, payload: dict) -> None:
        tin = _prompt_tokens(purpose, payload)
        tout = ASSUMED_OUTPUT_TOKENS[purpose]
        model = fast if purpose == "triage" else deep
        a.input_tokens += tin
        a.output_tokens += tout
        a.cost_usd += cost_usd(model, tin, tout, 0, settings.pricing, "cloud")
        a.latency_s += ASSUMED_LATENCY_S[purpose]
        if purpose == "triage":
            a.calls_fast += 1
        else:
            a.calls_deep += 1

    def tally(a: ApproachResult, predicted: bool, truth: bool, title: str) -> None:
        if predicted and truth:
            a.tp += 1
        elif predicted and not truth:
            a.fp += 1
        elif truth:
            a.fn += 1
            a.missed.append(title)
        else:
            a.tn += 1

    n_rel = 0
    for row in rows:
        rec = row_to_record(row, "benchmark", "benchmark", Path("."), "dataset")
        truth = row.get("label") == "relevant"
        n_rel += truth
        base = {"title": rec.title, "organisation": rec.organisation, "category": rec.category,
                "opportunity_type": rec.opportunity_type.value, "description": (rec.description or "")[:1500]}

        # 1. LLM on everything
        a = approaches["llm_all"]
        call(a, "deep", {**base, "candidate_capabilities": all_cards})
        tally(a, _oracle(rec.title, truth, llm_accuracy), truth, rec.title)

        # 2. Keyword filter then LLM
        a = approaches["keyword_llm"]
        text = normalize(rec.text_for_matching())
        if any(phrase_regex(k).search(text) for k in all_keywords):
            call(a, "deep", {**base, "candidate_capabilities": all_cards})
            tally(a, _oracle(rec.title, truth, llm_accuracy), truth, rec.title)
        else:
            tally(a, False, truth, rec.title)

        # 3. Chanakya funnel
        a = approaches["chanakya"]
        rr = classifier.classify(rec)
        decision = rr.decision
        sem = semantic.score(rec.text_for_matching()) if decision != FilterDecision.REJECTED else {}
        by_cap = {h.capability_id: h.score for h in rr.hits}
        combined = {cid: max(r, 0.65 * r + 0.35 * min(1.0, sem.get(cid, 0) / SEM_NORM)) for cid, r in by_cap.items()}
        best = max(combined.values()) if combined else 0.0
        best_sem = max(sem.values()) if sem else 0.0
        sem_bar = settings.semantic.candidate_threshold if (rr.tech_signals or rr.high_recall) \
            else max(settings.semantic.candidate_threshold, settings.semantic.weak_only_threshold)
        if decision == FilterDecision.LOW and best_sem >= sem_bar and not rr.exclusions:
            decision = FilterDecision.CANDIDATE
        top = sorted(combined, key=combined.get, reverse=True)[:3]
        cards = [c for c in all_cards if c["id"] in top]
        lo, hi = settings.llm.triage_band
        rich = len(rec.description or "") >= 200
        if decision in (FilterDecision.REJECTED, FilterDecision.LOW):
            tally(a, False, truth, rec.title)
            continue
        if best >= hi and decision == FilterDecision.STRONG:
            predicted = True
            if rich:
                call(a, "deep", {**base, "candidate_capabilities": cards})
                predicted = _oracle(rec.title, truth, llm_accuracy) or best >= 0.8
        else:
            call(a, "triage", {**base, "candidate_capabilities": cards, "rule_hint": round(best, 2)})
            predicted = _oracle(rec.title, truth, llm_accuracy)
            if predicted and rich:
                call(a, "deep", {**base, "candidate_capabilities": cards})
        tally(a, predicted, truth, rec.title)

    return BenchmarkResult(n=len(rows), n_relevant=n_rel, llm_accuracy=llm_accuracy, fast_model=fast,
                           deep_model=deep, approaches=list(approaches.values()), repeat_run_calls=0)


def format_markdown(res: BenchmarkResult) -> str:
    lines = [
        f"Dataset: {res.n} notices ({res.n_relevant} labelled relevant). Simulated LLM accuracy: {res.llm_accuracy:.0%}. "
        f"Models priced as fast={res.fast_model}, deep={res.deep_model}.",
        "",
        "| Approach | Precision | Recall | F1 | Fast calls | Deep calls | Input tokens | Output tokens | Est. cost (USD) | Est. LLM latency (s) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in res.approaches:
        lines.append(f"| {a.name} | {a.precision:.2f} | {a.recall:.2f} | {a.f1:.2f} | {a.calls_fast} | {a.calls_deep} | "
                     f"{a.input_tokens:,} | {a.output_tokens:,} | {a.cost_usd:.4f} | {a.latency_s:.0f} |")
    base = res.approaches[0]
    ch = res.approaches[-1]
    if base.input_tokens:
        lines += ["", f"Chanakya funnel uses {ch.input_tokens / base.input_tokens:.1%} of the input tokens and "
                      f"{(ch.cost_usd / base.cost_usd if base.cost_usd else 0):.1%} of the cost of analysing every notice. "
                      "Repeat runs over unchanged notices make no LLM calls (content-hash cache; asserted in "
                      "tests/test_reporting_e2e.py::test_full_run_then_incremental_runs)."]
    for a in res.approaches:
        if a.missed:
            lines.append(f"\nMissed by {a.name}: " + "; ".join(t[:70] for t in a.missed))
    lines.append("\nAssumptions: output tokens per call triage≈160, deep≈700; latency triage≈1.5 s, deep≈6 s; "
                 "LLM verdicts simulated (same per-notice error pattern for every approach). In the funnel, STRONG rule "
                 "matches are accepted without an LLM verdict, which is why its precision can exceed the simulated LLM's; "
                 "deep calls happen only with ≥200 chars of description/document context.")
    return "\n".join(lines)
