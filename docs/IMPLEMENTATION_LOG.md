# Implementation Log — Unified Crawling Platform

Chronological record of the commits in the "unified crawling" phases (config foundation through
frontend). Earlier phases (baseline repair, crawling-reliability benchmark) are logged in
[CRAWLING_RELIABILITY_BACKLOG.md](CRAWLING_RELIABILITY_BACKLOG.md). All commits below are on
`main` at `https://github.com/arkabera2004/Chanakya.git`; none were pushed to
`https://github.com/mrigankad/Chanakya.git` (its push URL is disabled locally as a safety rail).

| Date | Commit | Change | Files | Tests |
|---|---|---|---|---|
| 2026-10-28 | `8718ce9` | Acquisition provider settings for all 9 integrations | `config.py`, `.env.example` | Full suite green |
| 2026-10-28 | `481bb22` | Shared provider contract, registry, Scrapling adapter | `acquisition/` (new package) | 7 new, full suite green |
| 2026-10-28 | `49105a8` | Playwright adapter | `providers/playwright_provider.py` | 5 new (real Chromium, loopback server) |
| 2026-10-28 | `e293f4e` | Firecrawl adapter | `providers/firecrawl_provider.py` | 6 new (mocked) + 1 real call |
| 2026-10-28 | `ae8ef74` | Apify adapter | `providers/apify_provider.py` | 7 new (mocked) + 1 real auth call |
| 2026-10-28 | `8d0dfb9` | Browserless adapter | `providers/browserless_provider.py` | 6 new (1 real + 5 mocked) |
| 2026-10-28 | `688144a` | Zyte adapter (blocked by credentials) | `providers/zyte_provider.py` | 6 new (mocked only) |
| 2026-10-28 | `9024493` | Crawlee adapter | `providers/crawlee_provider.py` | 5 new (real local, loopback server) |
| 2026-10-28 | `b8d766e` | Crawl4AI adapter | `providers/crawl4ai_provider.py` | 7 new (real local, loopback server) |
| 2026-10-28 | `12cf41e` | Scrapy adapter (subprocess-isolated) — 9th and final adapter | `providers/scrapy_provider.py`, `_scrapy_worker.py` | 5 new (real subprocess, loopback server) |
| 2026-10-28 | `b12f154` | Gitignore Crawlee's local storage artifact | `.gitignore` | n/a (housekeeping) |
| 2026-10-28 | `a3ef71d` | `AcquisitionJob`/`AcquisitionJobEvent` DB tables + migration | `db.py`, migration `08b63b59322d` | `test_migrations.py`/`test_db_portability.py` unaffected; migration verified on a disposable DB |
| 2026-10-28 | `cf70d81` | Unified job manager | `acquisition/job_manager.py` | 9 new; live-verified via real Firecrawl call |
| 2026-10-28 | `93ed13c` | `/api/acquisition/*` FastAPI endpoints | `desk_acquisition.py`, `server.py` | 7 new (TestClient) |
| 2026-10-28 | `21b82b5` | `/providers` frontend page | `web/src/app/providers/page.tsx`, `api-acquisition.ts`, `shell.tsx` | `npm run build` clean; manually verified against a real running backend in a real browser |
| 2026-10-28 | `247e3e2` | Feature inventory, architecture, tool evaluation, log (first versions) | `docs/*.md` | n/a |
| 2026-10-28 | `99fc00d` | Optional bearer-token auth for `/api/acquisition/*` | `config.py`, `desk_acquisition.py` | 3 new |
| 2026-10-28 | `e5c26b4` | Orphaned queued/running job recovery on manager startup | `job_manager.py` | 2 new (simulated crash + no-op case) |
| 2026-10-28 | `d18e9ed` | Pipeline integration tests (dedup, provenance, partial status) | `test_acquisition_pipeline_integration.py` | 3 new |
| 2026-10-28 | `31f8039` | `/api/acquisition/analytics` endpoint | `desk_acquisition.py` | 2 new |
| 2026-10-28 | `ff3faae` | Real operational-analytics panel on `/providers` | `providers/page.tsx`, `api-acquisition.ts` | `npm run build` clean; verified in a real browser against real backend data |
| 2026-10-28 | `f1edb60` | Docs: reflect auth, recovery, analytics, integration tests | `docs/*.md` | n/a |
| 2026-10-28 | `744711a` | Postgres parity: fixed 2 test files bypassing it, live-verified | `test_acquisition_job_manager.py`, `test_acquisition_pipeline_integration.py` | Full suite against real Postgres 17: 411 passed / 2 skipped / 0 failed |
| 2026-10-28 | `b4d4c73` | Docs: record Postgres parity closure | `docs/*.md` | n/a |
| 2026-10-29 | `7065002` | App-wide bearer-token auth (was acquisition-only) | `server.py`, `desk_acquisition.py` | 4 new; live-verified against a real running server |
| 2026-10-29 | `c3808cc` | Configurable CORS origins (`app.cors_origins`) | `config.py`, `server.py` | 1 new, real CORS-header assertion |
| 2026-10-29 | `8cc753f` | Production-mode startup fails fast without an auth token | `config.py`, `server.py` | 5 new; live-verified against a real server (crash on missing token, 200/401/200 with one configured) |
| 2026-10-29 | `d885e17` | **FIXED**: root-caused and closed the intermittent `test_followup_alerts` failure | `tests/conftest.py`, `tests/test_followup_alerts.py` | 5× consecutive full-suite runs: 0 failures (previously ~1/7-8); also verified against real Postgres 17 |
| 2026-10-29 | `ba30369` | Shared selector-based HTML→TenderRecord extraction helper | `acquisition/extraction.py` | 4 new |
| 2026-10-29 | `3d03d0f` | Scrapling refactored onto the shared helper | `scrapling_provider.py` | 0 new; all 7 existing tests pass unchanged (zero behavior change) |
| 2026-10-29 | `ec37d2e` | Crawlee wired to real TenderRecord extraction | `crawlee_provider.py` | 1 new, real HttpCrawler run |
| 2026-10-29 | `2606ecd` | Scrapy wired to real TenderRecord extraction | `scrapy_provider.py` | 1 new, real subprocess run |
| 2026-10-29 | `6a9860a` | Playwright wired to real TenderRecord extraction | `playwright_provider.py` | 1 new, real headless Chromium |
| 2026-10-29 | `3a14ff7` | Browserless wired to real TenderRecord extraction | `browserless_provider.py` | 1 new, mocked HTTP |
| 2026-10-29 | `1b95924` | Zyte wired to real TenderRecord extraction (6th and final provider) | `zyte_provider.py` | 1 new, mocked HTTP |
| 2026-10-29 | `112e019` | Full-jitter retry backoff in the job manager | `job_manager.py` | 1 new |
| 2026-10-29 | `5eda34b` | `extract_by_labels()`: deterministic Markdown field extraction | `acquisition/extraction.py` | 10 new (4 prior + 6 new) |
| 2026-10-29 | `0f4f741` | Crawl4AI wired to real TenderRecord extraction | `crawl4ai_provider.py` | 1 new, real AsyncWebCrawler run |
| 2026-10-29 | `4e9b5a3` | Firecrawl wired to real TenderRecord extraction — 9th and final provider | `firecrawl_provider.py` | 1 new, mocked HTTP |

