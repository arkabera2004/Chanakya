# Website Intelligence — Design & Phase Plan

Status: draft, awaiting approval. Source: CHANAKYA priority-development brief (2026-10-30).

## 1. Intent

Add a user-driven Website Intelligence module: a user enters a website URL
(government procurement portal first, company website second), the system
crawls permitted pages within scope, extracts structured tenders or company
facts with source evidence, matches tenders against Wayam's capability book,
and lets the user verify every result against its original source.

CAPTCHA bypass and anti-bot evasion are explicitly out of scope. LinkedIn
automation is out of scope (research/documentation only). This phase is
about reliable extraction and traceability, not feature breadth.

## 2. What already exists (do not rebuild)

The repo audit (2026-10-30) confirms a mature acquisition platform already
covers most of the "crawling infrastructure" ask in the brief:

- **`src/chanakya/acquisition/`** — 9 providers (Scrapling, Crawlee, Crawl4AI,
  Scrapy, Playwright, Firecrawl, Apify, Browserless, Zyte) behind a common
  `AcquisitionProvider` contract (`ProviderCapability`: HTTP_FETCH,
  BROWSER_RENDER, STRUCTURED_EXTRACTION, LLM_EXTRACTION, LINK_DISCOVERY,
  DOCUMENT_DOWNLOAD, MANAGED_CRAWL, ACTOR_EXECUTION). `JobManager` already
  does bounded concurrency, exponential backoff honoring provider
  `retry_after_s`, CAPTCHA/blocked reclassification, redirect-allowlist
  enforcement (SSRF-relevant), orphaned-job recovery, and persists every
  transition as `AcquisitionJobEvent`. **New crawling work is a new
  capability/provider combination on this layer, not a parallel crawler.**
- **`src/chanakya/connectors/`** — government-portal-specific parsers
  (GePNIC, CPPP). Structure-bound to those two portal families; not a
  general "any website" crawler.
- **`src/chanakya/documents/`** — multi-format extraction (PDF incl. OCR
  fallback, DOCX, XLSX, HTML, text, zip) with magic-byte type detection,
  section identification, and citation-tagged context building. Covers the
  brief's document-extraction requirements almost entirely as-is.
- **`src/chanakya/pipeline/`** — the capability-matching/scoring engine
  (`RuleClassifier`, `LexicalSemanticScorer`, `Scorer`, eligibility
  extraction with evidence grounding). This **is** the "Wayam capability
  matching" the brief asks for — reuse it, do not duplicate it.
- **`src/chanakya/llm/`** — budget-guarded LLM access, already enforces
  cost ceilings before every call.
- **DB (`db.py` + Alembic)** — already has `sources`, `scan_runs`,
  `opportunities`/`opportunity_versions`, `opportunity_documents`,
  `eligibility_findings`, `opportunity_capability_matches`,
  `acquisition_jobs`/`acquisition_job_events`. This is schema the brief's
  "website" and "crawl job" concepts should extend, not duplicate.
- **`docs/`** already has 21 numbered architecture docs (06 system arch, 07
  data model, 08 connector design, 10 matching/scoring, 13 security, 19
  cost estimates) plus crawling-specific specs
  (`CHANAKYA_UNIFIED_CRAWLING_FEATURE_INVENTORY.md`,
  `CRAWLING_TARGET_ARCHITECTURE.md`, `CRAWLING_RELIABILITY_BACKLOG.md`).
  No existing doc is titled "website intelligence" — this is genuinely new
  product surface, but it sits on documented foundations. Each new doc
  required by the brief must cross-reference the relevant existing numbered
  doc instead of restating it.

## 3. What's actually new

1. A **`Website`** entity (user-managed: name, base URL, category, crawl
   config, enabled flag) — new table, analogous to but distinct from the
   existing `sources` table (which models known government-portal sources,
   not arbitrary user-added URLs).
2. A **generic same-origin crawler** (sitemap discovery, link discovery,
   pagination, depth/page limits, per-domain concurrency, SSRF-safe URL/IP
   validation) implemented as a new acquisition capability
   (`LINK_DISCOVERY` + `DOCUMENT_DOWNLOAD`) layered on an existing provider
   (Playwright/Crawlee/Scrapling), driving `JobManager` the same way
   existing connectors do.
