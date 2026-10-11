# 17 — Test Strategy

## Principles

1. **Offline by default.** The test suite never touches a government portal, Ollama or an SMTP server:
   HTTP is mocked with `httpx.MockTransport`/respx, the LLM with `FakeProvider` (scripted or heuristic),
   email with the file provider or a mocked `smtplib`.
2. **Real data where it matters, labelled synthetic data elsewhere.** Parser fixtures come from pages
   observed on 2026-10-10 (structure reconstructed, values as published). Anything invented is marked
   `SYNTHETIC` in the file name, a header comment, the record's platform/evidence fields, or the
   benchmark's `origin` field — never presented as a real tender.
3. **Assert behaviour, not implementation.** e.g. "a repeat run makes zero LLM calls", "an unrelated
   notice cannot reach P3 on value and deadline", "a hallucinated value is discarded".
4. **Every bug gets a regression test** (e.g. plural matching of "mes" ≠ "mess", the long-document
   input-budget overflow, net-worth extraction, config typos).

## Suite (127 tests, ~11 s)

| File | Tests | Covers |
|---|---|---|
| `test_timeutil_security.py` | 36 | IST parsing (formats, day-first, unparseable → None), storage round-trip; URL validation (scheme, credentials, private IPs, DNS rebinding, allowlist); safe file names; ZIP traversal, absolute paths, symlinks, bombs, member counts, encrypted members; formula-injection neutralisation; safe links; secret redaction; injection screening; strict config (typos rejected) |
| `test_connectors.py` | 18 | CPPP listing parse (real page), pagination + high-water mark stop, GeM feed, CAPTCHA page → blocked, layout change → `StructureChangedError`, empty results, malformed rows; PoliteClient retries on 429, robots disallow, 403/5xx, off-allowlist redirect; GePNIC active list (synthetic), home rows (real), detail enrichment (synthetic); manual YAML/CSV/XLSX import with malformed rows |
| `test_store_documents.py` | 15 | Dedup keys; new/unchanged/updated + deadline change events; cross-portal merge; fuzzy duplicate; retender detection; expiry; PDF text extraction with sections and page citations; page limits and quality; scanned PDF → manual review; corrupted files; DOCX/XLSX/HTML/ZIP; malicious ZIP; magic-byte detection |
| `test_matching.py` | 23 | Road works mentioning "monitoring" not a candidate; weak words never enough; strong video-analytics match; strong phrase rescues an excluded title; civil construction of a command centre not strong; synonyms/overlaps counted once; plurals; hardware supply capped; high-recall net; EOI kept; KB validation; lexical semantics; eligibility extraction (incl. net worth), unknown profile never assumes eligibility, profile-driven assessment, overall status; score breakdown sums; expired/rejected → P4; relevance gate; flags; configurable weights; embedding cache |
| `test_llm.py` | 20 | Ollama cloud request shape (Bearer, no `format`) and usage parsing; local mode sends schema without auth; missing key fails fast; auth error never leaks the key; 429 retry then success; timeout; missing usage estimated; cost with cached rate; budget guard limits; JSON repair once; invalid twice → failed (not faked); hallucination removal (capability, value, evidence); value kept when present; injection screening; `<tender_data>` wrapping and candidate-only cards; cache hit skips provider; budget exhaustion recorded; auth failure disables further calls; long-document excerpt fits the input budget |
| `test_reporting_e2e.py` | 15 | Full run then incremental runs (0 assessments, 0 LLM calls on repeat; deadline change detected); deferred deep analysis resumes after document import; KB change triggers re-assessment; duplicate across sources + unavailable source → partial run, no corruption; budget limit enforced and reported; LLM disabled still produces a report; workbook structure (8 sheets, 35+ columns) and safety; email idempotency, dry run, SMTP auth failure, transient retries, HTML escaping, no recipients; unique delivery keys; CLI dry run end to end; benchmark funnel cheaper without losing recall |

Run: `pytest` (or `python -m pytest tests -q`). Lint: `ruff check src tests scripts` (config in
`pyproject.toml`).

## Fixtures

| Fixture | Origin |
|---|---|
| `tests/fixtures/html/cppp_central_p1.cppp.html` | Real: 10 rows observed on the CPPP `cpppdata` listing, 2026-10-10 (markup reconstructed from the observed table structure) |
| `tests/fixtures/html/gepnic_home.gepnic.html` | Real rows from the eprocure.gov.in home-page "Latest Tenders" table |
| `tests/fixtures/html/gepnic_active.gepnic.html`, `gepnic_detail.html` | **SYNTHETIC**, labelled — follow the GePNIC template; used for parser logic only |
| `tests/fixtures/manual/manual_import_template.csv` | Template with an `[EXAMPLE ROW — replace]` row |
| `tests/fixtures/benchmark/labeled_tenders.yaml` | 64 notices: 24 live-observed (with source), 25 synthetic relevant, 15 synthetic irrelevant — `origin` field on every record |
| Generated in tests | PDFs (reportlab), DOCX, XLSX, ZIPs (incl. malicious) built at test time |

## Validation beyond unit tests

| Activity | Command / file | Result (2026-10-10) |
|---|---|---|
| Live snapshot of real notices | `scripts/validate_live_snapshot.py` → `docs/validation/LIVE_SNAPSHOT_2026-10-10.md` | 23/24 decisions agree with labels; all 4 relevant notices detected (all already closed → P4) |
| Approach benchmark | `chanakya benchmark --out docs/validation/BENCHMARK.md` | Funnel P 1.00 / R 0.93 at 5% of the cost of LLM-on-everything |
| Demo end-to-end | `scripts/make_demo_data.py` + `chanakya -c config/chanakya.demo.yaml run --dry-run` | Exit 0, 8 notices, workbook + dry-run digest |
| Cost model | `scripts/cost_model.py` | Measured prompt sizes, monthly projection (doc 19) |

## Not yet tested (and how to close the gap)

| Gap | Plan |
|---|---|
| Live portal parsing from the production network | `chanakya sources test` in Phase 1; CI `live-sources` job (manual `workflow_dispatch`) |
| Real Ollama Cloud responses (JSON compliance of gpt-oss, actual token counts) | `chanakya llm check` + a dry run with `--llm-mode cloud`; then add 10 recorded responses as fixtures |
| Real SMTP relay | One real send in Phase 1 |
| Docker image build and smoke test | CI `docker` job (builds the image and validates the knowledge base inside it); extend with a demo run once the image is built in CI |
| PostgreSQL | CI job `test-postgres` runs this same suite on a PostgreSQL 17 service container. Locally: `pip install -e ".[dev,postgres]"`, then `CHANAKYA_TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/chanakya_test pytest`; every test gets its own schema (`tests/dbsupport.py`). Without the variable the suite runs on SQLite as before. The suite passed (317 tests) against an embedded PostgreSQL 18.4 on the build machine on 2026-10-10; **the CI job itself has not been seen running** (doc 18, "Not verified") |
| Model quality on real documents | Shadow phase with BD labels (doc 16) |

## CI gates (`.github/workflows/ci.yml`)

Tests on Python 3.12 and 3.13 · ruff · knowledge-base validation · benchmark run (must complete) ·
Docker build + in-container KB validation. The live-source job runs only on manual dispatch and never blocks merges.
