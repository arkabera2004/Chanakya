# Crawling Reliability Backlog

Status after this phase: no live code defect was found in `http.py`, the connectors, or
`documents/` — see [CRAWLING_RELIABILITY_BASELINE.md](CRAWLING_RELIABILITY_BASELINE.md) §D. The one
confirmed gap (untested download-size enforcement) was fixed and is marked **done** below. The
remaining items are measurement/coverage gaps, not defects, and are left for explicit
authorization rather than fixed speculatively, per the "only confirmed, evidence-based changes"
rule.

---

### CRAWL-001 — Test isolation defect in desk config-resolution test
- **Status:** done (commit `fadb40a`)
- **Defect/evidence:** `test_the_desk_reads_the_config_named_in_the_environment` asserted against
  the real, gitignored `config/chanakya.yaml` instead of a `tmp_path`-scoped file like every
  sibling test.
- **Files:** `tests/test_desk_api.py`.
- **Change:** monkeypatch `chanakya.server.project_root` to a `tmp_path` sandbox with its own copy
  of `chanakya.example.yaml`.
- **Verification:** `pytest tests/test_desk_api.py -q`; full `pytest -q`.
- **Risk:** low (test-only change).
- **Rollback:** revert commit `fadb40a`.

### CRAWL-002 — Untested download size-limit enforcement
- **Status:** done (commit `c04c82d`)
- **Defect/evidence:** `PoliteClient.download()` enforces a size cap on both the declared
  `Content-Length` and while streaming, with partial-file cleanup — but no test exercised either
  branch (`CRAWLING_RELIABILITY_BASELINE.md` §C).
- **Files:** `tests/test_connectors.py`.
- **Change:** two regression tests, one per enforcement branch.
- **Verification:** `pytest tests/test_connectors.py -q`; full `pytest -q`.
- **Risk:** low (test-only change; confirmed the existing production logic was already correct).
- **Rollback:** revert commit `c04c82d`.

### CRAWL-003 — Deterministic crawling-extraction benchmark harness
- **Status:** done (commit `536c05f`)
- **Problem solved:** no reproducible, numerator/denominator-reported baseline existed for
  connector extraction correctness and failure classification; only scattered unit tests.
- **Files:** `tests/fixtures/benchmark/crawling_cases.yaml`, `tests/test_crawling_benchmark.py`.
- **Change:** manifest + parametrized pytest harness over existing fixtures (no new fixtures
  needed beyond what CRAWL-002 already added).
- **Verification:** `pytest tests/test_crawling_benchmark.py -v`.
- **Risk:** low (additive, test-only).
- **Rollback:** revert commit `536c05f`.

### CRAWL-004 — Fixture naming inconsistency (`gepnic_active.gepnic.html`)
- **Status:** proposed
- **Problem:** the file contains synthetic tender IDs (`2026_SYNRLY_...`) but, unlike
  `cppp_awards_results.synthetic.html`, has no `.synthetic.` marker in its filename — inconsistent
  with the project's own stated convention.
- **Files:** `tests/fixtures/html/gepnic_active.gepnic.html` and its 2 referencing tests in
  `tests/test_connectors.py`.
- **Proposed change:** rename to `gepnic_active.synthetic.gepnic.html` (or similar) and update the
  two `FIXTURES / "html" / ...` references.
- **Dependencies:** none.
- **Tests:** existing tests continue to pass under the new path; no new test needed.
- **Verification command:** `pytest tests/test_connectors.py -q`.
- **Acceptance criteria:** filename self-documents as synthetic; no other fixture references break.
- **Risk:** very low (pure rename).
- **Commit message:** `chore(fixtures): mark gepnic_active fixture as synthetic in its filename`.
- **Rollback:** revert the rename commit.

### CRAWL-005 — No response-size cap on `PoliteClient.get_text()`
- **Status:** proposed
- **Problem:** `download()` enforces `max_download_mb`; `get_text()` (used for listing pages) has
  no equivalent cap — a listing endpoint that returned an unexpectedly huge body would be read in
  full. No reproducible failure exists today (every known listing page is small and bounded by
  `max_pages`), so this is a hardening item, not a bug fix.
- **Files:** `src/chanakya/http.py` (`PoliteClient.get_text`).
- **Proposed change:** stream and cap `get_text()` the same way `download()` does, reusing
  `settings.max_download_mb` or a new, smaller `max_text_mb` setting.
- **Dependencies:** none.
- **Tests:** one new case mirroring `test_download_rejects_oversize_discovered_while_streaming`.
- **Verification command:** `pytest tests/test_connectors.py -q`.
- **Acceptance criteria:** an oversized text response raises `FetchError` instead of being read in
  full; existing listing-page tests unaffected.
- **Risk:** low-medium — touches a hot path used by every listing fetch; needs careful review that
  no legitimate large-but-valid listing page is rejected.
- **Commit message:** `fix(http): bound get_text() response size like download()`.
- **Rollback:** revert the change; `get_text()` returns to unbounded reads.

### CRAWL-006 — Unicode edge-case fixture
- **Status:** proposed
- **Problem:** no fixture exercises unusual-but-valid Unicode (e.g. combining marks, non-Latin
  organisation names) through the CPPP/GePNIC parsers or document extraction.
- **Files:** new fixture under `tests/fixtures/html/`, one new test in `test_connectors.py`.
- **Dependencies:** none.
- **Tests:** the new test itself.
- **Verification command:** `pytest tests/test_connectors.py -q`.
- **Acceptance criteria:** parser extracts the Unicode fields byte-for-byte correctly; no
  normalization corruption.
- **Risk:** low.
- **Commit message:** `test(connectors): add a Unicode-edge-case listing fixture`.
- **Rollback:** revert the commit.

### CRAWL-007 — Decide `cppp_awards` long-term scope
- **Status:** decision needed (product owner)
- **Problem:** the live "Result of Tenders" page is CAPTCHA-gated in the one real capture available;
  the connector's happy path is only proven against a synthetic fixture.
- **Options:** (a) keep `verification_status: candidate`/disabled-by-default as-is and rely on
  manual import; (b) remove the connector and formalize manual-import-only; (c) invest in an
  authorized CAPTCHA-solving-free alternative (e.g. an official results feed/API) if one exists.
- **Dependencies:** none technical; purely a scope decision.
- **No code change proposed here** — tracked for the next phase once decided.

### CRAWL-008 — Postgres benchmark/test parity
- **Status:** carried over from the prior audit, not newly discovered
- **Problem:** the Postgres migration-parity test job is gated behind
  `CHANAKYA_TEST_DATABASE_URL` and only runs in a separate CI job, not the default `pytest -q`.
- **Proposed change:** run it locally once (`CHANAKYA_TEST_DATABASE_URL=... pytest tests/test_migrations.py tests/test_db_portability.py -q`) and report the result before deciding whether to promote it to the default suite.
- **Dependencies:** a local/CI Postgres instance.
- **Risk:** low to run; the decision to always-run it in CI has a minor CI-time cost tradeoff.
- **Commit message (if promoted):** `ci: run Postgres parity tests by default`.
