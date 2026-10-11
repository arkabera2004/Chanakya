# 03 — Open-Source Comparison

Versions, dates and licences below were read from PyPI and GitHub on **2026-10-10**.

## Tender-specific projects

| Project | Language / licence | Activity | What it does | Reuse verdict |
|---|---|---|---|---|
| [switchr24/mcp-india-tenders](https://github.com/switchr24/mcp-india-tenders) | TypeScript · MIT | 4 commits, 1 star, last README update Dec 2025 | MCP server exposing CPPP, eProc Rajasthan and Defence portal search in OCDS form | Too small and young to depend on; different language; useful as a reference for OCDS field naming. **Not reused.** |
| Apify actors (e.g. CPPP/GeM scrapers) | Proprietary SaaS | Active | Paid scraping of CPPP and GeM listings | Not open source, per-record pricing, opaque compliance. **Not used.** |
| Generic "government tender scraper" actors | Proprietary | Active | Multi-country adapters (incl. CPPP/GePNIC) | Confirms the adapter pattern; not reusable. |

No maintained, permissively licensed open-source Indian tender aggregator was found. The portal
parsing is small (one HTML table per feed), so a thin in-house connector layer is the right call.

## General-purpose components

| Tool | Version (date) | Licence | Footprint | Role considered | Decision |
|---|---|---|---|---|---|
| httpx | 0.28.1 (2024-12) | BSD-3 | Small | HTTP client | **Used** — sync client, mock transport for tests, streaming downloads |
| BeautifulSoup4 + lxml | 4.15.0 (2026-06) | MIT | Small | HTML parsing | **Used** — header-driven table parsing tolerant to markup changes |
| selectolax | 1.0.0 (2026-10) | MIT | Small (C) | Faster HTML parsing | Not needed at 10 rows/page |
| Scrapy | 2.19.0 (2026-09) | BSD-3 | Large (Twisted, 35 deps) | Crawling framework | **Not used** — scheduling, throttling and pipelines would duplicate what a 300-line connector layer does; adds async complexity |
| Crawlee (Python) | 1.10.4 (2026-10) | Apache-2.0 | Large (55 deps) | Crawling + browser automation | Not used for the same reason |
| Playwright | 1.63.0 (2026-09) | Apache-2.0 | Very large (browsers) | JS rendering | **Not used** — target listings are server-rendered; JS-only portals (GeM) are restricted anyway |
| pdfplumber (pdfminer.six) | 0.11.10 (2026-06) | MIT | Small | PDF text + tables, page-level | **Used** — permissive licence, page numbers for citations |
| pypdf | 6.20.0 (2026-10) | BSD-3 | Small | PDF utilities | Dependency for future splitting/metadata |
| PyMuPDF | 1.28.2 (2026-08) | **AGPL-3.0 or commercial** | Small | Fast PDF text | **Rejected for default** — AGPL obligations for a commercial service; usable only with an Artifex licence |
| Docling | 2.137.0 (2026-10) | MIT | Heavy (ML models) | Layout-aware PDF → structured | Future option for complex BOQ tables; too heavy for the MVP container |
| Unstructured | 0.27.25 (2026-10) | Apache-2.0 | Very heavy (116 deps) | Document partitioning | Not used |
| Apache Tika (python client) | 3.3.2 (2026-08) | Apache-2.0 | Needs a Java server | Universal extraction | Not used — extra JVM service for formats we already cover |
| Trafilatura | 2.3.1 (2026-10) | Apache-2.0 | Medium | Article/boilerplate extraction | Not needed — we parse structured tables, not articles |
| python-docx / openpyxl | 1.2.0 / 3.1.5 | MIT | Small | DOCX / XLSX read; XLSX write | **Used** |
| XlsxWriter | 3.2.9 (2025-09) | BSD-2 | Small | XLSX write | Equivalent to openpyxl; openpyxl chosen because it also reads (manual imports, tests) |
| pytesseract (+ tesseract) | 0.3.13 | Apache-2.0 | Binary dep | OCR fallback | **Optional extra** (`.[ocr]`), off by default |
| SQLAlchemy | 2.1.4 (2026-10) | MIT | Medium | ORM, SQLite→Postgres | **Used** |
| Alembic | 1.20.0 (2026-09) | MIT | Small | Migrations | Planned for Phase 4 (MVP uses `create_all` + schema version row) |
| Pydantic | 2.14.0 (2026-10) | MIT | Small | Schemas, LLM output validation | **Used** |
| Typer | 0.27.3 (2026-10) | MIT | Small | CLI | **Used** |
| FastAPI | 0.143.0 (2026-10) | MIT | Medium | Dashboard API | Phase 6 (dashboard is not an MVP blocker) |
| ollama (Python client) | 0.6.3 (2026-09) | MIT | Small | Ollama API | Not used — direct httpx calls give full control of retries, usage fields and redaction with one fewer dependency |
| sentence-transformers / fastembed | 6.1.0 / 0.9.0 | Apache-2.0 | Heavy / medium | Local embeddings | Not in MVP — free lexical semantics reached full funnel recall on the benchmark; local Ollama embeddings are wired as an option |
| APScheduler | 3.11.3 (2026-06) | MIT | Small | In-process scheduling | Not needed — `chanakya schedule` (daily IST loop) + OS cron / container jobs |
| Prefect / Dagster | 3.8.8 / 1.13.26 | Apache-2.0 | Very heavy | Orchestration | Rejected — one daily pipeline does not justify a workflow platform |
| Jinja2 | 3.1.6 | BSD-3 | Small | Email templates (autoescaped) | **Used** |

## Decision

Build our own thin connector, matching and reporting layers on a small set of permissive,
actively maintained libraries (httpx, BeautifulSoup/lxml, pdfplumber, python-docx, openpyxl,
SQLAlchemy, Pydantic, Typer, Jinja2). Total runtime dependencies: 12. Heavy frameworks were rejected
because the hard problems here — compliance, deduplication, cost control and trustworthy analysis —
are domain logic, not crawling or orchestration.
