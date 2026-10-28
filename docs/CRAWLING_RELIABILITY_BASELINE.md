# Crawling Reliability Baseline

Status: evidence-based snapshot taken during Phase 2 of the crawling-reliability program.
Cross-references: [20_RISKS_AND_OPEN_QUESTIONS.md](20_RISKS_AND_OPEN_QUESTIONS.md),
[16_IMPLEMENTATION_ROADMAP.md](16_IMPLEMENTATION_ROADMAP.md). This document does not repeat their
content — only the acquisition-reliability facts needed to plan the next phase.

## A. Repository baseline

- Branch `main`, HEAD after this phase's commits: `fix(tests)` → `test(http)` → `test(benchmark)` →
  this docs commit (see `git log --oneline -4` for exact hashes).
- Python 3.12 (project `.venv`), dependencies per `pyproject.toml` (httpx 0.28.x, SQLAlchemy 2.x,
  FastAPI 0.115+, dev extras: pytest, respx, ruff).
- CI (`.github/workflows/ci.yml`): lint (ruff) → capability-KB validation → `pytest -q` (offline,
  fake LLM provider) → benchmark regression guard (`chanakya benchmark`, a *relevance-scoring*
  benchmark — see note in Section D) → a separate Postgres job (migrations + demo run) →
  `pip-audit` → Docker build/smoke test → an opt-in `workflow_dispatch`-only live-portal check.
- Before this phase: 286/287 offline tests passed. The one failure
  (`tests/test_desk_api.py::test_the_desk_reads_the_config_named_in_the_environment`) was a **test
  isolation defect** (Verified): it was the only test calling the real, unpatched `project_root()`
  and asserting against the real repo-root `config/chanakya.yaml`, which is gitignored and has
  never been committed (`git ls-files` / `git log --all -- config/chanakya.yaml` both empty); every
  sibling test uses a `tmp_path`-scoped config. Fixed by monkeypatching `project_root()` in that one
  test — `load_settings()`'s fail-fast-on-missing-explicit-path behavior was not touched. After the
  fix: full suite 321 passed, 5 skipped, 0 failed (count differs from the pre-phase 287 because this
  phase added 8 new tests across two commits).

## B. Connector baseline (Verified via existing tests + this phase's fixture inventory)

| Connector | File | Access | Entry point | Fixtures | Tests |
|---|---|---|---|---|---|
| CPPP | `connectors/cppp.py` | HTTP/HTML, no browser | `CPPPConnector.parse_listing`/`fetch_listing`/`discover` | `html/cppp_central_p1.cppp.html` (**real** capture) | `test_connectors.py` (9 cases) |
| GePNIC | `connectors/gepnic.py` | HTTP/HTML, home-page widget only | `GePNICConnector.parse_listing`/`apply_detail` | `html/gepnic_home.gepnic.html` (**real**), `html/gepnic_active.gepnic.html` (labelled synthetic tender IDs despite the filename — see Section E), `html/gepnic_detail.html` | `test_connectors.py` (6 cases) |
| CPPP Awards | `connectors/cppp_awards.py` | HTTP/HTML, `verification_status: candidate` | `CPPPAwardsConnector.parse_page` | `html/cppp_awards_results.synthetic.html` (synthetic, explicitly marked), `html/cppp_results_captcha_form.real.html` (**real**) | `test_awards.py` (13+ cases) |
| Manual | `connectors/manual.py` | CSV/XLSX/YAML inbox | `ManualImportConnector` | `manual/manual_import_template.csv` | `test_connectors.py` |

All four connectors share `BlockedError` on CAPTCHA detection (`has_captcha` in `base.py`) —
Verified for both CPPP (`test_cppp_captcha_only_page_is_blocked`) and the awards connector
(`test_real_captcha_form_page_raises_blocked`), and independently re-verified in this phase by
running `CPPPConnector().parse_listing()` directly against `cppp_results_captcha_form.real.html`
(raises `BlockedError: CPPP listing replaced by a CAPTCHA page; not bypassing`).

## C. Acquisition failure inventory (Verified — categories actually distinguished today)

