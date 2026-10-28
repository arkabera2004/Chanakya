# Crawling Target Architecture — As Built

This supersedes the "proposed minimal architecture" draft from the earlier reliability phase
(never separately published) with what was actually implemented. See
[CHANAKYA_UNIFIED_CRAWLING_FEATURE_INVENTORY.md](CHANAKYA_UNIFIED_CRAWLING_FEATURE_INVENTORY.md)
for per-capability verification status.

```
Frontend (/providers)
      |  GET/POST  /api/acquisition/*
      v
FastAPI router (desk_acquisition.py)
      |
      v
JobManager (acquisition/job_manager.py)
  - creates/persists AcquisitionJob + AcquisitionJobEvent rows
  - runs each job on a bounded daemon-thread pool (never blocks the request)
  - retries (bounded, only when the provider says retryable), cancellation (cooperative)
      |
      v
ProviderRegistry (acquisition/registry.py)  -- lazy-loads 9 adapters
      |
      +-- scrapling / crawlee / crawl4ai / scrapy / playwright   (local)
      +-- firecrawl / apify / browserless / zyte                 (managed, api_key_env)
      |
      v
AcquisitionResult (ok | blocked | failed, never conflated)
      |
      v
pipeline.store.upsert_record()  <-- same dedup/provenance path as CPPP/GePNIC connectors
      |
      v
opportunities / raw_opportunities tables (unchanged schema)
```

## Design decisions and why

- **One contract, nine adapters, zero duplicate pipelines.** Every adapter returns the same
  `AcquisitionResult` shape; normalization/dedup/provenance stays in `pipeline.store`, reused
  rather than re-implemented per provider (see §6.2 of the original brief).
- **Job manager owns concurrency, not FastAPI.** A `POST /api/acquisition/jobs` returns as soon
  as the job row is created and handed to a background thread — the HTTP request never waits on
  a provider's network call, satisfying "long-running crawling must not block normal FastAPI
  request handling."
- **Local vs. subprocess vs. thread, chosen per library's actual constraints, not uniformly:**
  - Scrapling/Playwright/Crawl4AI: run in the job manager's own thread (Playwright/Crawl4AI use
    `asyncio.run()` internally; each gets a fresh event loop per call).
  - Crawlee: also `asyncio.run()`, but with an **isolated storage_dir per call** — its default
    file-system request queue binds locks to the event loop that created it, which broke on a
    second call with a shared default storage dir (found and fixed this phase).
  - Scrapy: a full **subprocess** per call — Twisted's reactor can only start once per process,
    so reusing the same process for a second Scrapy job would raise `ReactorNotRestartable`.
  - Firecrawl/Apify/Browserless/Zyte: plain synchronous `httpx` calls — no event loop needed.
- **Settings-driven, not infrastructure-driven.** No new queue, no new database, no microservice.
  `AcquisitionJob`/`AcquisitionJobEvent` are ordinary tables in the existing SQLite/Postgres
  schema, migrated the same way every other table is.
- **Credentials never touch the DB or logs.** `settings.secret()` is the only way a provider
  reads its API key, which routes through `obs.register_secret` for log redaction — the same
  mechanism `llm.api_key_env` already used.

## What this architecture deliberately does not do

- It does not give any provider a second normalization path — a provider that returns
  `TenderRecord`s goes through `pipeline.store.upsert_record`, same as CPPP/GePNIC.
- It does not add a distributed task queue (Celery/RQ/etc.) — the bounded thread pool plus DB
  persistence is sufficient at this project's volume, consistent with the rule against
  introducing infrastructure without a demonstrated requirement.
- It does not wire any of the 9 providers into the CPPP/GePNIC source-scanning flow
  (`SourceConfig`/`TenderSourceConnector`) — that remains a separate, already-verified path.

## Additions since the first version of this document

- **Orphaned-job recovery** (`JobManager.recover_orphaned_jobs`, commit `e5c26b4`): a job stuck
  at `queued`/`running` when the process stops (crash, deploy, restart) has no live thread behind
  it. The manager now marks every such row `failed`+`retryable` on its own construction, so a
  restarted process doesn't leave jobs stuck forever and `retry()` can create a fresh one.
- **Optional auth** (`app.api_token_env`, commit `99fc00d`): a single FastAPI dependency gates
  every `/api/acquisition/*` route when a token env var is configured; unset (the default)
  preserves today's open-localhost desk behavior. This does not extend to the rest of the desk
  API (`/api/opportunities`, `/api/brief`, ...), which remains unauthenticated — a decision for a
  future phase if the desk is ever exposed beyond localhost.
