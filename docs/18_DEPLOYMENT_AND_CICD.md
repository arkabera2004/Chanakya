# 18 — Deployment and CI/CD

Chanakya is a batch job: start, run the pipeline, write the workbook and digest, exit with a status
code. Deploy it wherever scheduled containers run.

## Local (developer laptop or a VM)

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"                     # add ".[ocr]" + apt install tesseract-ocr for OCR
chanakya init                               # config/chanakya.yaml, data/, database
cp .env.example .env                        # add OLLAMA_API_KEY / SMTP credentials if used
chanakya sources test                       # confirm the portals parse from this network
chanakya llm check                          # only for llm.mode cloud/local
chanakya run --dry-run                      # digest rendered to data/outbox/dry_run
```

Demo without network or keys:

```bash
python scripts/make_demo_data.py
chanakya -c config/chanakya.demo.yaml run --dry-run
```

Scheduling on a VM: cron (the host must be on IST or use `CRON_TZ`):

```
CRON_TZ=Asia/Kolkata
30 7 * * *  cd /opt/chanakya && . .venv/bin/activate && set -a && . ./.env && set +a && chanakya run --email >> logs/run.log 2>&1
0 2 * * *   cd /opt/chanakya && scripts/backup.sh ./data ./backups 14
```

## Docker

`Dockerfile`: `python:3.12-slim`, optional OCR (`--build-arg INSTALL_OCR=true` installs tesseract with
English and Hindi), optional PostgreSQL driver (`--build-arg INSTALL_POSTGRES=true`, or
`INSTALL_POSTGRES=true` in `.env` for compose), non-root user 10001, `/app/data` volume, `TZ=Asia/Kolkata`,
`HEALTHCHECK chanakya healthcheck --max-age-hours 36`, `ENTRYPOINT chanakya`, default `run --dry-run`.

```bash
cp .env.example .env && cp config/chanakya.example.yaml config/chanakya.yaml   # edit both
docker compose build
docker compose run --rm chanakya sources test
docker compose run --rm chanakya run --dry-run
docker compose up -d scheduler            # long-running: daily at schedule.daily_at IST, with email
docker compose --profile local-llm up -d ollama     # optional local models (llm.mode: local)
docker compose --profile postgres up -d postgres    # optional PostgreSQL (set POSTGRES_PASSWORD)
docker compose run --rm chanakya db upgrade         # PostgreSQL: build or migrate the schema before the first run
```

The config folder is mounted read-only; data (database, reports, inbox, outbox) lives in the
`chanakya-data` volume. If `CHANAKYA_CONFIG` points at a missing file the container fails fast instead
of running with defaults.

## Azure (recommended managed option)

| Component | Service | Notes |
|---|---|---|
| Image | Azure Container Registry | Build in CI, tag with git SHA |
| Daily run | **Azure Container Apps job**, schedule trigger `0 2 * * *` (UTC = 07:30 IST), command `chanakya run --email` | Replica timeout ≥ 30 min; retry limit 1 (the run is idempotent) |
| Data | Azure Files share mounted at `/app/data` (SQLite) **or** Azure Database for PostgreSQL Flexible Server | SQLite on Azure Files is acceptable for a single scheduled writer; choose Postgres when a dashboard or second writer is added |
| Secrets | Key Vault references → env vars `OLLAMA_API_KEY`, `CHANAKYA_SMTP_USERNAME`, `CHANAKYA_SMTP_PASSWORD` | Managed identity for Key Vault access |
| Config | Mount `chanakya.yaml` from a share or bake a reviewed config into a derived image | |
| Monitoring | Log Analytics; alert on job execution failures (non-zero exit) | Exit 3 = partial: alert BD/ops but not paging |
| Email | Microsoft 365 SMTP AUTH (if enabled for the tenant) or Azure Communication Services Email via SMTP | Use a dedicated sender mailbox |
| Outbound network | Allow egress to `eprocure.gov.in`, `etenders.gov.in`, `ollama.com` and the SMTP host | Indian government portals may throttle or block non-Indian IP ranges — **prefer an India region (Central India / South India)** and verify with `sources test` |

Equivalents: AWS (ECS scheduled task + EFS/RDS + Secrets Manager), GCP (Cloud Run jobs + Cloud
Scheduler + Secret Manager), Kubernetes (CronJob + PVC + Secret).

## Database migrations and PostgreSQL

Schema rules and the `chanakya db` commands are in doc 07 ("Migrations"). This section is the
operator's side.

**Status (2026-10-10): verified locally on an embedded server, not yet in CI.** There is no Docker
daemon on the build machine. The PostgreSQL path was run against an embedded PostgreSQL 18.4 on the build machine (the `pixeltable-pgserver` wheel in a throwaway virtual environment, Windows, database created with `LC_COLLATE 'C'`, server time zone Asia/Calcutta):

- the full test suite: 317 passed (SQLite: 314 passed, 3 PostgreSQL-only tests skipped);
- `chanakya db upgrade` on an empty database, then `db current --check`;
- the demo run end to end, twice, with the same counts as on SQLite;
- `chanakya db copy-from` of a copy of the live SQLite database: 26,446 rows in 19 tables, every row
  compared value for value afterwards, no difference, id sequences positioned after the copied ids;
- the desk's lead queries on both copies: the same totals for every filter tried, and the same order
  for the lists compared once ties were given a fixed order (doc 07).

That is PostgreSQL 18 on Windows, not the PostgreSQL 17 Linux container that CI and compose use, and
not a managed service. The CI job `test-postgres` has not been seen running. Rehearse the move with a
copy of production data on the target server before the real one.

### Runbook: adopt the existing SQLite database (once)

`data/chanakya.db` was created by `create_all` before migrations existed. It keeps working as it is.
Adopting it adds one small table (`alembic_version`) and changes no row in any other table. Rehearsed
on a copy of the live file on 2026-10-10: 19 tables and 26,446 rows before; the same 19 tables with
identical row counts and identical content hashes after, plus `alembic_version` with one row.

```bash
# 1. Stop every writer: the desk's watch (Stop on the desk, or end `chanakya watch`) and any scheduler.
chanakya db current                      # expect: not under migration control ... chanakya db stamp
# 2. Back up. The script is safe while the desk is still reading.
scripts/backup.sh ./data ./backups 14
# 3. Adopt. Refuses, and lists the differences, if the schema is not what revision 0001 describes.
chanakya db stamp
chanakya db current --check              # expect: up to date at 0001, exit 0
# 4. Start the watch again.
```

On Windows without bash, step 2 is a file copy of `data\chanakya.db` taken while no writer is running.
If `stamp` reports a missing column, the file predates a model change that `create_all` could not
apply; do not use `--force`, restore nothing, and raise it: that database needs a hand-written
migration. To undo an adoption, stop writers and run `DROP TABLE alembic_version` in SQLite.

From then on a schema change arrives as a new revision: deploy the code, stop writers, back up,
`chanakya db upgrade`, start. A command run against a database that is behind stops with that
instruction and changes nothing.

### Runbook: move from SQLite to PostgreSQL

Move when a second writer or a second host is needed (ADR-03). Budget a maintenance window: the copy
is offline.

```bash
# 0. Install the driver:  pip install 'chanakya[postgres]'
#    Docker:              INSTALL_POSTGRES=true in .env, then docker compose build
# 1. Create the database with bytewise collation (text then sorts as on SQLite):
#    docker compose --profile postgres up -d postgres      (the compose file passes --locale=C)
#    managed service: create the database with LC_COLLATE and LC_CTYPE "C", encoding UTF8
# 2. Stop every writer on the SQLite database and back it up (as above). Adopt it if it is not stamped yet.
chanakya db current --check              # still pointing at SQLite: must be up to date
# 3. Point Chanakya at PostgreSQL. The URL holds a password, so it goes in the environment, never in YAML:
export CHANAKYA_DATABASE_URL='postgresql+psycopg://chanakya:<password>@<host>:5432/chanakya'
chanakya db upgrade                      # builds the schema from the migration scripts
# 4. Copy the rows. The source is only read; the target is written in one transaction.
chanakya db copy-from sqlite:///./data/chanakya.db
# 5. Check, then start the writers with CHANAKYA_DATABASE_URL set.
chanakya db current --check && chanakya status
```

`copy-from` prints the rows copied per table, verifies the counts before it commits and moves each
id sequence past the copied ids. `postgres://` and `postgresql://` URLs are accepted and read as
`postgresql+psycopg://`. Keep the SQLite file until the PostgreSQL deployment has run for a few days;
going back is a matter of unsetting `CHANAKYA_DATABASE_URL` (runs made on PostgreSQL in between are
not copied back).