## Website Intelligence — Phase 1 (data model, CRUD API, frontend client)

Design: [superpowers/specs/2026-10-30-website-intelligence-design.md](superpowers/specs/2026-10-30-website-intelligence-design.md).
Plan: [superpowers/plans/2026-10-30-website-intelligence-phase1-data-model.md](superpowers/plans/2026-10-30-website-intelligence-phase1-data-model.md).
Reuses the existing acquisition/pipeline/document-processing layers (see the design doc §2) —
no new crawler, matching engine, or document parser in this phase.

| Date | Commit | Change | Files | Tests |
|---|---|---|---|---|
| 2026-10-30 | `d1bfc6e` | Design spec + Phase 1 implementation plan | `docs/superpowers/` | — (docs only) |
| 2026-10-30 | `aaef7cb` | RED: reproduce missing `Website`/`WebsiteCrawlJob` models | `tests/test_db_website_models.py` | 1 new, failing (expected) |
| 2026-10-30 | `faf3db0` | `Website` and `WebsiteCrawlJob` models + Alembic migration | `db.py`, `migrations/versions/bceafae7b6e2_*.py` | 1 new passing; `test_migrations.py` baseline-equivalence green |
| 2026-10-30 | `f24362f` | RED: reproduce missing website CRUD/crawl-job API | `tests/test_desk_websites_api.py` | 15 new, failing (expected) |
| 2026-10-30 | `5755071` | Website CRUD + crawl-job lifecycle API (create/list/get/patch/delete, cancel/pause/resume) | `desk_websites.py`, `server.py` | 15 new passing; full suite green |
| 2026-10-30 | `aece380` | Frontend API client (`websitesApi`) | `web/src/lib/api-websites.ts` | `tsc --noEmit` clean |

