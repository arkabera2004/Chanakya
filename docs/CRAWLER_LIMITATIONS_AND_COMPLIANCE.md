# Website Intelligence — Crawler Limitations and Compliance

States what this crawler will and will not do, and why, as a compliance reference — not an
aspirational policy document. Matches the existing project-wide stance in
`CRAWLING_RELIABILITY_BACKLOG.md`/`CRAWLING_TARGET_ARCHITECTURE.md` for the acquisition-provider
layer; this document covers the newer `chanakya.webcrawl` generic crawler specifically.

## Explicitly out of scope (by design, not by current limitation)

- **CAPTCHA solving or bypass of any kind.** A page that requires solving a CAPTCHA to view is
  reported as blocked; this crawler never submits a CAPTCHA response, solved or otherwise.
- **Browser fingerprint evasion or identity rotation.** There is no user-agent rotation, proxy
  rotation, or anti-detection measure anywhere in `chanakya.webcrawl`. The crawler identifies
  itself with a fixed, honest `User-Agent` string
  (`ChanakyaWebsiteIntelligenceBot/1.0 (+https://wayam.ai)`).
- **Crawling outside the scope the user configured.** The crawler never follows a link to a
  different domain than the website's own host (plus any explicitly configured additional
  allowed domains) — this is enforced by `chanakya.security.validate_url`, the same SSRF
  guard used elsewhere in this codebase, not a crawler-specific allowlist that could drift.
- **Automated LinkedIn scraping.** Not implemented, not planned as part of this crawler;
  LinkedIn integration options are research-only (`COST_ESTIMATION_REPORT.md` §C).

## What the crawler actually respects

- **`robots.txt`** — fetched once per crawl run from the site root; a disallowed path is
  skipped and recorded as `skipped_robots`, visible in the crawl monitor, never silently
  dropped.
- **Same-origin scope by default** — only the website's own hostname (plus any explicitly
  configured `extra_allowed_domains` in the website's `crawl_config`) is in scope. Off-scope
  links are recorded as `skipped_offsite`, not fetched.
- **Page and depth limits** — `max_pages` (default 50) and `max_depth` (default 2), configured
  per website; the crawl never runs unbounded.
- **File-size limits on downloaded documents** — 25 MB per document by default
  (`webcrawl.documents.DEFAULT_MAX_BYTES`); an oversized document is reported as failed with an
  explicit "too large" error, never partially downloaded.
- **Cancellation** — a `WebsiteCrawlJob.cancel_requested` flag is checked before every page is
  fetched; a cancelled crawl stops promptly and keeps whatever it had already collected.

## Live verification finding (2026-10-30)

A real (non-mocked) crawl against `https://nhai.gov.in/` — the brief's own named example site —
fetched the homepage successfully (HTTP 200) but discovered **zero further pages**: direct
inspection confirms the homepage's raw HTML (4.5 KB) contains no `<a href>` tags at all. NHAI's
site renders its navigation client-side (a JS single-page-app shell), which this
non-browser-rendering crawler (by design — see "Not yet done" in
`WEBSITE_INTELLIGENCE_ARCHITECTURE.md`) cannot see past. This matches this project's own prior
documented finding for CPPP's JS-migrated portal (`20_RISKS_AND_OPEN_QUESTIONS.md`) — it is a
real, structural limitation of a static-HTML crawler against a JS-rendered government site, not
a bug in this implementation. A real crawl against `https://example.com/` (a static test
domain) succeeded identically at the HTTP level, confirming the crawl/robots/page-persistence
pipeline itself works correctly end to end against real DNS and a real server — the zero-link
result on NHAI is specifically about JS rendering, not about the crawler being broken.

**Implication:** crawling a JS-rendered government site for real tender discovery needs a
browser-rendering fetch (the existing Playwright/Crawlee acquisition providers already do this
for connector-based sources) wired into `chanakya.webcrawl.crawler`'s page-fetch step, instead
of today's plain `httpx.Client.get`. This is a real next step, not a currently-working feature
mis-described here.

A second real crawl, against `https://eprocure.gov.in/eprocure/app` (static HTML, not a JS SPA,
`max_pages=8`), confirms the positive path: **8 pages discovered and fetched (all HTTP 200)**,
**1 document downloaded**, and **1 tender record extracted** with a capability match, all from
real network traffic against a real government portal. This is the same portal family
(GePNIC/CPPP) the existing `connectors/gepnic.py` was built against, and it confirms the
generic crawler's link discovery, same-origin scoping, and tender extraction work correctly
end to end on a real site whose homepage actually has static `<a href>` links — the NHAI
result above is specifically a JS-rendering limitation, not a general crawler defect.

## Known gaps (honest limitations, not policy choices)

- **No request-rate configuration surfaced yet.** `CrawlConfig` has no `request_delay_s` field
  today (unlike the design doc's original plan, which specified one) — the crawler currently
  issues requests back-to-back, bounded only by `max_pages`/`max_depth`. For a low-traffic
  pilot against a handful of sites this has not caused observed problems, but a polite,
  configurable delay between requests should be added before crawling any site at volume, as a
  matter of being a considerate automated visitor, not because anything currently breaks.
- **No per-domain concurrency limiting across simultaneous crawl jobs.** Each crawl job runs
  sequentially within itself; nothing yet prevents two crawl jobs against the same domain from
  running concurrently and doubling the request rate that domain sees. Not observed as a
  problem in testing (which uses mocked HTTP, not real concurrent crawls), but a real limitation
  to fix before running multiple simultaneous crawls against the same site in production.
- **Synchronous execution within one HTTP request** (see `WEBSITE_INTELLIGENCE_ARCHITECTURE.md`
  §5) means a crawl that runs long could hit a request timeout before finishing cleanly. This
  is a performance/architecture limitation, not a compliance one, but it's worth knowing before
  pointing this at a very large site.

## If a target site blocks access

This project's response to a blocked or CAPTCHA-gated site is to **report that honestly** in
the crawl monitor (the Pages tab shows `skipped_robots` or a failed fetch with its HTTP status)
and let an authorized user decide how to proceed — not to attempt to work around the block.
Options for a legitimately blocked site: obtain the data through an approved official access
method, or import documents manually through the existing `chanakya.connectors.manual` path.
