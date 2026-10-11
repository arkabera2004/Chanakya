# 08 — Source Connector Design

## Contract (`connectors/base.py`)

```python
class TenderSourceConnector(ABC):
    supports_documents: bool; supports_detail: bool
    def discover(self, ctx: DiscoveryContext) -> Iterator[TenderRecord]   # newest-first, stops at ctx.since
    def fetch_listing(self, url: str | None, page_no: int) -> ListingPage # one page → records + next_url
    def fetch_detail(self, record) -> TenderRecord                       # optional enrichment
    def fetch_documents(self, record) -> list[DocumentRef]               # only access in {direct, manual}
    def normalize(self, raw: dict) -> TenderRecord
    def check_updates(self, keys) -> dict[str, str]                      # status re-verification (feeds)
    def health_check(self) -> HealthStatus                               # used by `chanakya sources test`
```

`DiscoveryContext` carries `since` (aware IST), `max_pages`, counters (`pages_fetched`, `malformed`),
`notes` and `stopped_reason` ("reached high-water mark", "last page", or "max_pages … coverage may be
partial", which is surfaced in the report).

Connectors only acquire and normalise. They never classify, call an LLM or write to the database.

## Shared parsing strategy

- **Header-driven tables**: `find_data_table` maps header text through aliases (`e-Published Date`,
  `Bid Submission Closing Date`, `Title/Ref.No./Tender Id`, `Organisation Chain`…) so column order
  changes do not break parsing; a missing required column raises `StructureChangedError`.
- **CAPTCHA detection** looks at text *and* attributes (alt/title/name/id/src). A page that becomes a
  CAPTCHA raises `BlockedError` — the run reports the source as restricted; nothing is bypassed.
- **Dates** are parsed day-first as IST; unparseable dates become `None`, never a default.
- **Malformed rows** (missing title/cells) are skipped and counted.

## CPPP (`connectors/cppp.py`) — MVP
- Feeds: `cpppdata` (central), `mmpdata` (states), `gemdata` (GeM mirror).
- Pagination: follow the page's own "Next »" link (base64 `url` parameter); `page_url()` exists for tests.
- Title cell: anchor text = title; remaining text = `/RefNo/TenderId`; Tender ID is the **last**
  segment because reference numbers contain slashes. Titles that equal the reference (CPWD style) are
  flagged `title_cell_is_ref`.
- Provenance: `official_url` = feed URL (stable), `detail_url` = captured link marked expiring,
  `evidence` = "<platform> listing (<url>), Tender ID X, captured <UTC time>".
- Documents: `captcha_required` → manual import.

## GePNIC (`connectors/gepnic.py`)
- One connector, many portals: `base_url` per source (eprocure, etenders, state portals).
- Listing `?page=FrontEndLatestActiveTenders&service=page` with a cookie jar (session links); falls back
  to the home-page "Latest Tenders" table for health checks.
- Title cell `[Title] [Ref][TenderID]`; organisation chain `A||B||C` → organisation + department path.
- Detail page (label/value table) adds value, EMD, category, tender category, location, description.
- Documents: CAPTCHA → manual.

## CPPP awards (`connectors/cppp_awards.py`): disabled, candidate

Award notices are outcomes, not opportunities. The connector is not a `TenderSourceConnector`, is not
in the registry, and has its own config block (`awards:`), model (`AwardRecord`), table (`awards`) and
commands (`chanakya awards scan | import | test | report`). It never feeds the tender pipeline and
makes no model calls.

**Verified** (2026-10-10, `PoliteClient` with the example config, 5 requests in total):

| Request | Result |
|---|---|
| `https://eprocure.gov.in/robots.txt` (twice, once per session) | 404, no robots policy published |
| `https://eprocure.gov.in/cppp/` | 200. "Result of Tenders" and "Bid Awards" both link to `/cppp/resultoftendersnew` |
| `https://eprocure.gov.in/cppp/resultoftendersnew` | Redirect to `/cppp/resultoftendersnew/cpppdata`, 200, 144,891 bytes |

- The page is titled "Results Of Tenders - Central" with a second tab for states (`.../mmpdata`, not
  fetched).
- It holds one POST form: organisation name, keyword, tender id, location, AOC published year
  (2021 to 2026), AOC status (Published AOC, Canceled AOC), an image CAPTCHA ("What code is in the
  image?") and Drupal hidden fields.
- It holds no table, no result row and no pager. Nothing is listed until the form is submitted with
  the CAPTCHA answer. Chanakya stopped there and does not submit it.
- Fixture of that page: `tests/fixtures/html/cppp_results_captcha_form.real.html` (real capture,
  trimmed, provenance in the header). `parse_page` raises `BlockedError` on it, and a scan ends as
  `blocked` after one GET.

**Assumed** (never observed, because results need the CAPTCHA):

- The layout of the results table. The parser is header-driven (aliases for AOC date, selected
  bidder, contract value, the combined `Title/Ref.No./Tender Id` cell, or separate tender id and
  reference columns) and is tested only against
  `tests/fixtures/html/cppp_awards_results.synthetic.html`, a SYNTHETIC page.
- That the listing states the bidder name and contract value at all. On the real portal these may sit
  on a per-tender AOC page. A missing column gives `None`, shown as "Not stated".
- That paging uses a "Next" link, as the active-tender listings do.
- That the states tab (`mmpdata`) is the same form.

**Behaviour**

- A table counts as an award table only if it has a title column and at least one award column, so an
  active-tender page cannot be read as awards (`StructureChangedError`).
- "No records found" gives an empty page; a CAPTCHA page with no table gives `BlockedError`; rows
  with no title, or with no bidder, date or value, are skipped and counted.
- `awards scan` takes the one-writer lock and refuses to run while `awards.enabled` is false.
  Exit codes: 0 ok, 3 blocked, 2 failed, 1 disabled or lock held.
- `awards import <file>` is the compliant route today: a person runs the search in a browser, saves
  the result page (`.html`) or prepares a CSV (`tender_id, ref_no, title, organisation, awarded_to,
  awarded_value_inr, award_date, source_url`), and the same parser stores it. No network.
- `awards test` is the awards equivalent of `sources test` (first page, no database writes).
- `awards report` prints (a) awards whose Tender ID equals a notice in the book, or whose reference
  equals one and the organisation does not differ, and (b) for awards whose title the rule classifier
  (`pipeline/classify.py`) rates CANDIDATE or STRONG: the most frequent winners and the value range
  per capability. Bidder names are printed as published. No claim is made about any company beyond
  what the award line states.
- Storage: one row per (tender, bidder), keyed `tid:<tender id>|<hash of bidder>` or a hash of
  reference, organisation, title and bidder. Re-reading the same line is a no-op; a changed value
  updates the row.

**To verify later**: a person saves one real result page; run `chanakya awards import` on it. If it
parses, replace the synthetic fixture with that capture (with provenance) and record the real column
names here. The status stays `candidate` and `enabled: false` unless CPPP publishes a listing that
needs no CAPTCHA and `chanakya awards test` passes from a real network.

## Manual import (`connectors/manual.py`) — MVP
- Reads every CSV/XLSX/YAML in the inbox; template `tests/fixtures/manual/manual_import_template.csv`.
- `document_paths` (`;`-separated, relative to the file) or `chanakya import <pdf|dir> --tender-id ID`
  attach documents; the deferred deep analysis resumes automatically on the next run.
- Used for GeM bids, IREPS, PSU sites and anything copied by BD.

## Fixture connector
Offline demos/tests: YAML/CSV records plus saved `*.cppp.html` / `*.gepnic.html` pages.

## Adding a connector (checklist)
1. Subclass `TenderSourceConnector`; implement `fetch_listing` (and `discover` if not page-based).
2. Use `self.client` (PoliteClient) only; add the host to `http.allowed_domains`.
3. Save one real page as a fixture with a provenance comment; write parsing tests (happy path, empty,
   CAPTCHA, layout change, malformed rows, pagination).
4. Register in `connectors/__init__.py`; add a disabled source entry with `verification_status: candidate`.
5. Run `chanakya sources test --source <id>` from the deployment network; then mark `verified_working`.

## Failure handling
| Failure | Behaviour |
|---|---|
| 429 / 5xx / timeout | Exponential backoff with jitter, honours Retry-After, bounded retries |
| 401 / 403 / robots disallow / CAPTCHA | `BlockedError` → source `restricted`, coverage note, no retry loop |
| Layout change | `StructureChangedError` → source failed, coverage note |
| Repeated failures | Circuit breaker: cooldown after N consecutive failures |
| Partial page set | Records committed every 25 rows; earlier records stay valid |
