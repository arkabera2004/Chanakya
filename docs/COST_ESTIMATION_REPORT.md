# Cost Estimation Report — Website Intelligence

Status: estimate for planning purposes, not a committed budget. Every figure below is
labeled as one of:

- **Confirmed published price** — from an official vendor pricing page, as of this report's
  writing date (2026-10-30). Prices change; re-check the linked page before budgeting.
- **Usage-based estimate** — a confirmed unit price multiplied by an assumed usage volume
  stated explicitly in that section.
- **Assumption** — a number this report invents for the scenario (e.g. "10 websites",
  "weekly crawl") because the brief doesn't fix it. Change it and the downstream totals change.
- **Unknown** — no reliable published figure was available; the formula and the official
  calculator link are given instead of a guessed number.

All prices are USD unless stated. India (₹) figures use an assumed rate of **₹83 = $1**
(round-number planning assumption, not a live rate) and exclude GST/VAT unless noted.

---

## A. Website Crawling — Infrastructure Requirements

**Confirmed from this implementation** (Phases 1–9, already built and tested):

- One crawl job fetches: the site's `robots.txt`, `sitemap.xml` (+ one level of sitemap-index
  nesting), then breadth-first up to `max_pages` (default 50) and `max_depth` (default 2).
- Each fetched HTML page is parsed in-process (BeautifulSoup); no browser rendering is used
  for the generic crawler (Phase 2) — this keeps per-page CPU cost low (tens of milliseconds),
  unlike the existing Playwright/Crawlee-backed acquisition providers, which render JS and cost
  more CPU/memory per page.
- Documents (PDF/DOC/DOCX) linked from a page are downloaded (capped at 25 MB each by default)
  and text-extracted via the existing `chanakya.documents` module (native text extraction;
  OCR only when a PDF page has no text layer).

**Assumption — per-crawl volumes** (these are the knobs every scenario in §D multiplies):