Note: crawl-job lifecycle endpoints in this phase only flip `status`/`cancel_requested`/
`pause_requested` columns — there is no executor yet. Phase 2 wires an actual crawler onto
these jobs.

Operational note: mid-phase, the development machine's disk filled to 127MB free, causing
unrelated `test_obs.py` SQLite I/O failures (confirmed unrelated to this phase's changes by
re-running against a clean working tree). Resolved by deleting Docker Desktop's unused 27GB VM
disk image (`~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw`, with explicit
user confirmation) — not a code or test defect.

## Website Intelligence — Phase 2 (generic URL-driven crawler)

A new `chanakya.webcrawl` package, separate from `connectors/` (government-portal-specific)
and `acquisition/` (provider-backed): crawls an arbitrary user-added website within an
explicit same-origin scope. Reuses `security.validate_url` for SSRF protection rather than
reimplementing it.

| Date | Commit | Change | Files | Tests |
|---|---|---|---|---|
| 2026-10-30 | `66f5d62` | RED: reproduce missing webcrawl module | `tests/test_webcrawl_crawler.py` | 8 new, failing (expected) |
| 2026-10-30 | `61bfdf9` | Crawler core: robots.txt, sitemap discovery, same-origin link discovery, page/depth limits | `webcrawl/crawler.py` | 8 new passing |
| 2026-10-30 | `1998b72` | RED: reproduce missing cancel hook | `tests/test_webcrawl_crawler.py` | 1 new, failing (expected) |
| 2026-10-30 | `16fe1b8` | `should_stop` hook so a crawl can be cancelled/paused mid-run | `webcrawl/crawler.py` | 1 new passing |
| 2026-10-30 | `fd254ed` | RED: reproduce missing `WebsitePage` model | `tests/test_db_website_page_model.py` | 1 new, failing (expected) |
| 2026-10-30 | `d5f5c1b` | `WebsitePage` model + migration (one row per discovered URL per crawl job) | `db.py`, `migrations/versions/8b0a76f20354_*.py` | 1 new passing; migration-equivalence green |
| 2026-10-30 | `519ce2d` | RED: reproduce missing crawl job runner | `tests/test_webcrawl_runner.py` | 3 new, failing (expected) |
| 2026-10-30 | `6224d3c` | Runner: executes a crawl job, persists pages, updates job counters/status | `webcrawl/runner.py` | 3 new passing |
| 2026-10-30 | `84bc892` | RED: reproduce missing start/pages endpoints | `tests/test_desk_websites_api.py` | 2 new, failing (expected) |
| 2026-10-30 | `7214fc6` | `/start` (runs the crawl) and `/pages` (discovered-page list) endpoints | `desk_websites.py` | 2 new passing; full suite green |
| 2026-10-30 | `30cf52d` | Frontend client: `startCrawlJob`, `listCrawlJobPages` | `web/src/lib/api-websites.ts` | `tsc --noEmit` clean |