What changes for operations on PostgreSQL:

- `data_dir` is still needed and must be shared by the desk and the watcher: the lock file, the stop
  flag and the per-run event files live there. The database itself enforces one writer with an
  advisory lock, so a writer on a second host is refused even without a shared folder.
- Backups: `scripts/backup.sh` covers SQLite only. Use scheduled `pg_dump` or the managed service's
  point-in-time restore, and keep archiving `data/reports`.
- Managed PostgreSQL usually requires TLS: append `?sslmode=require` to the URL.
- Commands print the database URL with the password masked.

### Not verified (PostgreSQL)

| Item | Why it is open | How it gets verified |
|---|---|---|
| The CI job `test-postgres` (PostgreSQL 17, Linux container, `--locale=C` through `POSTGRES_INITDB_ARGS`) | The workflow cannot be run from the build machine. The same suite passed on an embedded PostgreSQL 18.4 | First run of the workflow |
| PostgreSQL 17 specifically, and any managed service (TLS, connection limits, idle timeouts) | Only PostgreSQL 18.4 was available locally | CI for 17; a rehearsal on the target service |
| A database not created with `C` collation | Only `C` was tried. Text ordering of source ids and similar lists may differ from SQLite | Create the database as documented, or accept the difference |
| Two hosts contending for the writer lock | The advisory lock was tested with two sessions from one process, not two machines | `test_postgres_lets_only_one_writer_hold_the_database` covers the mechanism; try a second host in staging |
| Queries without `ORDER BY` | The suite passes on PostgreSQL and the lead lists were given a fixed tie order, but not every unordered query was reviewed | Add an `ORDER BY` ending in a unique key where an order is shown or paged |
| A numeric edge: PostgreSQL's `json` type rejects `NaN` and `Infinity`, which SQLite stores as text | The copy of the live data loaded into PostgreSQL without meeting one; no guard was added | Would surface as an insert error naming the column |
| `pip-audit` | It needs the advisory service, and this work made no network calls except `pip install` | CI job `audit`; expect to triage its first run |
| Docker image builds (default and `INSTALL_POSTGRES=true`) | No Docker daemon | CI job `docker` |