| Site size | Pages/crawl | Documents/crawl | Avg. doc size |
|---|---|---|---|
| Small (e.g. a state e-procurement portal's visible listings) | 20–50 | 5–15 | 0.5–2 MB |
| Medium (e.g. NHAI, a multi-section department site) | 50–150 | 15–50 | 1–5 MB |
| Large (e.g. a PSU with deep archives) | 150–500 | 50–200 | 1–10 MB |

**Storage per crawl** (assumption, from the above): a medium site's crawl stores roughly
50–150 page-text rows (a few KB each in the database) plus 15–50 documents at 1–5 MB each →
**~15–250 MB of document storage per crawl**, plus well under 1 MB of database rows. Documents
dominate storage; the database (SQLite/PostgreSQL) stores metadata and extracted text only.

**Compute per crawl** (estimate, from this implementation's actual code path): fetching +
parsing + extracting ~100 pages and ~30 documents takes on the order of **1–5 CPU-minutes**
total (dominated by document text extraction, not network I/O, since requests are sequential
with a configurable delay). This is a rough estimate from the shape of the code, not a
benchmark run against real traffic — a real pilot (Phase 11) should measure it.

---

## B. AI and OCR

**Confirmed published prices** (Azure OpenAI, pay-as-you-go, as of this report's writing —
check https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/
for current rates, since these change frequently):

- GPT-4o-mini class model: roughly $0.15 / 1M input tokens, $0.60 / 1M output tokens (confirmed
  order of magnitude; exact current rate — **unknown**, verify at the link above before
  budgeting).
- Azure AI Document Intelligence (OCR), **confirmed published price**: ~$1.50 per 1,000 pages
  for the "Read" (OCR) model on the standard tier
  (https://azure.microsoft.com/en-us/pricing/details/ai-document-intelligence/) — verify the
  current tier name and price before budgeting; Microsoft has renamed this service more than
  once.

**This implementation's actual AI/OCR usage today (Phases 1–9):**

- Tender and company field extraction (Phases 4 and 9) is **rule-based regex extraction**, not
  an LLM call — $0 marginal AI cost per page, by design (the brief explicitly asks why CAPTCHA
  vision models are excluded; the same reasoning extends to using an LLM for routine field
  extraction when deterministic patterns suffice).
- Capability matching (Phase 5) reuses `pipeline.classify.RuleClassifier` — also $0 marginal
  cost, deterministic keyword scoring, no LLM call.
- OCR is **only invoked when a PDF page has no extractable text layer** (`chanakya.documents`
  already implements this — native text extraction first). A typical e-procurement PDF
  (born-digital) needs no OCR at all; only a scanned notice does.

**Cost per 1,000 pages/documents** (usage-based estimate, using the OCR price above, assuming
**20% of documents are scanned and need OCR** — assumption, no data behind this percentage yet):
1,000 documents × 20% × $1.50/1,000 pages ≈ **$0.30 per 1,000 documents crawled**, essentially
negligible at pilot scale. The dominant unknown is the actual scanned-document rate on real
target portals — Phase 11's live verification should record it.

**If an LLM-assisted extraction pass is added later** (to cover the fuller field list the brief
specifies, per the Phase 4 known-limitation note in `IMPLEMENTATION_LOG.md`): assuming ~2,000
input tokens (a document excerpt) + 300 output tokens per document, at the GPT-4o-mini-class
rate above: (2,000/1,000,000 × $0.15) + (300/1,000,000 × $0.60) ≈ **$0.0005 per document**, or
**$0.50 per 1,000 documents** — small, but this is a projection for work not yet built, not a
measured cost.

**Why CAPTCHA-solving vision models are excluded from this phase:** per the brief's own scope
boundary (§1, §7) and this project's `CRAWLER_LIMITATIONS_AND_COMPLIANCE.md` — CAPTCHA bypass is
explicitly out of scope regardless of cost; a government portal that gates its data behind a
CAPTCHA is reported as blocked, not circumvented. There is therefore no CAPTCHA-vision-model
line item in this budget at all, by policy, not by cost comparison.

---

## C. LinkedIn (Sales Navigator) — Research Only, Not a Dependency

No automated LinkedIn scraping exists or is planned (brief §11, §7). This section is research
for a future, permitted integration decision — it does not affect any cost total in §D.

| Plan | Published monthly price (US, confirmed from linkedin.com/premium pricing pages historically; **re-verify before budgeting — LinkedIn does not publish a stable public price list and changes it by region/time**) | India pricing | API/export access |
|---|---|---|---|
| Sales Navigator Core | ~$99.99/month (per-seat, US list price) | **Unknown — not separately published for India**; LinkedIn typically shows INR pricing only inside a logged-in account at checkout | Search UI only; no bulk export API included |
| Sales Navigator Advanced | ~$149.99/month (per-seat, US list price) | Unknown, same caveat | Adds CRM sync (contact/activity sync to supported CRMs via LinkedIn's own integration, not a general data export API) |
| Sales Navigator Advanced Plus | Enterprise/custom pricing, not publicly listed | Unknown | Includes the official Sales Navigator API, but **access is by application/approval, not self-serve** |

**Confirmed, important distinction** (per brief §11): a Sales Navigator subscription grants
**search and CRM-sync capability inside LinkedIn's own UI and approved integrations**. It does
**not** grant a general right to programmatically scrape or bulk-store LinkedIn profile/company
data — that requires either the approved Advanced Plus API (subject to LinkedIn's own usage
policy and approval) or manually exported/CSV data a user supplies themselves. Automated
scraping outside these paths risks ToS violation regardless of which plan is paid for.

**GST/currency caveat:** any India-billed LinkedIn subscription is subject to GST (currently
18% on digital services to an Indian business, per general GST rules — verify the current rate
with a tax advisor, not this report) in addition to whatever subscription price applies.

**Recommendation for this phase:** do not budget LinkedIn into the pilot cost (§D); treat it as
a separate, later decision gated on (a) actual need for prospect-level data beyond what public
websites already give Website Intelligence, and (b) securing Advanced Plus API approval if
automated access is wanted.

---

## D. Azure — Hosting Options and Scenario Costs

### D.1 Architecture options compared

| Option | Fits this workload? | Why |
|---|---|---|
| **A. Container Apps Consumption Plan** | **Recommended** | Scale-to-zero when idle (crawls are intermittent, not constant traffic); pay only for vCPU-seconds/memory-GB-seconds actually used; the FastAPI backend runs as a long-lived revision while crawl execution (today: synchronous within a request, per the documented shortcut in `desk_websites.start_crawl_job`) fits a request-scoped container; upgrading to **Container Apps Jobs** (triggered per crawl) is the natural next step once crawls need to run longer than an HTTP request timeout. |
| B. Container Apps Dedicated Plan | Not justified yet | Dedicated (always-on) compute costs more at low/intermittent request volume; only worth it once sustained concurrent crawl volume justifies reserved capacity. |
| C. Container Apps Jobs | Recommended for crawl execution specifically, once built | Matches "each job has a finite execution lifecycle, can be retried/cancelled" (brief §12) exactly — this is the natural home for `run_crawl_job`, decoupled from the API's request/response cycle. Not yet wired in this implementation (documented shortcut); the `WebsiteCrawlJob` row model (status, cancel/pause flags) already matches what a Jobs-based executor would read. |
| D. Azure Container Instances | Not recommended | No built-in scale-to-zero billing granularity advantage over Container Apps for this workload; Container Apps is the newer, Microsoft-recommended path for this shape of app. |
| E. A small always-on VM | Not recommended | Pays for idle time between crawls; no clear operational win over Container Apps for a workload this intermittent. Only justified if a future requirement needs a persistent browser-automation daemon that doesn't fit the container-per-request/job model — not a need identified in Phases 1–9. |

### D.2 Component costs (confirmed published prices, Azure "Pay-As-You-Go", as of writing —
re-check https://azure.microsoft.com/en-us/pricing/calculator/ before budgeting; regional
pricing varies, these are representative "Central India" or "East US" list prices):

- **Container Apps Consumption**: confirmed published price — $0.000024/vCPU-second,
  $0.000003/GiB-second, plus 2 million free requests/month and ~180,000 vCPU-seconds +
  360,000 GiB-seconds free per month (Azure's standing free grant for Container Apps
  Consumption). Source: https://azure.microsoft.com/en-us/pricing/details/container-apps/
- **Azure Container Registry** (Basic tier): confirmed published price — ~$0.167/day
  (~$5/month), 10 GiB storage included. Source:
  https://azure.microsoft.com/en-us/pricing/details/container-registry/
- **Azure Database for PostgreSQL — Flexible Server** (Burstable B1ms, 1 vCPU/2 GiB — a
  reasonable pilot tier): confirmed published price — roughly $0.018/hour compute (~$13/month
  if run continuously) + storage at ~$0.12/GiB-month. Source:
  https://azure.microsoft.com/en-us/pricing/details/postgresql-flexible-server/
- **Azure Blob Storage** (Hot tier, for crawled documents): confirmed published price — ~$0.018
  per GiB-month (LRS, Hot tier, first 50 TiB). Source:
  https://azure.microsoft.com/en-us/pricing/details/storage/blobs/
- **Log Analytics / Azure Monitor** (for Container Apps logs): confirmed published price — first
  5 GB/month free, then ~$2.30/GB ingested. Source:
  https://azure.microsoft.com/en-us/pricing/details/monitor/
- **Network egress**: confirmed published price — first 100 GB/month free (global, outbound),
  then ~$0.087/GB for the next tier. Source:
  https://azure.microsoft.com/en-us/pricing/details/bandwidth/

### D.3 Three scenarios

Assumptions stated per scenario; all are **assumptions**, not measured data, since no live
pilot has run yet (Phase 11).

**Scenario 1 — Small pilot (3–5 websites)**

- Crawl frequency: weekly per site. Pages: 50/crawl avg. Documents: 15/crawl avg, 2 MB avg.
- Monthly volume: 5 sites × 4 crawls × (50 pages + 15 docs × 2 MB) ≈ 1,000 pages,
  300 documents (~600 MB), ~1 CPU-hour of crawl compute (very conservative, from §A's
  1–5 CPU-minutes/crawl estimate × 20 crawls).
- **Compute (Container Apps)**: well within the free monthly grant (180,000 vCPU-seconds ≈
  50 CPU-hours free) → **$0/month** for crawl + API compute at this volume (usage-based
  estimate from the confirmed free-grant figure).
- **Storage (Blob, 600 MB/month accumulating)**: negligible, under $0.02/month at this price
  (usage-based estimate).
- **Database (Flexible Server Burstable B1ms, always-on)**: ~$13/month compute + ~$1/month
  storage at a few GB (usage-based estimate from confirmed unit prices).
- **Container Registry (Basic)**: ~$5/month (confirmed published price).
- **Logs**: within the 5 GB/month free tier at this volume → $0/month (usage-based estimate).
- **Total Scenario 1: ~$19/month ($228/year)** — dominated by the always-on database, not
  crawling itself.

**Scenario 2 — Medium deployment (10–20 websites)**

- Crawl frequency: 2×/week per site. Pages: 100/crawl avg. Documents: 30/crawl avg, 3 MB avg.
- Monthly volume: 15 sites × 8 crawls × (100 pages + 30 docs × 3 MB) ≈ 12,000 pages,
  3,600 documents (~10.8 GB), ~10–20 CPU-hours of crawl compute (usage-based estimate).
- **Compute**: ~10–20 CPU-hours exceeds the ~50 free CPU-hour grant only if other API traffic
  also uses it; conservatively still **$0–$5/month** (usage-based estimate — the free grant is
  generous relative to this workload's actual CPU shape, which is I/O- and parsing-bound, not
  compute-heavy).
- **Storage**: ~10.8 GB/month accumulating → by month 6, ~65 GB stored ≈ **~$1.20/month**
  (usage-based estimate).
- **Database**: likely needs a step up to General Purpose tier (2 vCPU) for concurrent crawl
  writes — confirmed published price ~$0.07/hour (~$50/month) + storage (usage-based estimate).
- **Container Registry (Basic)**: ~$5/month.
- **Logs**: likely exceeds the 5 GB free tier — assume ~10 GB/month ingested → ~$11.50/month
  (usage-based estimate at the confirmed per-GB rate).
- **Total Scenario 2: ~$68–$73/month (~$820–$880/year)**.

**Scenario 3 — Larger deployment (50+ websites)**

- Crawl frequency: daily per site for high-priority sources, weekly for the rest (assumption —
  no tiering is built yet; this is a future scheduling feature, not implemented in Phases 1–9).
- Monthly volume (rough, assumption): ~100,000+ pages, ~20,000+ documents (~60+ GB/month
  accumulating), tens of CPU-hours.
- At this scale, Container Apps **Dedicated Plan** or a mix of Consumption + **Container Apps
  Jobs** for crawl execution becomes worth evaluating (moves from "pay per request" to
  reserved capacity once sustained load is high enough) — this is a genuine unknown without a
  real pilot's measured CPU/memory profile; this report does not guess a dedicated-plan price
  without that data.
- **Database**: General Purpose 4 vCPU tier likely needed, confirmed published price roughly
  $0.14/hour (~$100/month) + storage at several hundred GB (usage-based estimate).
- **Storage**: ~60 GB/month accumulating → by month 12, ~720 GB ≈ **~$13/month** (usage-based
  estimate).
- **Logs**: likely 50+ GB/month ingested → ~$115/month (usage-based estimate).
- **Compute**: genuinely **unknown** without a measured pilot — do not budget this scenario
  without first running Scenario 1 or 2 for a month and extrapolating from real Container Apps
  billing, not this report's estimate.
- **Total Scenario 3: at least ~$230/month in known components, plus an unknown compute line**
  — this scenario needs a real pilot's data before a committed number, consistent with this
  report's instruction not to invent prices where none are confirmed.

---

## E. Final Recommendation

**Cheapest architecture that satisfies reliability, traceability and performance:**

1. **Azure Container Apps Consumption Plan** for both the FastAPI backend and (once built)
   crawl execution via **Container Apps Jobs** — scale-to-zero matches this workload's
   intermittent nature exactly, and avoids paying for idle compute between crawls.
2. **Azure Database for PostgreSQL Flexible Server**, starting at Burstable tier, stepping up
   only when Scenario 1's pilot data shows write-concurrency pressure.
3. **Azure Blob Storage (Hot tier)** for downloaded documents — cheaper than keeping them in
   the database, and the existing `WebsiteDocument.local_path` model already anticipates a
   path-based storage layer that Blob Storage slots into without a schema change.
4. **No AI/OCR spend by default** — rule-based extraction (Phases 4, 9) costs nothing per
   document; add an LLM-assisted pass only for the fuller field list the brief specifies, and
   budget it per §B's $0.50/1,000-document estimate when that work is scoped.
5. **No LinkedIn spend** until a specific, approved integration need is identified (§C).
6. **No CAPTCHA-solving spend, ever, in this phase** — it is excluded by policy (§B), not cost.

**Avoid:** GPU-backed compute (nothing in this pipeline needs one), a dedicated/always-on VM
(pays for idle time), and any paid LinkedIn or CAPTCHA-solving service before Phase 11's actual
usage data exists to justify it.

**Before committing to Scenario 2 or 3's numbers:** run Scenario 1 for real (a true Phase 11
live pilot against 3–5 public test sites), read the actual Azure bill it produces, and replace
every "usage-based estimate" above with a measured number. This report's job is to show the
formula and the confirmed unit prices, not to substitute for that measurement.
