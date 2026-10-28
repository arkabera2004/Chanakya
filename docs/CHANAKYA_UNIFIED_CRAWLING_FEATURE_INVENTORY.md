# Chanakya Unified Crawling — Feature Inventory

Nine acquisition providers behind one shared contract (`src/chanakya/acquisition/contract.py`),
one job manager (`job_manager.py`), one DB schema (`AcquisitionJob`/`AcquisitionJobEvent`,
migration `08b63b59322d`), one API (`desk_acquisition.py`, `/api/acquisition/*`), and one
frontend page (`web/src/app/providers/page.tsx`). Cross-references:
[CRAWLING_RELIABILITY_BASELINE.md](CRAWLING_RELIABILITY_BASELINE.md) (the pre-existing CPPP/GePNIC
connectors, not duplicated here), [CRAWLING_BENCHMARK_SPECIFICATION.md](CRAWLING_BENCHMARK_SPECIFICATION.md).

Status legend: `IMPLEMENTED` (code exists, not yet run) · `TESTED` (passing automated test,
mocked or local) · `LIVE_VERIFIED` (at least one real call against the real provider) ·
`BLOCKED_BY_CREDENTIALS` · `NOT_APPLICABLE_WITH_REASON`.

## Shared foundation

| Feature | Module | Status | Evidence |
|---|---|---|---|
| Provider contract (fetch/validate_config/is_configured, ok/blocked/failed classification) | `acquisition/contract.py` | TESTED | Exercised by every provider's test file |
| Lazy-loading registry (missing package → clear problem, not a crash) | `acquisition/registry.py` | TESTED | `test_acquisition_*` files; live registry-status check during dev showed all 9 reporting correctly |
| Provider settings (`acquisition_providers.*`, `api_key_env` pattern) | `config.py` | TESTED | `pyproject.toml` extras per provider; `settings.secret()` reused, redaction via `register_secret` |
| Job manager (create/submit/cancel/retry, bounded thread pool, event history, orphaned-job recovery on restart) | `acquisition/job_manager.py` | TESTED + LIVE_VERIFIED | 11 unit tests; live-verified end to end via the real Firecrawl provider (job reached `succeeded`); recovery verified with a simulated-crash test (commit `e5c26b4`) |
| DB persistence (`AcquisitionJob`, `AcquisitionJobEvent`) | `db.py`, migration `08b63b59322d` | TESTED | Migration applied cleanly from baseline on a disposable DB; `test_migrations.py`/`test_db_portability.py` unaffected; real demo DB upgraded and exercised via a running server (see below) |
| API (`/api/acquisition/providers`, `/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel`, `/jobs/{id}/retry`, `/analytics`) | `desk_acquisition.py` | TESTED + LIVE_VERIFIED | 11 FastAPI TestClient tests; **also run against a real `chanakya serve` process**; optional bearer-token auth added (`app.api_token_env`, commit `99fc00d`), open by default |
| Frontend (`/providers`) | `web/src/app/providers/page.tsx` | LIVE_VERIFIED | `npm run build` passes; manually exercised in a real Chrome tab against a real running backend — all 9 providers rendered with correct status/reasons, a real job's event history displayed correctly, and (commit `ff3faae`) real operational-analytics figures (succeeded/failed counts, avg/p95 duration) rendered with an honest "no finished job yet" state rather than a fabricated 0 |

**Real local end-to-end run performed during this phase** (not simulated): started
`chanakya serve` against `config/chanakya.demo.yaml` on port 8787, started `next dev` on port
3001 with the existing API proxy, created a job via `curl POST /api/acquisition/jobs`
(`provider_id: scrapling`, disabled → correctly `failed` with a clear reason), confirmed it via
`GET /api/acquisition/jobs`, then loaded `http://localhost:3001/providers` in a real browser tab
and confirmed the same job and all 9 provider statuses rendered from the live backend. This also
surfaced and resolved one real issue: the demo DB was stamped at migration `0001` and needed
`chanakya db upgrade` after the new tables were added — exactly the fail-fast behavior the
migration system is designed to produce, not a defect.

## Per-provider capability status

