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
- **Auth**: `/api/acquisition/*` now supports optional bearer-token auth
  (`app.api_token_env`, commit `99fc00d`) — opt-in, so local dev is unaffected until a token is
  configured. The rest of the desk API (`/api/opportunities`, `/api/brief`, etc.) is unchanged and
  still has no auth; extending the same gate there was out of scope for this phase.
- **Postgres parity**: not re-tested this phase (no Postgres instance available in this
  environment); the new migration `08b63b59322d` was only verified against SQLite. Carried over
  from `CRAWLING_RELIABILITY_BACKLOG.md#CRAWL-008`.
- **Demo database migration**: `data/demo/chanakya_demo.db` required `chanakya db upgrade` after
  the new migration was added — found during the real local run, not a code defect, and resolved
  before the local verification above.
- No provider was wired into the CPPP/GePNIC source scanning flow; they remain independent,
  directly-invokable capabilities behind the job manager.
