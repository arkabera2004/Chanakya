# 15 — API and CLI Specification

Chanakya is CLI-first. Every command accepts `--config/-c <path>` (default: `$CHANAKYA_CONFIG`, else
`config/chanakya.yaml`, else `config/chanakya.example.yaml`) and `--verbose/-v`. Relative paths in the
config resolve against the project root (the parent of the config folder).

## Commands

| Command | Purpose | Options |
|---|---|---|
| `chanakya init` | Create `config/chanakya.yaml` from the example, data folders and the database | — |
| `chanakya version` | Print version | — |
| `chanakya db current` | Show the database's schema revision and the one the code expects (doc 07). Reads only | `--check` (exit 1 unless at the head) |
| `chanakya db upgrade [REV]` | Run migration scripts up to REV (default `head`); builds an empty database | `--sql` (print the SQL, change nothing) |
| `chanakya db stamp [REV]` | Adopt an existing database: record its revision after comparing its schema; runs no migration | `--force` (skip the comparison) |
| `chanakya db copy-from URL` | Copy all rows from another Chanakya database into the configured, empty one (SQLite to PostgreSQL, doc 18) | none |
| `chanakya db revision` | Developer command: write a new migration script | `-m TEXT` · `--autogenerate/--empty` · `--rev-id` |
| `chanakya run` | Full pipeline: scan → assess → score → Excel → (email) | `--source/-s ID` (repeatable) · `--since YYYY-MM-DD` · `--max-pages N` · `--export/--no-export` (default export) · `--email/--no-email` (default no-email) · `--dry-run` (render digest to `outbox/dry_run`, never send) · `--llm-mode cloud\|local\|disabled\|fake` |
| `chanakya scan` | Acquire new/changed notices only | `--source/-s` · `--since YYYY-MM-DD` (re-scan back to a date) · `--max-pages` |
| `chanakya analyze` | Classify, analyse and score stored notices | `--scope changed\|all` (default changed) · `--new-only` · `--llm-mode` |
| `chanakya export` | Write the workbook for the current opportunity book | `--format xlsx` |
| `chanakya report` | Print top active opportunities | `--latest` · `--limit N` (15) |
| `chanakya email` | Send (or dry-run) the digest for the latest run and workbook, the weekly digest, or the new-P1 alert; idempotent per recipient | `--latest` · `--dry-run` · `--kind digest\|weekly\|alert` |
| `chanakya status` | Last 5 runs (status, new, cost), source health (health, consecutive failures, high-water mark, last error), active notices by priority | — |
| `chanakya costs` | LLM usage by model and purpose: calls, cache hits, calls refused by the budget, tokens, cost | `--last-days N` (30) |
| `chanakya import PATH` | Copy a CSV/XLSX/YAML of notices into the manual inbox, or attach documents | `--tender-id ID` → files go to `inbox/documents/<ID>/` |
| `chanakya review ID` | Record a BD decision / relevance label | `--status New\|Reviewing\|Shortlisted\|Qualified\|Partner needed\|Pursuing\|Rejected\|Expired\|Won\|Lost` · `--label relevant\|irrelevant` · `--note` · `--reviewer` |
| `chanakya benchmark` | Compare LLM-everything, keyword+LLM and the funnel on a labelled set | `--dataset PATH` · `--llm-accuracy 0.9` · `--out FILE.md` |
| `chanakya calibrate` | Propose `scoring.weights` from BD labels (doc 10). A proposal only: never edits the config or any score | `--out FILE.md` · `--write-proposal FILE.yaml` · `--min-labels N` |
| `chanakya winloss` | Read-only summary of pursued, won and lost notices by buyer, capability and platform | `--out FILE.md` · `--limit 20` |
| `chanakya schedule` | In-process daily scheduler (IST) | `--daily-at HH:MM` · `--email/--no-email` · `--once` |
| `chanakya watch` | Keep scanning every enabled source. A failed site or the end of a pass does not exit. | `--pause-s` (600) · `--retry-s` (30) · `--email/--no-email` |
| `chanakya serve` | Desk HTTP API on `127.0.0.1:8787`. The web app in `web/` calls it. | `--host` · `--port` |
| `chanakya healthcheck` | Container health: config loads, DB reachable, last successful run recent | `--max-age-hours 36` |
| `chanakya metrics` | Latest run, source health and last-24-hours totals for a monitor, read from the database (Prometheus text format; doc 18) | `--format prometheus\|json` · `--out FILE` (atomic write for a textfile collector) |
| `chanakya sources list` | Configured sources with type, enabled flag and verification status | — |
| `chanakya sources test` | Fetch one listing page per source (or `--source ID`) and report rows parsed, sample, health | `--source/-s` |
| `chanakya awards scan` | Read the CPPP award listing and store award lines. Takes the one-writer lock. Refuses while `awards.enabled` is false; ends as blocked (exit 3) on a CAPTCHA page | `--max-pages` |
| `chanakya awards import FILE` | Store awards from a result page (`.html`) or CSV saved by a person. No network | none |
| `chanakya awards test` | Fetch and parse the first award page, no database writes (run before changing `awards.verification_status`) | none |
| `chanakya awards report` | Awards for notices already in the book, then frequent winners and value ranges for awards whose title matched a capability | `--top 10` |
| `chanakya llm check` | List the models visible to the configured provider (validates reachability and the key) and confirm `fast_model`/`deep_model` are available; no secrets printed; no tokens billed | — |
| `chanakya capabilities validate` | Validate the knowledge base and print the capability table | — |