| Failure category | Distinguished? | Evidence |
|---|---|---|
| HTTP 429 with bounded retry | Yes | `test_http_retries_on_429_then_succeeds` |
| HTTP 403 / 5xx | Yes, 403→`BlockedError` not retried, 5xx→`FetchError` after bounded retries | `test_http_403_and_5xx` |
| robots.txt disallow | Yes → `BlockedError` | `test_http_robots_disallow_is_respected` |
| Redirect validated per hop, SSRF-blocked redirect | Yes → `SecurityError` | `test_http_redirect_off_allowlist_blocked` |
| Empty response body | Yes → empty record list, not an error | `test_cppp_empty_results` |
| Changed/unexpected page structure | Yes → `StructureChangedError` | `test_cppp_changed_layout_raises` |
| CAPTCHA / access restriction | Yes → `BlockedError` | Section B |
| Malformed/partial rows | Yes, skipped and counted, valid rows kept | `test_cppp_skips_malformed_rows`, `test_malformed_rows_are_skipped_and_counted` |
| Duplicate records (same source, cross-source, fuzzy title) | Yes | `test_dedup_key_rules`, `test_cross_portal_duplicate_is_merged`, `test_fuzzy_title_duplicate_same_closing` |
| Corrupt PDF/DOCX/XLSX | Yes, caught, flagged, does not crash | `test_corrupted_documents_do_not_crash` |
| Malicious/zip-bomb archive | Yes → rejected without crash | `test_malicious_zip_rejected_without_crash`, `test_safe_zip_rejects_bomb_and_member_count` |
| Scanned (image-only) PDF | Yes → flagged `needs_manual_review` | `test_scanned_pdf_flagged_for_manual_review` |
| Content-type/magic-byte mismatch | Yes, detected by magic bytes not extension | `test_detect_type_by_magic` |
| **Oversized document download** | **Was not regression-tested before this phase** — logic existed (`http.py::PoliteClient.download`, declared-length pre-check + streaming cutoff, partial-file cleanup) but no test exercised either branch | Fixed this phase: `test_download_rejects_declared_oversize_without_writing_file`, `test_download_rejects_oversize_discovered_while_streaming` (`tests/test_connectors.py`) |
| Table-cell injection / forged markers | Yes | `test_table_cells_cannot_forge_markers_and_are_screened` |
| Corrupt page geometry (PDF table extraction) | Yes → falls back to plain text | `test_corrupt_page_geometry_falls_back_to_plain_text` |

No category required a new exception class; the existing `FetchError`/`BlockedError`/
`StructureChangedError`/`SecurityError` hierarchy already distinguishes retryable from
non-retryable, and blocked-by-policy from genuinely failed.

## D. Current reliability conclusions

- **CPPP and GePNIC listing extraction: Verified** against real captured HTML, with correct field
  extraction, pagination, and CAPTCHA refusal. Confidence: high. Not yet validated against a live,
  un-captured, present-day request (the CI live-check job is `workflow_dispatch`-gated and was not
  run in this phase).
- **CPPP Awards: Code-inspected, Simulated for the happy path**. The live "Result of Tenders" page
  is CAPTCHA-gated in the one real capture available (`cppp_results_captcha_form.real.html`); the
  parser's happy path is only proven against a synthetic fixture. `verification_status: candidate`
  in `config.py::AwardsSettings` already reflects this honestly — no change needed.
- **HTTP client (`http.py`): Verified** for retry/backoff, robots.txt, redirect-SSRF validation, and
  (as of this phase) download size limits. Not covered: a response-size cap on `get_text()` for
  very large non-download text responses — mitigated today by every known listing page being small
  and bounded by `max_pages`, but not enforced at the HTTP layer itself. Flagged as a backlog item,
  not fixed in this phase (no reproducible failure exists to fix against).
- **An existing fixture-naming inconsistency** (not a defect): `html/gepnic_active.gepnic.html`
  contains synthetic tender IDs (`2026_SYNRLY_900001_1`, etc.) but, unlike
  `cppp_awards_results.synthetic.html`, its filename has no `.synthetic.` marker. Worth a one-line
  rename in a future housekeeping pass; not touched here to avoid an unrelated diff.
- **Existing relevance-scoring benchmark** (`tests/fixtures/benchmark/labeled_tenders.yaml`,
  `chanakya benchmark` CLI) is a **different benchmark** from the one added in this phase — it
  measures whether the capability-matching/scoring pipeline correctly labels tenders
  relevant/irrelevant, not crawling/extraction reliability. The two are complementary, not
  duplicates; see [CRAWLING_BENCHMARK_SPECIFICATION.md](CRAWLING_BENCHMARK_SPECIFICATION.md).
