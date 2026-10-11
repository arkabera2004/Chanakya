# 01 — Executive Summary

**Date:** 2026-10-10 · **Scope:** research, architecture and MVP implementation of Chanakya for Wayam AI.

## What was built

A working, tested Python modular monolith (`src/chanakya`) that:

1. Acquires notices from GeM-CPPP listing pages (central, state and GeM-bid feeds), NIC GePNIC
   portals, and a manual CSV/XLSX/YAML inbox; normalises them to one schema.
2. Deduplicates across portals (Tender ID, reference + organisation, fuzzy title/date match),
   versions every change and records deadline changes, corrigenda, retenders and expiry.
3. Filters with a zero-cost rule engine driven by an editable capability knowledge base
   (`config/wayam_capabilities.yaml`, 21 capabilities) plus free lexical semantics.
4. Calls an LLM (Ollama Cloud or local) only for ambiguous or strong candidates, with validated
   JSON, anti-hallucination checks, caching and hard budget limits.
5. Extracts eligibility requirements deterministically, never assumes Wayam qualifies, scores each
   notice 0 to 100 with an explainable breakdown, and assigns P1 to P4.
6. Produces an 8-sheet Excel workbook and an HTML/plain-text email digest with idempotent delivery.

127 offline tests cover connectors, documents, AI contract, scoring, reporting, email and full
end-to-end runs (including repeat runs, corrigenda, unavailable sources and budget exhaustion).

## Key research findings (verified 2026-10-10)

| Finding | Consequence |
|---|---|
| CPPP "Latest Active Tenders" pages are public, server-rendered HTML, 10 rows per page, newest first; about 29,600 central notices were active. The search form and document downloads sit behind an image CAPTCHA. | CPPP listings are the MVP backbone; search and documents are never automated. |
| CPPP detail links embed a time token and stop working later. | The Tender ID is the stable key; reports cite feed + Tender ID, and detail links are labelled as expiring. |
| GePNIC portals (eprocure, etenders and ~30 states) share one software stack with session-bound links. | One configurable connector covers many portals; documents need manual download. |
| GeM bidplus and IREPS disallow automated access in robots.txt. | Marked *verified restricted*; GeM bids are reached via CPPP's GeM feed or manual import. |
| Ollama Cloud uses Bearer API keys against `https://ollama.com/api`, reports token usage per call, and its docs state structured outputs are not supported in the cloud. | Mode A (cloud) is the default for the provided key; JSON validity is enforced by Pydantic + one repair retry, not by the API. |
| Ollama Cloud bills per million tokens; e.g. gpt-oss:20b $0.07 in / $0.30 out, gpt-oss:120b $0.15 / $0.60 (ollama.com/pricing). No embedding models are priced for cloud. | Fast model gpt-oss:20b, deep model gpt-oss:120b; semantic matching uses free lexical scoring or local embeddings only. |
| Wayam's site documents 20+ accelerators (Arjuna, Vakyam, Wayam Eye, Vedha, Sutradhar, Raksha, Pariksha, Setu, Drishti…), but publishes no government references, certifications, turnover or team size. ANPR, healthcare revenue-cycle and mining offerings are **not** on the site. | Those three are custom-build/low-confidence capabilities; eligibility defaults to *Unknown — human review required*. |

## Results

**Cost benchmark** (64 labelled notices; LLM verdicts simulated at 90% accuracy; see `docs/validation/BENCHMARK.md`):

| Approach | Recall | Precision | LLM calls | Input tokens | Est. cost |
|---|---|---|---|---|---|
| LLM on every notice | 0.83 | 0.83 | 64 deep | 180k | $0.054 |
| Keywords → LLM | 0.83 | 0.96 | 46 deep | 130k | $0.039 |
| **Chanakya funnel** | **0.93** | **1.00** | 11 fast + 3 deep | 9.7k | **$0.0027** |

The funnel passes every labelled-relevant notice to the AI stage (its two misses are simulated LLM
errors), at ~5% of the cost of analysing everything. Repeat runs over unchanged notices make zero
LLM calls (asserted in tests).

**Live validation** (24 real notices observed on CPPP, eprocure, etenders, BECIL, NTPC and Goa ITG on
2026-10-10): 23/24 decisions agreed with our labels. All four relevant notices — a CCTV rate
contract, NTPC REL drone thermography, a SOC/NOC EOI and a media-monitoring RFP — were detected but
had already closed, so Chanakya correctly ranked them P4. The one disagreement was an IT-flavoured
radio-equipment EOI that would cost one cheap triage call. See `docs/validation/LIVE_SNAPSHOT_2026-10-10.md`.

**Projected operating cost** for ~6,000 new notices/day (central + states): about **$0.90 per month**
of Ollama Cloud usage with the funnel at base assumptions (≈$1.8 to 3.6 in pessimistic cases), versus
≈$160/month to send every notice to the deep model. Prompt sizes are measured with the production
prompt builders (`scripts/cost_model.py`, `docs/19_COST_ESTIMATES.md`).

## Recommended MVP sources

1. **CPPP central feed** (`cpppdata`) — verified structure, broadest central coverage.
2. **CPPP state feed** (`mmpdata`) — same template; validate with `chanakya sources test`.
3. **Manual import** — GeM bids, IREPS, PSU sites (BECIL, NTPC) and all tender documents.

GePNIC connectors are included (disabled by default) for detail-page enrichment of tender value,
EMD, category and description.

## What remains before production

- Run `chanakya sources test` from the deployment network to confirm live parsing (the build
  sandbox could not reach the portals); confirm `mmpdata`/`gemdata` table layouts.
- Run `chanakya llm check` with the real key; confirm model names and whether the account's plan
  includes them.
- Fill `company_profile` with verified Wayam credentials so eligibility can move beyond *Unknown*.
- Two weeks of shadow runs with BD feedback (`chanakya review`) to calibrate weights and thresholds.
- Build and push the Docker image (not possible in the build environment).

Full list: `docs/20_RISKS_AND_OPEN_QUESTIONS.md`.
