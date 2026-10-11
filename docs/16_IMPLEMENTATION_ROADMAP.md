# 16: Implementation Roadmap

Durations are planning estimates for one engineer with part-time BD input, not commitments.

## Status today (2026-10-10)

| Area | State |
|---|---|
| Research (Wayam, sources, open source, Ollama) | Done: docs 01 to 04 |
| CPPP connector (central / state / GeM-mirror feeds) | Implemented and tested on a real captured page; **live run from the deployment network pending** |
| GePNIC connector (eprocure / etenders / state portals) | Implemented; tested on real home-page rows and a **synthetic** active-tender page; disabled by default |
| Manual import (CSV/XLSX/YAML + documents) | Implemented and tested: the route for GeM, IREPS, PSU sites and all documents |
| Dedup, versions, change events, expiry, retenders | Implemented and tested |
| Rules, exclusions, lexical semantics | Implemented; benchmark + live snapshot (23/24) |
| Documents, sections, eligibility extraction | Implemented and tested (PDF/DOCX/XLSX/HTML/ZIP; OCR optional) |
| Ollama cloud/local provider, budget, cache, validation | Implemented; tested against mocked HTTP. **Not yet called with the real key** |
| Scoring, priorities, flags | Implemented and tested |
| Excel (8 sheets), digest, SMTP/file providers, idempotency | Implemented and tested (SMTP mocked) |
| CLI, scheduler, health check, backup, Docker, CI | Implemented; Docker image **not built here** (no daemon) |

## Desk

The web console and the API work that follows it are planned in [21: Desk UI and API plan](21_DESK_UI_AND_API.md). That plan starts by showing the Live book and keeping a single writer, then the lead filters and the icon map, then moving `chanakya watch` behind the desk.

## Built on 2026-10-10, beyond the status table above

Each item is built and covered by tests. What has not been verified is stated beside it.

| Roadmap item | State |
|---|---|
| Phase 3: new-P1 alert, weekly digest | Built. File provider only; no real SMTP send. |
| Phase 3: corrigendum watch | Corrigendum alert and deadline reminders built. `check_updates` stays a no-op: there is no public per-notice lookup. The daily scan is incremental, so a corrigendum on an older notice is only seen by `run --since` or a manual import. A wider re-read of listings for tracked notices is not built. |
| Phase 4: PostgreSQL + Alembic | Built. Passes on SQLite and a local PostgreSQL 18. CI Postgres job, Docker builds and `pip-audit` have never run. Live database not stamped. |
| Phase 4: JSON logs and metrics | Built. Alert rules are examples, not loaded into a monitor. |
| Phase 4: load test with a 30-day backfill | Not done. Needs a long live crawl. |
| Phase 5: documents without the CLI | Upload on the lead page, with "Analyse this notice". Shared-folder sync not built. |
| Phase 5: BOQ table extraction | Built for ruled tables. Not tested on real tender BOQs; unruled and scanned tables not handled. |
| Phase 5: OCR in the image, Hindi pack | Not done. |
| Phase 6: desk and API | Built: Desk, Leads, Products, Passes, Sources, Costs, Profile. No SSO: the API listens on localhost only. |
| Phase 7: weight proposals, win/loss | Built. Win history in scoring is off by default. No real labels exist yet. |
| Phase 7: award-notice monitoring | Parser, import and report built. Automatic scan is not possible: the CPPP results page is a CAPTCHA form. Parser unverified against a real page. |

## Phase 1: Go-live readiness (week 1)

| Task | Owner | Exit criterion |
|---|---|---|
| `chanakya sources test` for `cppp_central`, `cppp_states`, `cppp_gem` from the production network | Eng | Rows parse; set `verification_status: verified_working`, or record the failure |
| `chanakya llm check` + one `run --llm-mode cloud --dry-run` with the real key | Eng | Both models listed; usage rows show real token counts; cost within budget |
| Fill `company_profile` with **verified** figures (turnover, years, certifications, MSME/DPIIT, CERT-In) | BD / Finance | Values signed off; eligibility moves beyond "Unknown" where criteria are stated |
| Set `http.user_agent` contact, SMTP relay, sender, recipients | Eng | `chanakya email --dry-run`, then one real send to the BD list |
| Build and push the Docker image; schedule the daily job (07:30 IST) | Eng | Container job runs; `healthcheck` green; backup job scheduled |

## Phase 2: Shadow operation and calibration (weeks 2 to 3)

- Daily runs with the digest going to BD only; BD records decisions with `chanakya review` (≥100 labels).
- Weekly calibration: KB phrases and exclusions first, then thresholds, then weights (doc 10).
- Track recall misses reported by BD → add phrases or enable embeddings.
- Exit: P1/P2 precision ≥ 0.7 on BD labels; no known relevant active notice missed in two consecutive weeks;
  monthly LLM cost on track for < $5.

## Phase 3: Coverage and alerts (weeks 4 to 6)

- Enable GePNIC detail enrichment (`fetch_detail`) for eprocure/etenders after live verification
  (value, EMD, category, description → better scoring, more deep analyses without documents).
- Add state GePNIC portals that BD cares about (one config entry each; test, then enable).
- Immediate P1 alert (`email.alert_on_new_p1`) and weekly digest (`email.digest: weekly`). **Built 2026-10-10**, tested with the file provider; not yet sent through a real SMTP relay.
- Corrigendum watch: alert on material changes to tracked notices (see the table above for what the scan can and cannot see).
- Exit: ≥5 sources verified; alerts in production.

## Phase 4: Hardening (weeks 6 to 8)

- PostgreSQL + Alembic migrations; move from SQLite when more than one writer or the dashboard arrives.
- Structured JSON logs and metrics (run duration, rows per source, LLM cost) to the platform monitor;
  alert on exit codes 1 to 3 and on `healthcheck` failures.
- `pip-audit` in CI; monthly image rebuild.
- Load test with a 30-day CPPP backfill (`--since`), confirm runtime and cost.

## Phase 5: Documents and depth (weeks 8 to 12)

- BD workflow for documents: a shared folder synced to `inbox/documents/` (OneDrive/SharePoint
  connector or rclone), so deep analysis runs without CLI use.
- Direct-download connectors only where the publisher offers documents without CAPTCHA/login
  (some PSU and state sites): verify terms first.
- OCR in production image if scanned documents are common; Hindi language pack.
- Better table extraction for BOQs (pdfplumber table mode or Docling for selected documents).

## Phase 6: Dashboard and API (quarter 2)

- Read-only FastAPI over the same database with SSO; opportunity list/detail with evidence and history;
  review buttons writing `user_review_feedback`.
- Saved views per product owner (Product-Wise sheet as a page).

## Phase 7: Learning loop (quarter 2+)

- Use review labels to tune weights automatically (logistic regression on component scores): always
  shown as a proposal for a human to accept, never auto-applied.
- Win/loss tracking (`Won`/`Lost` statuses) feeding the strategic component.
- Award-notice monitoring (CPPP "Results of Tenders") for competitor and price intelligence.

## Explicitly out of scope

Automatic bid submission, contacting officials, CAPTCHA/login automation, buying third-party scraped
data of unclear provenance.
