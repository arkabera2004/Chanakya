# Crawling Tool Evaluation — Nine Providers

This is the implementation-stage companion to the earlier benchmark-phase tool evaluation in
[CRAWLING_BENCHMARK_SPECIFICATION.md](CRAWLING_BENCHMARK_SPECIFICATION.md) (which concluded,
correctly, that CPPP/GePNIC need no JS rendering or managed service). This document instead
evaluates the nine providers added as an independent, optional capability layer, based on
hands-on implementation evidence from this phase, not vendor claims.

| Provider | Local or managed | Real evidence this phase | Fit for Chanakya today |
|---|---|---|---|
| Scrapling | Local (parsing only) | `Selector` works standalone; `Fetcher` pulls in a stealth dependency chain (`patchright`) | Good for ad hoc selector-based extraction on pages already fetched compliantly; its own retrieval layer is unusable here |
| Crawlee | Local | Real HttpCrawler run; one real bug found (storage/event-loop binding) and fixed | Solid single-request HTTP fetcher with built-in retry/session handling once storage is isolated per call |
| Crawl4AI | Local (own Playwright) | Real local crawl, clean Markdown output | Good when Markdown/cleaned content (not raw HTML) is the desired shape; LLM-assisted mode deliberately not wired to avoid a second LLM provider path |
| Scrapy | Local (subprocess) | Real subprocess run; one real API-version bug found (`start_requests` → `start()`) and fixed | Works, but the subprocess-per-request overhead (process spawn, ~0.5-1s) makes it the slowest of the five local options for single-page use; its real strength (spiders, pipelines, middleware) is unused at this granularity |
| Playwright | Local browser | Real headless Chromium render | Correct choice *if* a source ever needs JS rendering — none do today (confirmed by the earlier benchmark) |
| Firecrawl | Managed (API key) | **Live call succeeded**, real schema confirmed | Genuinely useful for ad hoc clean-Markdown scraping of non-portal pages (e.g. a company website for enrichment) — not needed for CPPP/GePNIC |
| Apify | Managed (API key) | **Auth live-verified**, free endpoint | Only as useful as the Actor chosen to run — no procurement-relevant Actor was selected or run this phase; this is a product decision, not a technical blocker |
| Browserless | Managed (API key) | **Live call succeeded** via REST `/content` | Simple, works; the documented WebSocket/CDP path did not work during exploration (endpoint appears to have moved), so REST is the supported path going forward unless re-investigated |
| Zyte | Managed (API key) | None — no key provided | Cannot be evaluated with real evidence; implemented against documented schema only |

## Conclusion

No provider in this set is needed for CPPP or GePNIC today — both are plain server-rendered HTML,
confirmed twice now (once in the crawling-reliability benchmark, once again implicitly here: none
of the five local providers revealed a parsing limitation the existing `bs4`/`lxml`-based
connectors don't already handle). The nine providers are a capability investment for **future**
sources or enrichment work outside the current CPPP/GePNIC scope, not a replacement for anything
working today. If a future source genuinely needs managed scraping or browser rendering, this
phase's work means the integration point (the shared `AcquisitionProvider` contract) already
exists and is tested — only a new source configuration and a product decision on provider choice
would be needed, not new plumbing.
