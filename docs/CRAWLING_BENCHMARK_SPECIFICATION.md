# Crawling Benchmark Specification

Implementation: `tests/fixtures/benchmark/crawling_cases.yaml` (manifest) +
`tests/test_crawling_benchmark.py` (harness). Baseline facts: see
[CRAWLING_RELIABILITY_BASELINE.md](CRAWLING_RELIABILITY_BASELINE.md). This is a **different**
benchmark from `tests/fixtures/benchmark/labeled_tenders.yaml` / `chanakya benchmark`, which scores
capability-matching relevance, not crawling/extraction correctness — the two are complementary.

## Tiers

| Tier | What it exercises | Status |
|---|---|---|
| Fixture (this benchmark) | Real connector `parse_listing`/`parse_page` functions against committed HTML fixtures, in-process, no network | **Implemented and executed this phase** |
| Local integration | `PoliteClient` against `httpx.MockTransport` (retries, redirects, download limits, robots.txt) | **Already implemented** in `test_connectors.py`'s `test_http_*` cases — not duplicated by this benchmark |
| Authorized live-source | Real request to `eprocure.gov.in`/CPPP mirrors | **Designed, not executed in this phase** — gated behind the existing `workflow_dispatch`-only CI job and `chanakya sources test`; out of scope per the "no live crawling without separate authorization" rule |
| Optional third-party provider | N/A — no managed scraping service is in use (see tool evaluation below) | Not applicable |

## Dataset

6 cases in `crawling_cases.yaml`, each reusing an already-committed fixture and an expected
outcome copied from an existing proven test assertion (never invented):

| Case ID | Fixture origin | Connector | Expected outcome |
|---|---|---|---|
| `CPPP-VALID-001` | real | cppp | 10 records, all required fields present |
| `GEPNIC-HOME-VALID-001` | synthetic (mislabelled filename, see baseline §E) | gepnic | 3 records |
| `GEPNIC-HOME-VALID-002` | real | gepnic | 4 records |
| `CPPP-CAPTCHA-BLOCKED-001` | real | cppp | `BlockedError` |
| `AWARDS-CAPTCHA-BLOCKED-001` | real | cppp_awards | `BlockedError` |
| `AWARDS-SYNTHETIC-VALID-001` | synthetic | cppp_awards | 6 records, `award_date`/value legitimately `None` on some rows |

### Coverage against the 25-category brief

Already covered by pre-existing tests (not duplicated here; see baseline §C for the exact test
names): valid CPPP/GePNIC listing, missing/malformed rows, empty response, malformed HTML/changed
layout, CAPTCHA/restricted page, HTTP 429/5xx/transient-then-success, SSRF-rejected redirect,
permitted redirect, valid/corrupt PDF, valid BOQ/spreadsheet, document with no meaningful data
(`test_page_without_table_is_unchanged`), misleading text not interpreted as valid data
(`test_boxes_and_frames_are_not_tables`, `test_table_cells_cannot_forge_markers_and_are_screened`),
cross-source/fuzzy duplicate tenders, syntactically-valid-but-incomplete records
(`test_cppp_skips_malformed_rows`). Closed this phase: oversized/unsupported document (declared and
streamed). Not covered by any fixture today, and not added in this phase for lack of a concrete
need: unusual-but-valid Unicode text, unexpected (non-document) content-type on a listing response.
Both are tracked in [CRAWLING_RELIABILITY_BACKLOG.md](CRAWLING_RELIABILITY_BACKLOG.md).

## Metrics — baseline run (this phase, offline, `pytest tests/test_crawling_benchmark.py -v`)

| Metric | Result |
|---|---|
| Retrieval success rate | N/A — this tier works from in-memory HTML; retrieval is covered by the separate, already-passing `test_http_*` tier (not re-measured here) |
| Extraction success rate | 6 / 6 cases produced the expected outcome (4 `extracted`, 2 correctly `blocked`) |
| Required-field completeness | 10/10 + 3/3 + 4/4 + 6/6 = 23/23 records had every listed required field present and non-empty |
| Exact field accuracy | Not separately measured beyond required-field presence in this pass — record counts and a subset of fields are checked; full per-field diffing against ground truth was judged not worth the added fixture/maintenance cost for this phase (see backlog) |
| Duplicate handling | N/A for this tier — covered by `test_dedup_key_rules`/`test_cross_portal_duplicate_is_merged` (pipeline-level, not in this manifest) |
| Failure classification accuracy | 2 / 2 CAPTCHA cases correctly raised `BlockedError` (counted as a correct security-policy outcome, not a crawl failure) |
| Regression count | 0 — first run, no prior baseline to regress against |
| Processing latency | 0.15s for all 6 cases (machine-specific; not asserted as a threshold, recorded for trend only) |

**Not measurable in this phase:** cost per successfully processed record (no LLM/paid calls made),
provenance completeness as a standalone metric (provenance fields exist on every record type per
the data-model audit but were not independently re-verified field-by-field here).

## Execution

```
pytest tests/test_crawling_benchmark.py -v
```

Deterministic, offline, no network, no email, no DB writes. Non-zero exit on any case regression;
failing case IDs appear directly in pytest's own output (e.g.
`FAILED tests/test_crawling_benchmark.py::test_crawling_benchmark_case[CPPP-VALID-001]`), satisfying
the "stable, actionable failure identification" requirement without a bespoke runner.

## Tool evaluation (evidence-gated)

No case in this benchmark, and no connector in the baseline audit, shows evidence that CPPP or
GePNIC requires JavaScript rendering — both are server-rendered HTML tables. Conclusion: **retain
httpx + BeautifulSoup/lxml; do not add Playwright, Scrapy, Crawlee, Crawl4AI, Scrapling, or a
managed scraping service in this phase.** The concrete trigger that would justify revisiting this:
a confirmed source where a required field only renders via client-side JavaScript (none observed
yet, including in the awards workflow — its blocker is CAPTCHA, which browser automation does not
and must not bypass).