### Exit codes

`0` success · `1` run or configuration error · `2` every enabled source failed/blocked · `3` partial
(a source failed or an email delivery failed; the report was still produced).

### Typical invocations

```bash
chanakya init
chanakya sources test                        # from the deployment network
chanakya llm check                           # with OLLAMA_API_KEY set
chanakya run --dry-run                       # full run, digest rendered to data/outbox/dry_run
chanakya run --email                         # production daily run
chanakya import ./downloads/<TENDER_ID>/ --tender-id <TENDER_ID>   # documents you downloaded
chanakya analyze                             # resumes deferred deep analyses
chanakya review <CHK-id> --status Shortlisted --label relevant --reviewer "BD lead"
chanakya costs --last-days 7
```

## Configuration schema (`config/chanakya.yaml`)

Top-level sections (all typed and validated by `config.py`; unknown keys are rejected):

| Section | Key fields |
|---|---|
| `app` | `timezone`, `data_dir`, `database_url`, `capabilities_file`, `reports_dir`, `report_retention`, `inbox_dir`, `documents_dir`, `log_level`, `log_format` (text\|json) |
| `http` | `user_agent`, `timeout_s`, `max_retries`, `backoff_base_s`, `min_interval_s`, `respect_robots_txt`, `allowed_domains`, `max_download_mb`, `max_redirects`, `circuit_breaker_failures`, `circuit_breaker_cooldown_s` |
| `sources[]` | `id`, `type` (cppp\|gepnic\|manual\|fixture), `name`, `enabled`, `base_url`, `feed`, `max_pages`, `overlap_hours`, `fetch_detail`, `path`, `platform_label`, `verification_status` |
| `filtering` | `skip_expired`, `backfill_days`, `reject_threshold`, `candidate_threshold`, `strong_threshold`, `high_recall_categories`, `high_recall_types` |
| `semantic` | `enabled`, `backend` (lexical\|ollama), `embed_model`, `candidate_threshold` |
| `llm` | `mode`, `cloud_base_url`, `local_base_url`, `api_key_env`, `fast_model`, `deep_model`, `think`, `temperature`, `request_timeout_s`, `max_output_tokens_fast/deep`, `max_retries`, `send_format_schema`, `prompt_version`, `triage_band`, `deep_min_score` |
| `budget` | `max_cost_per_run_usd`, `max_cost_per_day_usd`, `max_calls_per_run`, `max_input_tokens_per_call`, `max_documents_per_tender`, `max_pages_per_document`, `max_deep_analyses_per_run` |
| `pricing` | model → `{input, cached_input, output}` USD per 1M tokens |
| `scoring` | `weights`, `priority_thresholds`, `closing_soon_days`, `min_days_to_prepare`, `strategic_org_keywords` |
| `company_profile` | `annual_turnover_inr_crore`, `average_turnover_3y_inr_crore`, `years_in_operation`, `certifications`, `msme_registered`, `dpiit_startup`, `past_government_projects`, `cert_in_empanelled` (all null = unknown) |
| `email` | `enabled`, `provider` (file\|smtp), `smtp_host/port/security`, `username_env`, `password_env`, `sender`, `to`, `cc`, `min_score`, `closing_within_days`, `attach_excel`, `max_attachment_mb`, `subject_template`, `outbox_dir`, `max_retries` |
| `schedule` | `daily_at`, `weekdays_only`, `weekly_digest_day` |
| `documents` | `ocr_enabled`, `ocr_languages`, `max_zip_members`, `max_zip_total_mb`, `max_zip_ratio`, `min_chars_per_page`, `tables_enabled`, `max_table_rows_per_document` |
| `awards` | `enabled` (false), `source_id`, `name`, `url`, `max_pages`, `verification_status` (candidate) |