3. **Tender field extraction with evidence** for arbitrary (non-GePNIC/CPPP)
   procurement sites — a new LLM-assisted extraction step feeding the
   existing `pipeline` evidence/grounding machinery, producing records
   compatible with (or a documented extension of) `TenderRecord`.
4. **Company profile extraction** — a second, differently-shaped record
   type (company facts, not tenders), needing its own store path since
   `JobManager._store_records` currently assumes `TenderRecord`.
5. **Frontend**: new `Website Intelligence` nav section, website CRUD,
   crawl start/pause/resume/cancel, crawl monitoring, discovered
   pages/results views, tender/company detail pages with evidence and
   source links, CSV/XLSX export.
6. **Documentation deliverables**: the 6 docs listed in the brief
   (architecture, user guides ×2, cost report, compliance/limitations,
   implementation log), each written against what is actually built.
7. **Cost report** (`docs/COST_ESTIMATION_REPORT.md`): Azure Container Apps
   hosting options, AI/OCR cost per unit, LinkedIn plan research, 3 usage
   scenarios — a research/analysis deliverable, no code.

## 4. Non-goals / anti-goals

- No CAPTCHA solving, fingerprint evasion, or identity rotation.
- No LinkedIn scraping/automation — research and docs only.
- No crawling outside explicit user-configured scope; no "crawl the whole
  internet" fallback.
- No fabricated extraction values — every field is either evidenced or
  `"Not found in source"`.
- No parallel capability-matching engine — reuse `pipeline/`.
- Company intelligence work does not start until the government crawler
  (phases 1–8) is working and verified end-to-end.

## 5. Phase plan (maps to brief §16, adjusted for reuse)

| Phase | Scope | Key deliverable |
|---|---|---|
| 0 | Repo audit | Done (this doc, §2) |
| 1 | Data model | `websites`, `website_crawl_jobs` tables (or extend `sources`/`scan_runs`); Alembic migration; backend contracts |
| 2 | Crawler | SSRF-safe URL validator, robots.txt + sitemap discovery, same-origin link discovery, pagination, scope/limit config — new acquisition capability on an existing provider |
| 3 | Extraction | HTML/PDF/DOCX extraction via existing `documents/` module; dedupe by hash |
| 4 | Tender extraction | Structured field extraction with evidence excerpts, confidence, extraction method; "Not found in source" for missing fields |
| 5 | Capability matching | Wire extracted tenders into existing `pipeline.Scorer`/`RuleClassifier`; label facts vs rule-match vs AI interpretation distinctly in the API response |
| 6 | Frontend — websites | Website Intelligence nav section; add/edit/remove/categorize website; crawl controls |
| 7 | Frontend — monitoring & verification | Live crawl monitor; discovered pages; tender detail page with source links/evidence; distinguish zero-result reasons |
| 8 | Export & E2E tests | CSV/XLSX export with evidence; fixture-based tests for every item in brief §15 |
| 9 | Company intelligence | New record type + store path; company profile extraction; re-crawl/change detection |
| 10 | Azure architecture + cost report | Compare Container Apps Consumption/Dedicated/Jobs; `docs/COST_ESTIMATION_REPORT.md` with 3 scenarios |
| 11 | Final docs + live verification | Remaining docs; run a real small crawl (1–2 public test sites) end to end; final report per brief §19 |

Each phase ends in small, independently verifiable, separately pushed
commits per the brief's development-discipline rules (test-first RED
commit before its fix, targeted tests after each change, full suite before
moving on).

## 6. Open decisions left to the builder (flagged, not blocking)

- Exact new table names/shape for `websites` vs extending `sources` — will
  follow whichever existing pattern (`sources`/`scan_runs`) proves cleaner
  once phase 1 starts; documented in `07_DATA_MODEL.md` addendum.
- Which existing provider (Playwright/Crawlee/Scrapling) backs the generic
  crawler — decided in phase 2 based on a quick capability fit check, not
  upfront.
- Whether `TenderRecord` gets extended or a sibling `CompanyRecord` type is
  added in phase 9 — decided when phase 9 starts, since phase 1–8 doesn't
  depend on it.

## 7. Approval

This document, plus the phase table in §5, is the plan for the next unit of
work. On approval, Phase 1 starts: data model + migration for `websites`.
