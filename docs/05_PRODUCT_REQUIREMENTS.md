# 05 — Product Requirements

## Problem
Wayam's business-development team cannot read the ~29,600 active central notices on CPPP (plus state
portals, GeM and PSU sites) to find the handful that fit its AI, computer-vision, voice, security and
data offerings. Keyword alerts drown them in road works that mention "monitoring"; manual review misses
opportunities worded differently ("count visitors using existing cameras").

## Users
- **BD lead** — reads the daily digest, shortlists, decides go/no-go.
- **Solution architect** — reviews scope fit and technical risks for shortlisted notices.
- **Operator** (engineering) — configures sources, budgets, models, schedules; watches health and cost.

## Goals and measures
| Goal | Measure | MVP target |
|---|---|---|
| Find relevant opportunities | Recall on a labelled set (funnel stage) | ≥ 0.9 (benchmark: 1.0 at funnel stage, 0.93 end-to-end with simulated LLM) |
| Don't waste BD time | Precision of P1/P2 | ≥ 0.7 after calibration |
| Minimal paid inference | LLM cost per month | ≤ $5 at 6,000 notices/day (projected ≈ $1) |
| Never mislead | Invented deadlines/values/eligibility | Zero — enforced by tests |
| Timeliness | Notice to digest | Same day (daily run 07:30 IST) |

## Functional requirements
1. **Acquisition** — pluggable connectors (`discover`, `fetch_listing`, `fetch_detail`, `fetch_documents`,
   `normalize`, `check_updates`, `health_check`); incremental by high-water mark; manual CSV/XLSX/YAML
   import; document attachment by Tender ID.
2. **Normalisation** — one canonical record (IDs, title, organisation chain, state, type, category,
   dates in IST, value, EMD, URLs, documents, corrigenda, status, evidence line).
3. **Deduplication & history** — identity rules (doc 07), cross-portal merge, versions, change events
   (new, deadline, value, corrigendum, status, retender, document, details), automatic expiry.
4. **Deterministic classification** — capability scoring, exclusions, equipment-supply detection,
   high-recall net for technology categories and EOI/RFP types.
5. **Semantic matching** — free lexical TF-IDF by default; local embeddings optional; cached.
6. **Documents** — native text first, optional OCR, safe archives, sections with page citations,
   bounded excerpts; manual review flag when extraction quality is low.
7. **AI analysis** — two-stage (fast triage in the grey band; deep analysis for strong/confirmed notices
   with document context), JSON validated, hallucination checks, cached, budget-limited.
8. **Eligibility** — deterministic extraction (turnover, EMD, experience, certifications, OEM
   authorisation, consortium, MSE exemption) assessed only against the configured company profile;
   five statuses; explicit vs inferred vs missing kept apart.
9. **Scoring** — 0 to 100 with six weighted components and written reasons; P1 to P4; flags.
10. **Reporting** — 8-sheet workbook (doc 12); HTML + text digest; idempotent email; dry run.
11. **Operations** — one-command run, scheduler, health check, cost report, exit codes, run statistics.

## Non-functional requirements
- Python 3.12+, single process, SQLite (MVP) or PostgreSQL; runs on a 1 vCPU / 1 to 2 GB VM; no GPU.
- All source content is untrusted (doc 13). No CAPTCHA/login bypass. robots.txt honoured.
- Every source-derived claim carries provenance (feed + Tender ID + capture time, or document + page).
- Timestamps stored in UTC; displayed in IST.
- Failures isolated per source; partial runs still produce reports with coverage notes.

## Out of scope (MVP)
Automatic bid submission or contacting officials (never); web dashboard (Phase 6); multi-user auth;
GeM/IREPS automation (restricted); OCR for Indic scripts beyond what tesseract language packs provide.

## Acceptance criteria status
| # | Criterion | Status |
|---|---|---|
| 1 | ≥2 connectors functional or compliant alternative | CPPP + manual import implemented and tested; GePNIC implemented. **Live CPPP run pending** (sandbox network) |
| 2 | Single command | `chanakya run` ✔ |
| 3 | Normalised, deduplicated, persisted | ✔ (tests) |
| 4 | Wayam-specific matching | ✔ 21-capability KB |
| 5 | Paid inference skipped when not needed | ✔ (rejected/LOW/expired never reach LLM — asserted) |
| 6 | Eligibility without invented credentials | ✔ |
| 7 | Populated, readable Excel | ✔ |
| 8 | Delivery via test email provider | ✔ file provider; SMTP mocked |
| 9 | Repeat runs avoid reprocessing | ✔ (0 assessments, 0 LLM calls — asserted) |
| 10 | Deadline and document change detection | ✔ |
| 11 | LLM usage and cost recorded | ✔ per call |
| 12 | Budget enforced | ✔ per run / day / calls / tokens / deep analyses |
| 13 | Unavailable sources handled | ✔ partial status, no corruption |
| 14 | Tests, docs, Docker, env templates | ✔ (image not built here) |
| 15 | Claims linked to evidence | ✔ provenance + page citations |