Environment overrides: `CHANAKYA_CONFIG`, `CHANAKYA_DATABASE_URL`, `CHANAKYA_LLM_MODE`,
`CHANAKYA_EMAIL_TO`, `CHANAKYA_LOG_FORMAT`; secrets: `OLLAMA_API_KEY`, `CHANAKYA_SMTP_USERNAME`, `CHANAKYA_SMTP_PASSWORD`.

The HTTP desk (`chanakya serve`) is specified in [21 — Desk UI and API plan](21_DESK_UI_AND_API.md): routes that exist today, and the watch, analyse, and event routes still to build.

## Manual import format

CSV (UTF-8, header row), XLSX (first sheet) or YAML (list of mappings). Template:
`tests/fixtures/manual/manual_import_template.csv`.

| Column | Required | Notes |
|---|---|---|
| `platform` | yes | e.g. "GeM (manual)", "IREPS (manual)", "BECIL website" |
| `title` | yes | As published |
| `tender_id` | recommended | GePNIC ID, GeM bid number (`GEM/2026/B/…`), IREPS number |
| `ref_no`, `organisation`, `department`, `state`, `category`, `tender_category` | optional | |
| `opportunity_type` | optional | Inferred from title/ID when blank (Bid, EOI, RFP, RFQ, RFI, Empanelment, Rate Contract, Corrigendum) |
| `published_at`, `closing_at`, `opening_at` | recommended | Day-first, IST (e.g. `30-Oct-2026 03:00 PM`); unparseable → empty, never guessed |
| `estimated_value_inr`, `emd_inr` | optional | Numbers in rupees |
| `description` | recommended | Scope text copied from the document — enables deep analysis (≥200 chars) |
| `official_url` | recommended | Must be a real page; shown as a link only for allowlisted hosts |
| `document_paths` | optional | `;`-separated paths relative to the import file |
| `status` | optional | active (default), closed, cancelled… |
| `evidence` | recommended | Who copied it, from where, when |

## Python API (for embedding or tests)

```python
from chanakya.config import load_settings
from chanakya.app import open_session, run_pipeline

settings = load_settings("config/chanakya.yaml")
with open_session(settings) as session:
    result = run_pipeline(settings, session, source_ids=None, export=True, email=False, dry_run=True)
    print(result.exit_code, result.report_path, result.stats.candidates)
```

`run_pipeline(settings, session, *, source_ids, since, max_pages, export, email, dry_run, trigger,
provider, http_client, assess_scope, skip_scan) -> RunResult(run, stats, report_path, report_id,
email_status, email_error, exit_code)`. `provider` and `http_client` can be injected (tests use
`FakeProvider` and `httpx.MockTransport`).

### Connector contract

```python
class TenderSourceConnector(ABC):
    def discover(self, ctx: DiscoveryContext) -> Iterator[TenderRecord]
    def fetch_listing(self, url: str | None, page_no: int) -> ListingPage
    def fetch_detail(self, record: TenderRecord) -> TenderRecord
    def fetch_documents(self, record: TenderRecord) -> list[DocumentRef]
    def normalize(self, raw: dict) -> TenderRecord
    def check_updates(self, keys: list[str]) -> dict[str, str]
    def health_check(self) -> HealthStatus
```

Errors: `BlockedError` (restricted — never retried in a loop), `FetchError` (transient, retried),
`RateLimitedError`, `StructureChangedError` (layout change). Details: doc 08.

### LLM provider contract

```python
class LLMProvider(Protocol):
    mode: str   # cloud | local | fake
    def chat(self, messages: list[ChatMessage], *, model: str, max_tokens: int, temperature: float = 0.0,
             json_schema: dict | None = None, think=None) -> LLMResponse  # content, tokens, latency
```

Raises `LLMAuthError`, `LLMRateLimited`, `LLMTimeout`, `LLMError`. Output schemas
(`TriageOutput`, `DeepOutput`) are in `pipeline/analyze.py` and documented in doc 10.

## HTTP API

None in the MVP (ADR-09). Phase 6 adds a read-only FastAPI service over the same database:
`GET /opportunities?priority=&status=&capability=`, `GET /opportunities/{id}` (with findings, evidence,
history), `POST /opportunities/{id}/review`, `GET /runs`, `GET /costs` — authenticated with the
organisation's SSO.
