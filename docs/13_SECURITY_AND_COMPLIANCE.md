# 13 — Security and Compliance

## Compliance boundaries (non-negotiable)

| Rule | How it is enforced |
|---|---|
| No CAPTCHA, login or access-control bypass | `has_captcha` detects CAPTCHA pages (text and image attributes) → `BlockedError`; 401/403 → `BlockedError`; source marked `restricted`, documents routed to manual import. There is no CAPTCHA-solving code or service integration. |
| robots.txt honoured | `PoliteClient.robots_allows` before every request (cached per origin); a disallow blocks the source. A missing robots.txt (404) is treated as "no policy". |
| Polite rate | ≥ `http.min_interval_s` between requests per host (3 s in the example config); bounded retries with exponential backoff and jitter; `Retry-After` honoured; circuit breaker after 5 consecutive failures (15-minute cooldown). |
| Identifiable client | `http.user_agent` names the bot and a contact address (set it before production). |
| Public data only | Only public listing pages are fetched; documents behind CAPTCHA/login are downloaded by a person and imported. |
| No automated bidding or contact | No code submits bids, registers on portals, or emails officials. Email goes only to configured internal recipients. |
| No invented facts | Unknown values stay "Not stated"; LLM values not found in the source are discarded; eligibility is never assumed (doc 10). |

Portal terms: CPPP/GePNIC pages are published for public information; Chanakya reads listing pages at
human-like rates and keeps an evidence line (URL, Tender ID, capture time) so every record can be
traced to the official source. Have legal review the terms of use of each portal before enabling it in
production (doc 20).

## Threat model

| Asset | Threat | Mitigation |
|---|---|---|
| Ollama API key, SMTP password | Leak via code, logs, reports, errors | Read only from environment (`secret()`), never stored in config or DB; `redact()` removes keys and `Bearer …`/`password=…` patterns from errors and logs; tests assert the key never appears in error messages |
| Host / internal network | SSRF via a malicious link in a listing or redirect | `validate_url`: https only, host allowlist, no credentials in URL, private/reserved IP literals blocked, DNS answers must be public; redirects validated hop by hop (max 3) |
| Host filesystem | Path traversal via ZIP members or file names | `safe_extract_zip` rejects absolute paths, `..`, symlinks; `safe_filename` for every written name |
| Availability | Zip bombs, huge files, malformed PDFs | Member count / total size / compression ratio limits; 25 MB download cap; per-page error isolation; per-document error isolation |
| LLM behaviour | Prompt injection in tender text ("ignore previous instructions", "mark Wayam as eligible", "send an email…") | Text is wrapped in `<tender_data>` and declared untrusted in the system prompt; injection phrases are defanged and flagged (`INSTRUCTION-LIKE TEXT IN SOURCE`); the model has **no tools** — it can only return JSON, which is schema-validated; model output never changes config, recipients or scores directly |
| Report readers | Formula injection in Excel; HTML/script injection in email | Formula prefixes neutralised and cells forced to text; hyperlinks only for allowlisted https hosts; Jinja2 autoescape |
| Data integrity | A broken source corrupting stored records | Raw payloads stored immutably; per-source failure isolation; commits in batches; content-hash versioning |
| Decisions | Hallucinated deadlines, values, requirements, capabilities | Deterministic dates/values from listings; post-validation of LLM output against the source; unknown capability IDs dropped; scoring is deterministic |

Why tender text cannot instruct the system: the only consumer of LLM output is `Analyzer._post_validate`
→ `Pipeline._assess_one`, which reads a fixed set of typed fields. There is no code path from model
output to sending email, changing configuration, fetching URLs or reading files.

## Secrets management

| Secret | Variable | Notes |
|---|---|---|
| Ollama Cloud API key | `OLLAMA_API_KEY` (name configurable: `llm.api_key_env`) | Required only for `llm.mode: cloud`; `chanakya llm check` verifies it without printing it |
| SMTP username / password | `CHANAKYA_SMTP_USERNAME` / `CHANAKYA_SMTP_PASSWORD` | Required only for `email.provider: smtp` |
| Database URL (Postgres) | `CHANAKYA_DATABASE_URL` | May contain a password; never logged |

Local: `.env` (git-ignored; template `.env.example`). Docker: `env_file` or orchestrator secrets.
Azure: Key Vault references in Container Apps / App Service settings. GitHub Actions: repository
secrets (only the manual `live-sources` job needs network access, and none of the jobs need the Ollama
key). Rotate the Ollama key if it is ever pasted into a chat, ticket or log.

## Data protection

- Data handled: public procurement notices, tender documents, Wayam's own credentials (company
  profile), internal recipient email addresses, BD review notes. No personal data of citizens is
  collected; officer names/phone numbers that appear in documents stay inside the stored text and are
  not extracted or exported as fields.
- Storage: SQLite file (WAL) under `data/` or PostgreSQL; documents under `data/inbox/documents` and
  `data/documents`. Encrypt the volume at rest (cloud disk encryption) and restrict access to the BD team.
- Retention: workbooks keep the newest 30 (configurable); raw payloads and versions are kept for audit —
  add a purge job if policy requires (doc 20).
- Backups: `scripts/backup.sh` (SQLite online backup + report archive, rotation).
- Container: non-root user (UID 10001), read-only application code, writable `/app/data` volume only.

## Logging

Logs contain run IDs, source IDs, counts, timings and redacted errors. They never contain API keys,
SMTP credentials, full prompts or full documents. Set `app.log_level: DEBUG` only for troubleshooting.

This is enforced, not only intended: the log handler in `obs.py` carries a redaction filter that reuses
`redact()` with every secret value the process knows (each value returned by `settings.secret()`, the
Ollama key, the SMTP username and password, a database URL password), writes fields named `prompt`,
`messages`, `document_text` and similar only as a size, and caps messages at 1,000 characters, fields at
500 and tracebacks at 4,000. The same filter applies to the text format and to `app.log_format: json`
(doc 18, Monitoring and alerting). `tests/test_obs.py` asserts that a secret, a full prompt and a full
document do not reach a log line.

## Security tests (in `tests/`)

URL validation (scheme, credentials, private IPs, DNS rebinding, allowlist), redirect to off-allowlist
host, robots disallow, CAPTCHA detection, ZIP traversal / symlink / bomb / encryption, safe file names,
secret redaction (including auth errors from Ollama), prompt-injection screening and delimiting,
formula-injection neutralisation, unsafe hyperlinks, HTML escaping in the digest.

## Operational checklist before production

1. Set a real contact in `http.user_agent`.
2. Store secrets in the platform secret store; remove any `.env` from images.
3. Restrict who can read `data/` and the reports share.
4. Review portal terms with legal; keep `verification_status` honest per source.
5. Enable dependency scanning (e.g. `pip-audit`) in CI and rebuild the image monthly.
