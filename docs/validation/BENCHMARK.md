Dataset: 64 notices (29 labelled relevant). Simulated LLM accuracy: 90%. Models priced as fast=gpt-oss:20b, deep=gpt-oss:120b.

| Approach | Precision | Recall | F1 | Fast calls | Deep calls | Input tokens | Output tokens | Est. cost (USD) | Est. LLM latency (s) |
|---|---|---|---|---|---|---|---|---|---|
| llm_all | 0.83 | 0.83 | 0.83 | 0 | 64 | 180,258 | 44,800 | 0.0539 | 384 |
| keyword_llm | 0.96 | 0.83 | 0.89 | 0 | 46 | 129,567 | 32,200 | 0.0388 | 276 |
| chanakya | 1.00 | 0.93 | 0.96 | 11 | 3 | 9,689 | 3,860 | 0.0027 | 34 |

Chanakya funnel uses 5.4% of the input tokens and 5.0% of the cost of analysing every notice. Repeat runs over unchanged notices make no LLM calls (content-hash cache; asserted in tests/test_reporting_e2e.py::test_full_run_then_incremental_runs).

Missed by llm_all: Centralized Rate Contract for Drone Thermography for NREL Solar Projec; Intelligent system to automatically detect unattended baggage and over; Cyber security and compliance monitoring for hospital information syst; Demand forecasting and inventory optimisation of essential drugs for m; Solution to automatically count visitors and measure dwell time in mus

Missed by keyword_llm: Centralized Rate Contract for Drone Thermography for NREL Solar Projec; Intelligent system to automatically detect unattended baggage and over; Cyber security and compliance monitoring for hospital information syst; Demand forecasting and inventory optimisation of essential drugs for m; Solution to automatically count visitors and measure dwell time in mus

Missed by chanakya: Intelligent system to automatically detect unattended baggage and over; Cyber security and compliance monitoring for hospital information syst

Assumptions: output tokens per call triage≈160, deep≈700; latency triage≈1.5 s, deep≈6 s; LLM verdicts simulated (same per-notice error pattern for every approach). In the funnel, STRONG rule matches are accepted without an LLM verdict, which is why its precision can exceed the simulated LLM's; deep calls happen only with ≥200 chars of description/document context.