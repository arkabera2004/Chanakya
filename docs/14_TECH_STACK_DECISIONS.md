# 14 — Tech Stack Decisions

Library-by-library evaluation (versions, licences, alternatives): doc 03. This page records the
decisions and the reasons, in ADR form.

## Stack at a glance

| Layer | Choice | Tested with (build environment, 2026-10-10) |
|---|---|---|
| Language / runtime | Python ≥3.12 | CPython 3.13 locally; CI matrix 3.12 + 3.13 |
| HTTP | httpx (sync) | 0.28.1 |
| HTML parsing | BeautifulSoup4 + lxml | 4.15.0 / 6.1.3 |
| Documents | pdfplumber, pypdf, python-docx, openpyxl; optional pytesseract + pypdfium2 | 0.11.10 / 5.9.0 / 1.2.0 / 3.1.5 |
| Models & validation | Pydantic 2 | 2.13.5 |
| Persistence | SQLAlchemy 2 on SQLite (WAL) → PostgreSQL | 2.1.4 |
| Migrations | Alembic; optional PostgreSQL driver psycopg 3 (`chanakya[postgres]`) | 1.20.0 / 3.3.6, against an embedded PostgreSQL 18.4 (not the PostgreSQL 17 container CI uses; see ADR-13) |
| CLI | Typer | 0.27.3 |
| Templates | Jinja2 (autoescape) | 3.1.6 |
| Config | YAML (PyYAML) + environment variables | 6.0.3 |
| LLM | Ollama API over httpx (cloud or local) | API as documented on docs.ollama.com, 2026-10-10 |
| Tests / lint | pytest, respx, reportlab (PDF fixtures), ruff | 9.1.1 / 0.23.1 / 5.0.1 / 0.16 to 0.17 |
| Packaging | Docker (python:3.12-slim), docker compose | Image not built in the sandbox (no Docker daemon) |

## ADR-01 Python modular monolith

**Decision:** one Python package with stage modules; one process per run.
**Why:** a daily batch of a few thousand records; Python has the best PDF/Office/HTML tooling; BD
engineers can read it. **Rejected:** microservices (operational cost, no scaling need), TypeScript
(weaker document tooling; the only open-source tender project found is TypeScript but too small to
reuse). **Revisit when:** more than ~50 sources or multiple writers → per-source workers on Postgres.

## ADR-02 Synchronous, polite HTTP without a crawling framework

**Decision:** `httpx.Client` wrapped in `PoliteClient` (allowlist, robots, per-host interval, retries,
circuit breaker). **Why:** politeness (≥3 s per host), not concurrency, bounds throughput; Scrapy/Crawlee
would add an async runtime and dozens of dependencies to fetch ~30 pages a day. **Rejected:**
Playwright — listings are server-rendered and JS-heavy portals are login/CAPTCHA-gated anyway.

## ADR-03 SQLite first, PostgreSQL ready

**Decision:** SQLAlchemy models with portable types (JSON, naive-UTC DateTime); SQLite with WAL for the
MVP; `CHANAKYA_DATABASE_URL` switches to Postgres. **Why:** zero operations, single file backup.
**Trade-off:** single writer — fine for one scheduled job. **Next:** Alembic before the first schema
change. **Done 2026-10-10:** see ADR-13.

## ADR-04 Deterministic first, LLM last

**Decision:** rules + exclusions + lexical semantics decide most notices; LLM only for the grey band
and for deep extraction from documents; scores computed in code. **Why:** cost (doc 11), testability,
explainability, and immunity to model drift for the ranking itself.

## ADR-05 Ollama provider over raw HTTP, not the SDK

**Decision:** `llm/ollama.py` calls `/api/chat` directly for both cloud (`https://ollama.com`, Bearer
key) and local (`http://localhost:11434`). **Why:** full control of timeouts, retry classification,
usage fields (`prompt_eval_count`, `eval_count`, cached counts) and secret redaction; one fewer
dependency; identical code path for both modes. **Note:** Ollama documents that `format` structured
outputs are not supported for cloud models, so JSON is enforced by Pydantic validation in both modes,
and `format` is sent to local servers only.

## ADR-06 Default models: gpt-oss:20b (fast) and gpt-oss:120b (deep)

**Why:** both are listed on Ollama's cloud price list at low rates ($0.07/$0.30 and $0.15/$0.60 per
million input/output tokens), are open-weight (the same models can run locally later), support a
`think` level to cap reasoning tokens, and handle English procurement text. **Open:** confirm they are
included in the account's plan (`chanakya llm check`), and evaluate Indic-language notices (doc 20).
Models are configuration, not code.

## ADR-07 Lexical semantics by default, embeddings optional

**Decision:** TF-IDF cosine against capability profiles; local Ollama embeddings as a switch.
**Why:** no cloud embedding models are priced by Ollama; lexical matching achieved full funnel recall on
the benchmark at zero cost; avoids a vector database. **Revisit when:** BD reports misses on
paraphrased notices.

## ADR-08 Capability knowledge base as YAML

**Decision:** `config/wayam_capabilities.yaml`, validated on load, versioned by content hash.
**Why:** BD can edit phrases without code; version changes trigger re-scoring automatically;
docs are generated from it (doc 04).

## ADR-09 Excel + email as the primary interface