End-to-end at this point: add a website → create a crawl job → `/start` → a real (mocked-in-test)
crawl runs respecting robots.txt/sitemap/same-origin scope/page+depth limits → discovered pages
persisted and queryable, with the website's `last_crawl_status` updated.

Known shortcut (documented in `desk_websites.start_crawl_job`'s docstring): `/start` runs the
crawl synchronously within the request. Fine for the bounded page counts this phase targets;
upgrading to a background executor (mirroring `acquisition/job_manager.py`'s thread pool) is
deferred until crawl sizes actually need it.

Not yet done (Phase 3+): the crawler discovers and fetches HTML pages but does not yet persist
extracted text, download linked PDF/DOC/DOCX documents, or extract structured tender fields.
`chanakya.documents.extract_document` already exists and is the intended reuse target for that
work — not a new parser.

## RESOLVED: the `test_followup_alerts.py` intermittent failure (was "known defect, not fixed")

**Root cause, confirmed by direct reproduction, not guessed:** `tests/conftest.py::ist()` called
`now_ist()` fresh on every invocation. `test_a_run_sends_the_follow_up_alerts_only_when_they_are_switched_on`
calls `sample_records()` (via `rescan()`) four times; `ist()`'s format
(`"%d-%b-%Y %I:%M %p"`) has no seconds, so two calls straddling a real wall-clock minute
boundary produced HELPLINE's `closing_at` a minute apart. `diff_changes` correctly treats that as
a genuine `deadline_changed` event (`ALERT_CHANGE_TYPES` includes it), competing for delivery
alongside the test's real `emd_inr` change and causing the extra sends on the final rescan.

**This was a test-fixture determinism bug, not a defect in `pipeline/store.py` or
`reporting/followups.py`** — their change-detection and idempotency logic were already correct.
Proven two ways: (1) forcing a one-minute jump between two `rescan()` calls reproduced the exact
"4 alerts instead of 0" failure on demand; (2) after the fix, the same forced-drift scenario
(now advancing a full minute on *every* `now_ist()` call throughout the whole test) passes.

**Fix** (commit `d885e17`): a new `tests/conftest.py::_freeze_relative_dates` autouse fixture
captures one reference instant per test before the test body runs; `ist()` uses that frozen
instant instead of calling `now_ist()` again, so repeated fixture-date generation within one test
is always identical regardless of real elapsed wall-clock time.

**Regression coverage kept permanently:**
`test_repeated_rescans_are_stable_across_real_clock_drift` in `test_followup_alerts.py` — not a
throwaway repro, it stays in the suite forcing aggressive clock drift and asserting the original
bug's exact assertions still hold.

**Verification:** `test_followup_alerts.py` 13/13 (was 12 — new regression test). Full suite run 5
times consecutively: 418 passed / 5 skipped / 0 failed, every time. Also run against a real
`postgres:17-alpine` container: 421 passed / 2 skipped (unrelated) / 0 failed.

## Outstanding limitations (honest, as of the last commit above)

- **Zyte**: no credentials provided; adapter exists and is tested against the documented schema
  only, never against a real response.
- **Apify Actor execution**: auth is live-verified; Actor execution itself is mocked-only (running
  a real Actor costs compute credits and needs a product decision on which Actor to use).
- **Crawlee/Crawl4AI/Scrapy**: implemented and tested for the single-request case the shared
  contract needs; their multi-page orchestration/spider frameworks were not built (out of scope —
  no current source needs them; CPPP/GePNIC already have dedicated, working connectors).
- **Frontend**: one page (`/providers`) covers provider status + job dashboard + job detail +
  operational analytics (real data, `/api/acquisition/analytics`). A dedicated extraction-review
  screen (per-record evidence/confidence review, distinct from the job-level diagnostics panel)
  was still not built — no acquisition-sourced records with meaningful confidence/evidence data
  exist yet to review in this session's test runs, so building that screen now would risk a
  "page with nothing real to show" rather than a verified feature.
- **Auth: EXTENDED to the whole desk API (commit `7065002`)**, then **production-mode-enforced
  (commit `8cc753f`)**. `app.api_token_env` (opt-in, unset by default — local dev unaffected)
  gates every route via a single app-wide FastAPI dependency; `app.environment=production`
  (default `development`) now makes `create_app()` refuse to start if the token isn't
  resolvable, closing the "could accidentally run open in production" gap. Both live-verified
  against real running servers. Remaining gap: nothing automatically sets
  `environment=production` in any deployment config (Dockerfile/docker-compose) — an operator
  must set it explicitly; this was a deliberate choice to avoid breaking the documented local
  Docker workflow, not an oversight, but worth revisiting if/when there's a real deployment target.
- **CORS: now configurable** (`app.cors_origins`, commit `c3808cc`, default unchanged). Real
  CORS-header behavior verified by test, not just that the list is stored.
- **Postgres parity: CLOSED this phase.** Docker was available but not running; started it
  (`open -a Docker`), ran a disposable `postgres:17-alpine` container
  (`chanakya-pg-test`, not part of CI, torn down after verification), installed the
  project's `postgres` extra (`psycopg`), and ran the full suite with
  `CHANAKYA_TEST_DATABASE_URL` pointed at it: **411 passed, 2 skipped (unrelated — gated on a
  local `config/chanakya.yaml`), 0 failed** (commit `744711a`). Found and fixed a real gap in
  the process: `test_acquisition_job_manager.py` and
  `test_acquisition_pipeline_integration.py` hardcoded SQLite, bypassing
  `tests/dbsupport.py::isolated_database` entirely — so the acquisition job manager and the
  dedup-integration tests had never actually run against Postgres even when the env var was
  set. Fixed; both files now use the shared fixture like every other test file. SQLite-default
  run reconfirmed unaffected (408/5/0, unchanged). Not yet run in CI against Postgres as a
  standing job — this was a one-off manual verification; promoting it to always run in CI
  (rather than the current separate `test-postgres` job) remains a decision, not done here.
- **Demo database migration**: `data/demo/chanakya_demo.db` required `chanakya db upgrade` after
  the new migration was added — found during the real local run, not a code defect, and resolved
  before the local verification above.
- No provider was wired into the CPPP/GePNIC source scanning flow; they remain independent,
  directly-invokable capabilities behind the job manager.
- **Phase 3A CLOSED**: all 9 providers now produce real `TenderRecord`s. Crawl4AI/Firecrawl
  needed a new strategy (`extract_by_labels`, label-based Markdown parsing) since their output
  has no DOM for the existing CSS-selector helper; see the feature inventory for detail. Still
  single-page/single-request extraction only — no multi-page pagination/pipeline, no LLM-assisted
  extraction, matching every other provider's documented scope.
- **CPPP live API investigation (2026-10-29)**: following up the earlier "portal migrated to a
  JS SPA" finding, investigated via ordinary browser navigation (read-only, no CAPTCHA
  solving/bypass, no secrets recorded) whether the SPA's own `/cpppapi` REST API could be used
  instead. Finding: the listing endpoint itself (`getCpppCentralTenderGried`) now requires a
  solved CAPTCHA to return non-empty results — a stricter access policy than the old portal,
  where only a separate, unused search form had one. Full endpoint-by-endpoint evidence log in
  docs/20_RISKS_AND_OPEN_QUESTIONS.md. No connector code change: this is a portal access-policy
  restriction, not a parsing defect, and this project does not bypass CAPTCHAs. `cppp_central`
  stays `unsupported`. No commit changes connector *behavior* — only documentation/docstrings
  record the investigation's conclusion.
