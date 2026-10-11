# 11 — Token and Cost Optimisation

Principle: **free stages decide, paid stages resolve.** Every notice passes through deterministic and
lexical stages; only the ambiguous or the clearly promising reach a model, and then with the smallest
input that can answer the question.

## The ten levers (all implemented)

| # | Lever | Where | Effect |
|---|---|---|---|
| 1 | Incremental acquisition (high-water mark − overlap) | `runner._scan_source` | Only new pages are fetched |
| 2 | Content hash per record | `store.upsert_record` | Unchanged notices skip all assessment (repeat run = 0 LLM calls, asserted) |
| 3 | Exclusions and rules before anything else | `classify.py` | In the live snapshot 16 of 17 active notices were REJECTED at zero cost (the 17th was LOW) |
| 4 | Free semantics | `semantic.py` lexical TF-IDF | Recall for paraphrased notices without embeddings or API calls |
| 5 | Expired notices never analysed | `assess` (`active` check) | Closed notices are scored P4 deterministically |
| 6 | Two models, two prompts | `analyze.py` | Cheap `gpt-oss:20b` triage (≈1k tokens) only in the grey band; `gpt-oss:120b` deep analysis only for strong/confirmed notices |
| 7 | No deep analysis without context | `RICH_CONTEXT_CHARS`, `deferred_no_documents` | No paying to "analyse" a 12-word title |
| 8 | Bounded, prioritised excerpts | `build_context`, `excerpt_chars`, `_fit_excerpt` | Scope/eligibility sections first; requests never exceed `max_input_tokens_per_call` |
| 9 | Only 2 to 3 candidate capability cards | `_payload` | ≈2,000 fewer input tokens per call than sending the 21-card catalogue (measured by `scripts/cost_model.py`) |
| 10 | Result cache | `ai_analysis_results.cache_key` = sha256(purpose, model, prompt version, KB version, input) | Re-runs, re-exports and unchanged corrigenda cost nothing |

Additional controls: `temperature 0`, `think: low` for gpt-oss (reasoning effort is billed as output),
output caps (300 fast / 1,200 deep), compact JSON schemas written once in the prompt, one repair retry
maximum.

## Measured effect

Benchmark on 64 labelled notices (`docs/validation/BENCHMARK.md`, reproducible with
`chanakya benchmark`):

| Approach | Recall | Precision | Calls | Input tokens | Cost |
|---|---|---|---|---|---|
| Deep model on every notice | 0.83 | 0.83 | 64 deep | 180,258 | $0.0539 |
| Keyword filter → deep model | 0.83 | 0.96 | 46 deep | 129,567 | $0.0388 |
| **Chanakya funnel** | **0.93** | **1.00** | 11 fast + 3 deep | **9,689** | **$0.0027** |

The funnel uses 5.4% of the input tokens and 5.0% of the cost while improving recall, because its
recall comes from the free stages (29/29 relevant notices passed the funnel; both end-to-end misses are
simulated LLM errors) and STRONG rule matches do not depend on an LLM verdict. The benchmark's LLM is
simulated (90% accuracy, same per-notice error pattern for all approaches): it measures routing and
token volume, not model quality.

## Budget guard (`llm/budget.py`)

Checked **before** every call, using the worst case (estimated input + full output cap):

| Limit | Default | When exceeded |
|---|---|---|
| `max_cost_per_run_usd` | 0.50 | Call refused |
| `max_cost_per_day_usd` | 2.00 (spend since 00:00 IST from `ai_usage_events`) | Call refused |
| `max_calls_per_run` | 60 | Call refused |
| `max_deep_analyses_per_run` | 15 | Deep call refused (triage continues) |
| `max_input_tokens_per_call` | 6,000 | Excerpt is trimmed to fit; a request that still exceeds it is refused |
| `max_documents_per_tender` / `max_pages_per_document` | 3 / 80 | Extra files/pages not parsed (noted) |

A refused call sets the notice to `skipped_budget` with the flag **AI ANALYSIS SKIPPED (budget limit)**;
deterministic scoring still runs and the notice is retried on the next run. Notices are assessed in
descending rule score, so when a budget runs out it is the weakest candidates that wait. Skipped
analysis is never reported as completed.

With the defaults, a run that hits every count cap costs at most ≈$0.03 — the dollar caps are a
backstop against misconfiguration (e.g. someone switching the fast model to a frontier model), not the
expected spend.

## Usage accounting

Every call, cache hit and refusal writes an `ai_usage_events` row: run, opportunity, mode, model,
purpose, input/output/cached tokens (from Ollama `prompt_eval_count`, `eval_count` and
`prompt_eval_cached_count`; estimated and marked `tokens_estimated` when a response omits them),
latency, result (`ok`, `invalid_output`, `cache_hit`, `budget_blocked`, `timeout`, `rate_limited`, `auth_error`, `error`) and
estimated cost. Cost uses the configured price list including cached-input rates; local and fake modes
record zero.

Reports: `chanakya costs --last-days 30` (per model and purpose: calls, cache hits, calls refused by
the budget, tokens, cost), the desk Costs page (`/costs`, same sums from `reporting/insights.py`), the
workbook's Executive Summary and Scan Statistics sheets, and the digest footer. A model missing from
the price list is reported as a coverage note ("cost recorded as 0") rather than silently ignored.

## Tuning guidance

| Goal | Change |
|---|---|
| Spend even less | Narrow `llm.triage_band` (e.g. 0.25 to 0.45); raise `semantic.candidate_threshold`; lower `max_deep_analyses_per_run` |
| Catch more paraphrased notices | Enable local embeddings (`semantic.backend: ollama`); widen the triage band downwards |
| Better deep analysis | Raise `max_input_tokens_per_call` (excerpt grows automatically); keep the deep model |
| Zero paid inference | `llm.mode: local` (Ollama on own hardware) or `disabled` (rules + semantics only; flags say so) |
| Off-peak savings | Ollama lists lower off-peak rates for some models; scheduling the daily run outside 12:00 to 18:00 UTC on weekdays (the default 07:30 IST = 02:00 UTC) already falls off-peak — verify on ollama.com/pricing |

Projected monthly cost by volume: doc 19.