### 1. Scrapling (`providers/scrapling_provider.py`)
| Capability | Status |
|---|---|
| Structured extraction via `Selector` (CSS) | TESTED (7 tests, mocked HTTP) |
| Retrieval via Chanakya's own `PoliteClient` (SSRF/robots/retry already covered elsewhere) | TESTED |
| Adaptive-selector validation (missing required field → kept for review, never guessed) | TESTED |
| `Fetcher`/`StealthyFetcher` (Scrapling's own retrieval/stealth layer) | `NOT_APPLICABLE_WITH_REASON`: pulls in `curl_cffi` → `patchright` (a stealth Playwright fork built to evade bot detection), which conflicts with this project's no-stealth rule. Documented in the module docstring. |

### 2. Crawlee (`providers/crawlee_provider.py`)
| Capability | Status |
|---|---|
| HTTP crawling via `HttpCrawler` | TESTED (5 tests, real local HttpCrawler against a loopback server) + ad hoc LIVE_VERIFIED (example.com, httpbin.org/404 during dev) |
| Bounded retries (`max_request_retries`) | TESTED |
| Repeated-job storage isolation | TESTED — found and fixed a real bug (shared storage dir bound Crawlee's locks to the first call's event loop; fixed with a per-call isolated `storage_dir`) |
| Session-rotation/blocked-status classification | TESTED — found Crawlee raises `SessionError` (not `HttpStatusCodeError`) for 401/403; classified `blocked` |
| Request queues/prioritization/routing beyond the single-request case, browser crawling (`PlaywrightCrawler`) | NOT_STARTED — out of scope for this phase; the shared contract's single-fetch model doesn't need Crawlee's multi-page orchestration yet |

### 3. Crawl4AI (`providers/crawl4ai_provider.py`)
| Capability | Status |
|---|---|
| HTML-to-Markdown via `AsyncWebCrawler` | TESTED (7 tests, real local crawl against a loopback server) + ad hoc LIVE_VERIFIED (example.com, httpbin.org/404 during dev) |
| SSRF validation before any crawl (reuses `chanakya.security.validate_url`) | TESTED |
| LLM-assisted extraction | `NOT_APPLICABLE_WITH_REASON`: would call out to a model provider independently of `chanakya.llm`'s budget/provider machinery, duplicating rather than reusing it. `validate_config()` reports this explicitly. |
| Hosted Crawl4AI API | NOT_STARTED — no account/key available or requested |

### 4. Scrapy (`providers/scrapy_provider.py` + `_scrapy_worker.py`)
| Capability | Status |
|---|---|
| Single-request crawling via an isolated subprocess `CrawlerProcess` | TESTED (5 tests, real subprocess against a loopback server) + ad hoc LIVE_VERIFIED (example.com during dev) |
| `RobotsTxtMiddleware` respected (not disabled) | IMPLEMENTED — robots-dropped request classified `blocked`, not `failed` |
| Found & fixed: Scrapy 2.19 renamed `start_requests` → async `start()` | TESTED — the old method silently produced zero requests until fixed |
| Full spider/item-pipeline/middleware framework for multi-page crawls | NOT_STARTED — out of scope; this phase only needed single-request acquisition behind the shared contract |

### 5. Playwright (`providers/playwright_provider.py`)
| Capability | Status |
|---|---|
| Local Chromium/Firefox/WebKit navigation | TESTED (5 tests, real headless Chromium against a loopback server) |
| SSRF validation before navigation | TESTED |
| Context/browser cleanup on success and failure (nested `finally`) | IMPLEMENTED, exercised by every test |
| Disabled by default | IMPLEMENTED — no current source needs JS rendering per the crawling benchmark |
| Firefox/WebKit specifically | NOT_STARTED — only Chromium's binary is installed locally; the code path is generic (`getattr(pw, settings.browser)`) but untested for the other two engines |

### 6. Firecrawl (`providers/firecrawl_provider.py`)
| Capability | Status |
|---|---|
| `/v1/scrape` (single-page, markdown) | **LIVE_VERIFIED** — 1 real call during dev (`https://example.com`, 1 credit used), response schema confirmed, not guessed |
| Auth rejection (401/403) → blocked, non-retryable | TESTED (mocked) |
| Rate limit (429) → failed, retryable | TESTED (mocked) |
| Site crawl, URL mapping, batch/async jobs, webhooks | NOT_STARTED — only the single-page scrape endpoint was implemented; the others are documented by Firecrawl but not built this phase |

### 7. Apify (`providers/apify_provider.py`)
| Capability | Status |
|---|---|
| Auth (`GET /v2/users/me`) | **LIVE_VERIFIED** — real call, zero compute cost |
| Actor run start/poll/abort, dataset retrieval | TESTED (mocked only) — running a real Actor costs compute credits; picking an appropriate Actor for procurement acquisition is a product decision not made in this phase |
| 401 → blocked, non-retryable | TESTED (mocked) |

### 8. Browserless (`providers/browserless_provider.py`)
| Capability | Status |
|---|---|
| `/content` (REST, browser-rendered HTML) | **LIVE_VERIFIED** — real call during dev, confirmed end to end through the exact adapter code |
| Auth rejection, rate limit, 5xx classification | TESTED (mocked) |
| WebSocket CDP connect (Playwright `connect()`) | NOT_APPLICABLE_WITH_REASON — attempted during exploration, timed out; the classic `wss://chrome.browserless.io` endpoint appears to have moved to a regional `production-<region>.browserless.io` host. The REST API was used instead since it worked immediately and is equally documented. |

### 9. Zyte (`providers/zyte_provider.py`)
| Capability | Status |
|---|---|
| `/v1/extract` (httpResponseBody / browserHtml modes) | **BLOCKED_BY_CREDENTIALS** — no `ZYTE_API_KEY` was provided; implemented against Zyte's publicly documented schema, 6 tests pass against mocked responses only |
| Auth/rate-limit classification | TESTED (mocked, schema not independently confirmed) |

## Pipeline integration (proven, not just wired)

| Claim | Status | Evidence |
|---|---|---|
| A provider's `TenderRecord`s reach `pipeline.store.upsert_record` | TESTED | `test_acquisition_pipeline_integration.py::test_a_successful_job_stores_a_real_opportunity_with_dedup_key` |
| The same tender re-submitted through two separate jobs does not duplicate | TESTED | `...::test_resubmitting_the_same_tender_does_not_create_a_duplicate_opportunity` — proves the pipeline's own `dedup_key`, not job-level dedup, does the work |
| A partial storage failure is distinguishable from full success | TESTED | `...::test_a_partial_job_is_distinguished_from_a_fully_succeeded_one` — job status `partial`, `diagnostics.stored.errors` accurate |

## Explicit scope boundaries (not gaps — deliberate decisions, recorded here per the brief)

- No provider was wired into the existing CPPP/GePNIC source configuration — those connectors
  remain on their own, already-verified path (`chanakya.connectors`); the new providers are an
  additional, independently-invokable capability, not a replacement.
- No contact discovery, sales prospecting, CRM integration, or multi-tenant feature was added.
- No extraction-review or operational-analytics frontend page was built this phase — the job
  detail panel on `/providers` covers the same diagnostics for now (see commit `21b82b5`).
- No paid Actor run, no Zyte call, no additional Firecrawl/Apify/Browserless calls beyond the
  minimal live-verification calls recorded above were made.