**Decision:** 8-sheet workbook and daily digest; CLI for operators; no web UI in the MVP.
**Why:** that is how BD already works; zero hosting; a dashboard (FastAPI + simple UI) is Phase 6.

## ADR-10 Scheduling outside the application

**Decision:** `chanakya schedule` exists for simple hosts, but production should use cron/systemd timers,
Kubernetes CronJob or an Azure Container Apps job running `chanakya run --email`. **Why:** the platform
handles restarts, logs and alerting; the app stays a single-shot command with meaningful exit codes.

## ADR-11 Documents through manual import

**Decision:** no automated document download where a CAPTCHA/login protects it; `chanakya import
--tender-id` and an inbox folder instead; deep analysis waits (`deferred_no_documents`).
**Why:** compliance; honesty about what was analysed.

## ADR-12 Security by construction

Allowlisted https fetching, safe archive extraction, injection screening, no model tools, formula
neutralisation and secret redaction live in one module (`security.py`) with dedicated tests (doc 13).

## ADR-13 Alembic migrations, PostgreSQL through an optional psycopg 3 driver

**Decision:** version the schema with Alembic, scripts inside the package (`src/chanakya/migrations/`),
driven by `chanakya db` from `app.database_url`. PostgreSQL is reached with psycopg 3, installed only
on request (`pip install 'chanakya[postgres]'`, Docker `--build-arg INSTALL_POSTGRES=true`).

| Dependency | Where | Licence | Footprint (measured in the project venv, Windows, CPython 3.13) | Pulls in |
|---|---|---|---|---|
| alembic 1.20.0 (`alembic>=1.20,<2`) | default install | MIT | 2.5 MB | Mako 1.4.3 (MIT, 0.8 MB); MarkupSafe and typing-extensions were already installed for Jinja2 and Pydantic |
| psycopg 3.3.6 + psycopg-binary (`psycopg[binary]>=3.2,<4`) | optional extra `postgres` | LGPL-3.0-only | 2.1 MB + 8.1 MB + 6.3 MB of bundled libpq and OpenSSL libraries | tzdata (already a dependency) |

**Why Alembic:** it is the migration tool of the SQLAlchemy project, reads the existing models for
autogenerate, has a batch mode for SQLite's limited `ALTER TABLE`, and can stamp an existing database,
which is how the live SQLite file is adopted without touching data. It is in the default install
because every database Chanakya creates is stamped, SQLite included, and `chanakya db` must work
everywhere. **Rejected:** hand-written `ALTER` scripts keyed on `schema_version` (no autogenerate, no
SQLite table rebuild, one more thing to test); yoyo-migrations (SQL only, no model diff).

**Why inside the package and not a top-level `alembic/`:** the Docker image runs `pip install .` and
copies only `src`, so a top-level directory would not exist in the container that has to run the
migration. Shipping the scripts as package data also removes `alembic.ini` and with it any second place
a database URL could be written.

**Why psycopg 3, and why optional:** it is the driver SQLAlchemy 2 documents for
`postgresql+psycopg://`, which the config example and `.env.example` already named; the binary wheel
needs no compiler or system libpq. It is optional because SQLite users need none of it, and because of
its licence. **Licence note for a human to confirm:** LGPL-3.0 is not AGPL, and the library is used
unmodified through its public API as a separately installed package. Whether that satisfies Wayam's
policy for a proprietary product is a decision for whoever owns licensing, not a fact established here.
The binary wheel also redistributes libpq and OpenSSL; their licences were not checked in this work.
If LGPL is excluded, the candidate fallback is pg8000 (BSD-3-Clause, pure Python,
`postgresql+pg8000://`); `pgsupport.normalize_url` and the `options` handling would need adapting and
it has not been tried. **Rejected:** psycopg2 (older API, same licence family), asyncpg (async only).

**Portability choices** (detail in doc 07): generic `JSON` and not `jsonb`, so stored key order and
therefore output is identical on both databases; string columns unbounded on PostgreSQL because SQLite
never enforced their lengths; explicit `NULLS FIRST/LAST`; sessions pinned to UTC; an advisory lock
behind the writer lock file.

**Verified, and not:** there is no Docker daemon on the build machine, so the suite was run against
an embedded PostgreSQL 18.4 on the build machine (the `pixeltable-pgserver` wheel in a throwaway virtual environment, Windows, database created with `LC_COLLATE 'C'`, server time zone Asia/Calcutta). All 317 tests passed, the migrations built an empty database, the demo run completed end to
end, and a copy of the live SQLite data (26,446 rows) was moved with `chanakya db copy-from` and
compared value for value. Not verified: the CI job `test-postgres` itself (PostgreSQL 17, Linux,
container), the Docker image builds, `pip-audit`, and any managed PostgreSQL service. That embedded
server was a test tool only; it is not a dependency and is not installed in the project environment.

## Runtime footprint

Alembic (with Mako) was added to the default install on 2026-10-10; the dependency list of record is
`pyproject.toml`. No GPU. The demo run (8 notices, one PDF) peaked at ≈112 MB RSS and took
1.6 s; allow ~512 MB for runs that parse large PDFs (document parsing is the peak). A 1 vCPU / 1 to 2 GB VM or container is enough for cloud-LLM mode. Local LLM mode needs a separate
machine sized for the chosen model (doc 19).