## CI (`.github/workflows/ci.yml`)

Every job also runs on a weekly schedule (Mondays 07:00 IST) so that new advisories and base-image
changes show up without a push. `live-sources` stays manual.

| Job | Trigger | Steps |
|---|---|---|
| `test` | push to main, PRs, manual | Python 3.12 and 3.13 matrix: install `.[dev]`, `ruff check src tests scripts`, validate the KB, `pytest`, benchmark (regression guard) |
| `test-postgres` | push to main, PRs, manual, weekly | PostgreSQL 17 service container (`--locale=C`). Install `.[dev,postgres]`, run the same `pytest` suite with `CHANAKYA_TEST_DATABASE_URL` set (each test gets its own schema), then `chanakya db upgrade` and `db current --check` on the empty database, then the demo run end to end. Roadmap M5: this job is the proof that the suite is green on both databases. **The workflow itself has not been seen running**; its steps were rehearsed by hand on an embedded PostgreSQL 18.4 |
| `audit` | push to main, PRs, manual, weekly | `pip-audit` over the installed dependency set including the optional PostgreSQL driver. Fails on a known vulnerability. To accept one, add `--ignore-vuln <ID>` with the reason in the workflow. **Not run locally** |
| `docker` | after `test` | Build the image; validate the KB inside the container; `db upgrade` inside the container (proves the migration scripts ship); build the `INSTALL_POSTGRES=true` variant and import the driver |
| `live-sources` | manual dispatch only | `sources test --source cppp_central` — informational, never gates merges |

