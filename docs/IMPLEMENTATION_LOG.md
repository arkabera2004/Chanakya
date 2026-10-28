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
- **Auth: EXTENDED to the whole desk API (commit `7065002`).** `app.api_token_env` (opt-in,
  unset by default — local dev unaffected) now gates every route via a single app-wide FastAPI
  dependency, not just `/api/acquisition/*`. `/api/health` stays explicitly exempt. Live-verified
  against a real running server: 200/401/401/200 for no-header/wrong-token/missing-header/
  correct-token respectively. Still not done: no distinct "production mode" concept exists in
  `config.py` that would make the app refuse to start without a token configured — a token is
  opt-in, not enforced, in any environment. That would require introducing an environment
  concept this project doesn't currently have, which is a product decision, not made here.
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
