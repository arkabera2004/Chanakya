# 07 — Data Model

Implemented in `src/chanakya/db.py` (SQLAlchemy 2.0). All `DateTime` columns are **naive UTC**;
display converts to IST. JSON columns work on SQLite and PostgreSQL.

## Tables

| Table | Purpose | Key columns |
|---|---|---|
| `sources` | Configured portals and their health | `id`, `type`, `verification_status`, `high_water_mark`, `consecutive_failures`, `cooldown_until`, `health`, `last_error` |
| `scan_runs` | One row per run | `id` (`run_YYYYmmddTHHMMSS_xxxx`, IST), `status` ok/partial/failed, `trigger`, `dry_run`, `stats` JSON |
| `source_scan_runs` | Per-source outcome of a run | pages, seen, new, updated, unchanged, duplicates, malformed, duration, error, coverage note |
| `raw_opportunities` | Immutable raw payloads (one per distinct content) | unique (`source_id`, `source_record_key`, `content_hash`) |
| `opportunities` | Canonical, deduplicated record + latest assessment | `id` (`CHK-` + hash of dedup key, stable), `dedup_key` unique, identity, dates, value, EMD, URLs, `document_refs`, `status`, `evidence`, `also_seen_on`, `content_hash`, `version`; assessment: `filter_decision`, `rule_score`, `semantic_score`, `top_capabilities`, `analysis_state`, `relevance_score`, `priority`, `eligibility_status`, `data_completeness`, `score_breakdown`, `intelligence`, `assessed_hash`, `assessed_kb_version`, `review_status` |
| `opportunity_versions` | Snapshot per material change | `version`, `content_hash`, `snapshot` JSON, `run_id` |
| `change_events` | Human-readable change log | `change_type` (new, deadline_changed, value_changed, corrigendum_added, status_changed, retendered, document_changed, details_changed), field, old, new, `material` |
| `opportunity_documents` | Parsed documents | `sha256` (unique per opportunity), filename, path, pages, method, quality, `needs_manual_review`, `sections` JSON, `version` |
| `eligibility_findings` | Requirements and assessments | criterion, requirement text, value/unit, `explicit`, `source` rule/llm, `assessment`, `evidence` JSON (source, document, page, section, quote) |
| `opportunity_capability_matches` | Top capability matches | capability, rule/semantic/combined score, matched terms, `llm_confirmed` |
| `ai_analysis_results` | Validated LLM outputs (cache) | `cache_key`, purpose, model, prompt version, KB version, `result` JSON |
| `ai_usage_events` | One row per call or cache hit | run, opportunity, mode, model, purpose, input/output/cached tokens, `tokens_estimated`, cost, latency, result, `cache_hit` |
| `embedding_cache` | Vectors for local embeddings | unique (`text_hash`, `model`) |
| `notification_rules` | Alert rules (schema ready; digest uses config in MVP) | kind, min score, recipients, config |
| `email_delivery_log` | Idempotent delivery | unique `idempotency_key` = sha256(kind, report, run, recipient), status, attempts, error |
| `exported_reports` | Workbooks | path, sha256, rows, run |
| `user_review_feedback` | BD decisions and relevance labels | review status, `relevance_label`, note, reviewer |
| `awards` | Published award-of-contract lines (doc 08). No foreign key to `opportunities`; matched at report time | `id`, `source_id`, `award_key` (unique with `source_id`), `tender_id`, `ref_no`, `title`, `organisation`, `awarded_to`, `awarded_value_inr`, `award_date`, `source_url`, `evidence`, `fetched_at` |
| `system_settings` | Schema version etc. | key, value JSON |

## Identity and deduplication

1. `tid:<TENDER ID>` when the ID matches the GePNIC pattern `YYYY_ORG_SEQ[_N]` — unique across CPPP,
   GePNIC and state portals.
2. `sid:<org>:<id>` for other portal IDs (e.g. CPPP ePublishing numeric IDs), unique only per organisation.
3. `ref:<org>:<reference no.>`.
4. `h:<hash(title, org, closing date)>`.

Before inserting a non-`tid` record, a fuzzy check looks for the same reference number or a ≥0.92
similar title with the same closing time (±1 h) and a matching organisation; matches are merged
(`also_seen_on`) and missing fields are enriched without overwriting. A new GePNIC ID whose base
matches an existing one with a higher suffix (`…_1` → `…_2`) records a `retendered` event and marks
the older notice `retendered`.

## Change tracking rules

- Material fields hashed: title, ref, organisation, department, dates, value, EMD, description,
  corrigendum flag/list, status, category and document names. Re-fetch timestamps are excluded.
- Same source + different hash → new version + change events. Different source → merge only (avoids
  flip-flopping between portals that format fields differently).
- Expiry: active notices past their closing time become `expired` (non-material event).

## Re-assessment triggers

`assessed_hash ≠ content_hash`, `assessed_kb_version ≠ current KB version`, analysis state in
{pending, skipped_budget, failed, skipped_llm_disabled (when an LLM is now enabled)}, or
`deferred_no_documents` with a changed document fingerprint (new files in `inbox/documents/<id>/`).

## Migrations

The schema is versioned with Alembic. The scripts live in `src/chanakya/migrations/` (inside the
package, so `pip install .` and the Docker image ship them) and are driven by `chanakya db`, which
reads the database from `app.database_url` / `CHANAKYA_DATABASE_URL`. There is no `alembic.ini` and
no URL in any file under `migrations/`. The glue is `src/chanakya/migrate.py`.