## CD (to add when the target platform is chosen)

1. On tag `v*`: build, scan (`pip-audit`, image scanner), push `chanakya:<sha>` and `:<version>`.
2. Deploy to a **staging** job that runs with `--dry-run` against a staging database for one day.
3. Promote by updating the production job's image tag; keep the previous tag for rollback.
4. Database changes go through Alembic migrations run as a one-off job before the new image:
   stop writers, back up, `chanakya db upgrade` with the new image, start. `chanakya db upgrade --sql`
   prints the statements for review first.

## Monitoring and alerting

Chanakya gives a platform monitor three signals. It ships no alerting dependency of its own: the
rules below belong in the monitor you already run (Prometheus and Alertmanager, Azure Monitor, or
the scheduler's own failure alert). Code: `src/chanakya/obs.py`; tests: `tests/test_obs.py`.

### Signal 1: structured logs

Set `app.log_format: json` (or `CHANAKYA_LOG_FORMAT=json`, which wins over the config). Each log
record is then one JSON object on one line of **stderr**, with `timestamp` (UTC, ISO 8601), `level`,
`logger`, `message` and any structured fields. The human run summary stays on stdout. In JSON mode
`chanakya.*` loggers write at `app.log_level` (default `INFO`); text mode is unchanged (warnings and
above, everything with `--verbose`).

Every run ends with exactly one record whose `event` is `run_finished`. This covers `scan`,
`analyze`, `run`, each `watch` pass, each `schedule` run and each pass started from the desk,
because all of them go through `run_pipeline`, which carries the emitting decorator. Fields:

| Field | Meaning |
|---|---|
| `run_id`, `trigger`, `status`, `exit_code`, `dry_run` | Identity and outcome. `exit_code` uses the CLI contract: 0 ok, 1 run or config error, 2 every source failed, 3 partial |
| `duration_s` | Wall time of the whole run, including the workbook and email |
| `sources` | Per source: `status`, `rows`, `new`, `updated`, `pages`, `duration_s`, `error` |
| `rows_total`, `new`, `updated`, `assessed`, `priorities` | Counts, with `priorities` as `{"P1": n, "P2": n, "P3": n, "P4": n}` |
| `model_mode`, `model_calls`, `triage_calls`, `deep_calls`, `model_cached`, `model_failed`, `model_skipped_budget`, `model_cost_usd` | Model usage and estimated cost |
| `budget_blocked_reasons` | Budget limits that stopped a model call in this run |
| `email_sent`, `email_deliveries`, `email_error`, `alerts_sent`, `report_written` | Delivery. A dry-run preview is not counted as sent |
| `error` | The run error, redacted |

The record is `INFO` for exit code 0, `WARNING` for 3 and `ERROR` for 1 and 2. If `run_pipeline`
raises instead of returning (for example the disk fills while the workbook is written), the record
has `status: "crashed"`, `exit_code: 1` and no `run_id`.

Log safety (doc 13): a filter on the handler replaces every known secret value (each value handed
out by `settings.secret()`, the Ollama key, the SMTP username and password, a database URL
password) and every `Bearer ...` or `password=...` pattern with `***REDACTED***`. Fields named
`prompt`, `messages`, `document_text`, `content` and similar are written only as a size. Messages
are capped at 1,000 characters, fields at 500 and tracebacks at 4,000, so a full prompt or document
cannot fit in a line.

Reading the log with `jq`:

```bash
chanakya run --email 2>> logs/chanakya.jsonl      # stderr carries the JSON lines

# One line per run: what happened and what it cost
jq -c 'select(.event == "run_finished")
       | {timestamp, run_id, trigger, status, exit_code, duration_s, new, assessed, model_cost_usd, email_sent}' \
   logs/chanakya.jsonl

# Runs that need attention, with the failing sources
jq -c 'select(.event == "run_finished" and .exit_code != 0)
       | {run_id, exit_code, error, failed: [.sources | to_entries[] | select(.value.status != "ok") | .key]}' \
   logs/chanakya.jsonl
```

The same filter in Azure Log Analytics (Container Apps console logs), as a log alert query:

```kusto
ContainerAppConsoleLogs_CL
| where Log_s startswith "{"
| extend e = parse_json(Log_s)
| where e.event == "run_finished" and toint(e.exit_code) in (1, 2, 3)
| project TimeGenerated, run_id = tostring(e.run_id), exit_code = toint(e.exit_code), error = tostring(e.error)
```

### Signal 2: metrics

`chanakya metrics` prints the latest run, source health and last-24-hours totals in the Prometheus
text exposition format (`--format json` for a script). It only reads the database, so it is safe to
run while a scan or a watch holds the writer lock. The desk API serves the same text at
`GET /api/metrics` (`?format=json` for JSON). The desk has no authentication and binds to
`127.0.0.1`, so scrape it from the same host.

For a batch job, write the file for the node-exporter textfile collector after each run and on a
timer, so the staleness gauge keeps moving between runs:

```
CRON_TZ=Asia/Kolkata
30 7 * * *   cd /opt/chanakya && . .venv/bin/activate && set -a && . ./.env && set +a && chanakya run --email 2>> logs/chanakya.jsonl; chanakya metrics --out /var/lib/node_exporter/textfile/chanakya.prom
*/5 * * * *  cd /opt/chanakya && . .venv/bin/activate && chanakya metrics --out /var/lib/node_exporter/textfile/chanakya.prom
```

`--out` writes a temporary file and renames it, so the collector never reads half a file.

| Metric (all gauges) | Labels | Meaning |
|---|---|---|
| `chanakya_seconds_since_last_success` | | Seconds since the last `ok` or `partial` run started: the same rule as `chanakya healthcheck`. Absent until a run has succeeded |
| `chanakya_successful_run_recorded` | | 1 once any run has succeeded, else 0 |
| `chanakya_last_run_exit_code` | | 0 to 3 for the latest run, derived from its stored status, error and failed email rows. Absent while it is running |
| `chanakya_last_run_status` | `status` | 1 for the latest run's status |
| `chanakya_last_run_info` | `run_id`, `trigger`, `status` | Always 1 |
| `chanakya_last_run_duration_seconds` | | Scan and assessment time of the latest run (start to finish row; the log's `duration_s` also includes export and email) |
| `chanakya_last_run_notices` | `kind` = new, updated, assessed | Counts for the latest run |
| `chanakya_last_run_priority_notices` | `priority` | P1 to P4 counts for the latest run |
| `chanakya_last_run_model_calls`, `chanakya_last_run_model_cost_usd`, `chanakya_last_run_budget_blocked` | `purpose` on calls | Model usage of the latest run |
| `chanakya_last_run_source_rows`, `chanakya_last_run_source_ok`, `chanakya_last_run_source_duration_seconds` | `source_id` | Rows per source and per-source outcome for the latest run |
| `chanakya_source_consecutive_failures` | `source_id` | Scans of the source that failed in a row (reset by a good scan) |
| `chanakya_source_healthy`, `chanakya_source_enabled`, `chanakya_source_in_cooldown`, `chanakya_source_seconds_since_last_success` | `source_id` | Stored source health |
| `chanakya_runs_24h`, `chanakya_notices_24h`, `chanakya_source_rows_24h` | `status`, `kind`, `source_id` | Totals over the last 24 hours |
| `chanakya_model_calls_24h`, `chanakya_model_cost_usd_24h`, `chanakya_model_tokens_24h` | `direction` on tokens | Model usage over the last 24 hours, cache hits excluded |
| `chanakya_email_deliveries_24h` | `status` | Email delivery rows over the last 24 hours |
| `chanakya_metrics_generated_timestamp_seconds` | | When the numbers were read. Alert on this to catch a dead exporter |

### Signal 3: exit codes and the healthcheck

A scheduled job (cron, systemd timer, Container Apps job, Kubernetes CronJob) reports the exit code
of `chanakya run`. `chanakya healthcheck --max-age-hours 36` exits 1 when the database is
unreachable or the last successful run is older than the limit; the Docker image runs it as its
`HEALTHCHECK`. A `watch` loop never exits, so for a watch use the metrics and the log event instead
of the exit code.

### Alert rules

| Alert | Condition | Severity | Where to set it |
|---|---|---|---|
| Run error | Exit code 1 | Page | Job failure alert, or `chanakya_last_run_exit_code == 1`, or the log query above |
| All sources failed | Exit code 2 | Page | Same as above with 2 |
| Partial run | Exit code 3 (a source, the budget or email needs attention) | Notify BD and ops, do not page | Same as above with 3, held for two runs |
| Healthcheck failing | `chanakya healthcheck` exits 1 | Page | Container health state, or the staleness rule below |
| Stale | `chanakya_seconds_since_last_success > 129600` (36 hours) | Page | Prometheus |
| Never succeeded | `chanakya_successful_run_recorded == 0` for 36 hours after deployment | Notify | Prometheus |
| Source failing | `chanakya_source_consecutive_failures >= 3` on an enabled source | Notify | Prometheus |
| Source in cooldown | `chanakya_source_in_cooldown == 1` | Notify | Prometheus |
| Exporter dead | Metrics missing or older than 15 minutes | Notify | Prometheus |
| Model spend | `chanakya_model_cost_usd_24h` above `budget.max_cost_per_day_usd` | Notify | Prometheus |

Example Prometheus rules (`chanakya.rules.yml`). Thresholds mirror the defaults in this repository
(`healthcheck --max-age-hours 36`, `http.circuit_breaker_failures: 5`, `budget.max_cost_per_day_usd: 2.00`);
change them together with the config.

```yaml
groups:
  - name: chanakya
    rules:
      - alert: ChanakyaRunError
        expr: chanakya_last_run_exit_code == 1
        labels: {severity: page}
        annotations:
          summary: "Chanakya run failed with a run or config error (exit code 1)"
          description: "Read the run_finished log record for the error, then rerun: chanakya run is idempotent."

      - alert: ChanakyaAllSourcesFailed
        expr: chanakya_last_run_exit_code == 2
        labels: {severity: page}
        annotations:
          summary: "Every Chanakya source failed in the latest run (exit code 2)"
          description: "Check egress, DNS and the portals with: chanakya sources test"

      - alert: ChanakyaPartialRun
        expr: chanakya_last_run_exit_code == 3
        for: 26h          # still partial after the next daily run
        labels: {severity: notify}
        annotations:
          summary: "Chanakya runs are partial (exit code 3): a source, the budget or email needs attention"

      - alert: ChanakyaStale
        expr: chanakya_seconds_since_last_success > 36 * 3600
        for: 10m
        labels: {severity: page}
        annotations:
          summary: "No successful Chanakya run for {{ $value | humanizeDuration }}"
          description: "Same condition as a failing chanakya healthcheck. Check the scheduler, then run chanakya run --email."

      - alert: ChanakyaNeverSucceeded
        expr: chanakya_successful_run_recorded == 0
        for: 36h
        labels: {severity: notify}
        annotations:
          summary: "Chanakya has not completed a single successful run since it was deployed"

      - alert: ChanakyaSourceFailing
        expr: chanakya_source_consecutive_failures >= 3 and on (source_id) chanakya_source_enabled == 1
        labels: {severity: notify}
        annotations:
          summary: "Source {{ $labels.source_id }} has failed {{ $value }} scans in a row"
          description: "Run chanakya sources test -s {{ $labels.source_id }}. A CAPTCHA or a block is not bypassed: use manual import."

      - alert: ChanakyaSourceInCooldown
        expr: chanakya_source_in_cooldown == 1
        labels: {severity: notify}
        annotations:
          summary: "Source {{ $labels.source_id }} is held back by the circuit breaker, coverage is reduced"

      - alert: ChanakyaMetricsMissing
        expr: absent(chanakya_metrics_generated_timestamp_seconds) or time() - chanakya_metrics_generated_timestamp_seconds > 900
        for: 10m
        labels: {severity: notify}
        annotations:
          summary: "Chanakya metrics are missing or older than 15 minutes (exporter cron or desk API down)"

      - alert: ChanakyaModelSpendHigh
        expr: chanakya_model_cost_usd_24h > 2.00
        labels: {severity: notify}
        annotations:
          summary: "Chanakya model spend over the last 24 hours is ${{ $value }}"
```

Without Prometheus, the scheduler can alert on the same conditions with the two commands alone
(replace `notify` with the mail or chat command the platform already provides):

```
30 7 * * *  cd /opt/chanakya && . .venv/bin/activate && chanakya run --email 2>> logs/chanakya.jsonl || notify "chanakya run exit $?"
0 * * * *   cd /opt/chanakya && . .venv/bin/activate && chanakya healthcheck > /dev/null || notify "chanakya healthcheck failed"
```

Verified: the log format, the redaction, the single `run_finished` record per run, the metric names
and values, the staleness gauge and `GET /api/metrics` are covered by `tests/test_obs.py`. Not
verified: the Prometheus rules, the Kusto query and the cron lines above are examples. They have not
been loaded into a live Prometheus, Log Analytics workspace or production crontab; check them with
`promtool check rules` and a test alert before relying on them.

## Operations runbook

| Situation | Signal | Action |
|---|---|---|
| A source fails or is blocked | Exit 3; Executive Summary coverage note; `chanakya status` | Run `chanakya sources test -s <id>`; layout change → update parser + fixture; CAPTCHA/blocked → keep source disabled and use manual import |
| All sources fail | Exit 2 | Check egress/DNS/proxy from the job; portal outage → rerun later (`chanakya run` is idempotent) |
| LLM auth error | "AI ANALYSIS FAILED", usage rows `auth_error` | Rotate `OLLAMA_API_KEY`; `chanakya llm check`; `chanakya analyze` re-runs failed analyses |
| Budget hit | "AI ANALYSIS SKIPPED (budget limit)" | Expected on spikes; skipped notices retry next run; raise limits only after reviewing `chanakya costs` |
| Email failed | Exit 3; `email_delivery_log.status = failed` | Fix SMTP; `chanakya email --latest` sends only to recipients not yet served |
| Missed run | `healthcheck` fails (> 36 h) | Check the scheduler; run `chanakya run --email` manually |
| Restore | — | Stop the job; copy the newest `backups/chanakya_*.db` to `data/chanakya.db`; start |
| Schema behind the code | A command stops with "the code expects <rev>. Run: chanakya db upgrade" | Stop writers, back up, `chanakya db upgrade`, start. `chanakya db current` shows both revisions |
| Database not under migration control | `chanakya db current` says so; commands still work | Adopt it once: runbook "adopt the existing SQLite database" above |
| "database is stamped at ... but has no table(s) ..." | A command stops with that message | A table was added to the models without a migration. Development database: delete it. Otherwise add the revision (doc 07) and upgrade |
| Second writer refused on PostgreSQL | "another writer holds the database (pid 0, a writer on another host)" | Expected: one writer at a time. Find the other host's `watch` or `run`; the lock frees itself if that process dies |

Backups: `scripts/backup.sh [data_dir] [backup_dir] [keep]` — SQLite online backup (consistent while
writing) plus a reports archive, keeping the newest 14. For PostgreSQL use scheduled `pg_dump` or the
managed service's point-in-time restore.