| Command | What it does |
|---|---|
| `chanakya db current [--check]` | Shows the database's revision, the head the code expects and the state. Reads only. `--check` exits 1 unless the database is at the head |
| `chanakya db upgrade [REV] [--sql]` | Runs migration scripts up to REV (default head). On an empty database this builds the whole schema. `--sql` prints the SQL for the configured dialect and connects to nothing |
| `chanakya db stamp [REV] [--force]` | Adopts an existing database: records its revision, runs no migration, writes only `alembic_version`. Compares tables and columns with the revision first and refuses on a mismatch |
| `chanakya db copy-from SOURCE_URL` | Copies every row from another Chanakya database into the configured one, which must be empty. The SQLite to PostgreSQL move (doc 18) |
| `chanakya db revision -m "..." [--rev-id 0002]` | Developer command: writes a new migration script by comparing the models with a database at the head |

`upgrade`, `stamp` and `copy-from` take the single-writer lock, so stop `chanakya watch` first.

### How a database gets its schema

| Database found by `init_db()` | What happens |
|---|---|
| Empty | Tables are created from the models (`create_all`) and the database is stamped at the head revision in the same call. `chanakya db upgrade` on an empty database runs the scripts instead and ends in the same schema |
| Stamped at the head | Nothing. One catalogue query. If a model table is missing, the command stops and says so: a table was added to `db.py` without a migration |
| Stamped at another revision | The command stops with the instruction to run `chanakya db upgrade`. Nothing is migrated implicitly |
| Tables but no `alembic_version` (made by `create_all` before Alembic) | Works exactly as before: whole tables that newer models introduced are created, nothing existing is altered, nothing is stamped. Adopt it once with `chanakya db stamp` (runbook in doc 18) |

Fresh databases use `create_all` plus a stamp, not the scripts, because the two are held equal by a
test: `tests/test_migrations.py` upgrades an empty SQLite database through Alembic and compares
tables, columns, types, nullability, keys, indexes and constraints with the models, and asks Alembic
itself for a diff, which must be empty. Change a model without a migration and that test fails.
`system_settings.schema_version` is still written for older tooling; `alembic_version` is the authority.

### Changing the schema

1. Edit the model in `db.py`.
2. `chanakya db revision -m "add x to y" --rev-id 0002` against a development database at the head.
   Review the generated script. On SQLite, Alembic's batch mode rebuilds a table to alter it.
3. `chanakya db upgrade`, then run the tests. Never edit a revision that has shipped.

`scripts/regenerate_baseline.py` rewrites revision 0001 from the models. It exists only for the period
in which 0001 was the single revision and tables were still being added; it refuses to run once a
later revision exists.

## SQLite and PostgreSQL

The same models and the same test suite serve both. What differs, and how it is handled:

| Difference | Handling |
|---|---|
| SQLite ignores `VARCHAR(n)` lengths, PostgreSQL enforces them. Portal text is not truncated before it is stored | `db.String(n)` is `VARCHAR(n)` on SQLite and unbounded `TEXT` on PostgreSQL. The length documents what is expected |
| JSON columns | Generic `JSON`: text on SQLite, `json` on PostgreSQL. Not `jsonb`, which reorders object keys and would change what the desk and workbook show. `json` has no equality operator, so do not `DISTINCT` or `GROUP BY` a JSON column |
| `NULL` ordering: SQLite sorts NULL first ascending and last descending, PostgreSQL the opposite | Every `ORDER BY` on a nullable column states `.nulls_last()` or `.nulls_first()` with SQLite's placement. Keep doing this in new queries |
| `LIKE` is case-insensitive on SQLite (ASCII), case-sensitive on PostgreSQL | Text matching uses `ilike` / `icontains`, which mean the same on both |
| Timestamps are naive UTC. A PostgreSQL session in another time zone would shift an aware value | Every PostgreSQL connection is pinned to UTC (`pgsupport.engine_kwargs`) |
| PostgreSQL rejects NUL characters in text, SQLite stores them | NULs are stripped from string parameters on PostgreSQL only |
| Text sort order and case folding depend on the PostgreSQL locale | Create the database with `--locale=C` (compose and CI do) for the bytewise order SQLite uses |
| The writer lock file guards one host | On PostgreSQL the lock file (in `data_dir`) is backed by a session advisory lock, so a second host is refused |
| Rows that tie in an `ORDER BY`, and rows without one, come back in insertion order on SQLite and in no promised order on PostgreSQL | The lead lists, the paged `/api/opportunities` query, the P1 alert and the assessment queue end their `ORDER BY` with `first_seen_at, id`, which reproduces SQLite's order exactly (checked on a copy of the live data on both databases). Any new query whose order is shown to a person or paged must end in a unique key. Queries without `ORDER BY` were not all reviewed |

Verified on 2026-10-10 against an embedded PostgreSQL 18.4 on the build machine (the `pixeltable-pgserver` wheel in a throwaway virtual environment, Windows, database created with `LC_COLLATE 'C'`, server time zone Asia/Calcutta): the full suite passed there (317 passed) and on SQLite
(314 passed, 3 PostgreSQL-only tests skipped). The CI job `test-postgres` (PostgreSQL 17 in a Linux
container) has not been seen running; doc 18 lists what is still open.
