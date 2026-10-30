# Government Crawler — User Guide

Covers the Website Intelligence workflow for government procurement/department websites, as
actually implemented (not a feature wishlist). For architecture detail, see
`WEBSITE_INTELLIGENCE_ARCHITECTURE.md`.

## 1. Add a website

Open **Website Intelligence** in the nav, fill in:

- **Name** — a label for your reference (e.g. "NHAI").
- **Base URL** — the site's homepage, e.g. `https://nhai.gov.in`.
- **Category** — one of: Government Procurement, Government Department, NHAI, GePNIC, State
  E-Procurement, PSU, Company Website, Other. The category matters: **Company Website**
  switches the crawl to company-fact extraction instead of tender extraction (see
  `COMPANY_INTELLIGENCE_GUIDE.md`).

Click **Add**. This creates a `Website` record; nothing is crawled yet.

## 2. Start a crawl

On the website's detail page, click **New crawl** to create a crawl job (status `queued`),
then click the play button to **start** it. The crawl runs and the page refreshes with results
when it finishes (today: this happens within the same request — a very large site will take
correspondingly longer to show results; see the architecture doc's known-limitation note).

What the crawl actually does:

1. Checks `robots.txt` at the site root; a path disallowed there is recorded as
   `skipped_robots`, never fetched.
2. Fetches `sitemap.xml` (and one level of any nested sitemap index) to seed additional URLs.
3. Crawls breadth-first from the homepage, following same-origin links up to the configured
   page limit (default 50) and depth limit (default 2). A link to another domain is recorded
   as `skipped_offsite`, never fetched — this is enforced by the same SSRF-safe URL validator
   used elsewhere in this codebase, not a separate allowlist.
4. On every successfully fetched HTML page, looks for links to `.pdf`/`.doc`/`.docx` files
   within the same scope, downloads them (up to 25 MB each by default), and extracts their
   text using this repo's existing document-extraction module (native text first; OCR only if
   a PDF page has no text layer).
5. On every fetched page and extracted document, looks for tender fields anchored to labeled
   lines (e.g. "Tender No:", "Last Date of Submission:", "EMD Amount:") — see the field list
   below. A field not found in the text is reported as **absent**, never guessed.
6. Matches each extracted tender against the real Wayam capability book (the same engine every
   other source in this project uses) and attaches the top matching capabilities with their
   score.

## 3. Pause, resume, cancel

- **Cancel** (queued/running/paused → cancelled): stops the crawl at the next page boundary and
  marks the job cancelled. Already-collected pages/documents/tenders from before the cancel are
  kept, not discarded.
- **Pause** (running → paused): the crawl observes this flag at the next page boundary and
  stops; **resume** flips it back to running. Today, resuming re-enables the status but does
  not pick up a background executor mid-crawl (the crawl itself already finished by the time
  pause/resume matters, given the current synchronous-request execution model) — this becomes
  meaningfully different once a background job executor is added (architecture doc §5).

## 4. Inspect results

The crawl detail view has three tabs:

- **Tenders** — one card per extracted tender, showing every found field with its exact source
  excerpt, a link to the original page, and the matched Wayam capabilities with scores. A
  field with no match shows nothing fabricated — only fields actually found appear.
- **Pages** — every discovered URL with its fetch status (`fetched`/`failed`/`skipped_robots`/
  `skipped_offsite`) and HTTP status code, with a direct link to open it.
- **Documents** — every downloaded document with a link to the original document URL, a link
  to the page it was found on, and its extraction status.

**Zero results explained honestly:** if the Tenders tab is empty after a crawl that otherwise
succeeded, it means no labeled tender fields were found in any crawled page or document — not
that the crawl failed. Check the Pages tab's status column to distinguish "nothing relevant
found" (pages show `fetched`) from "the site blocked the crawl" (pages show `skipped_robots`)
or "pages exist outside scope" (`skipped_offsite`).

## 5. Fields currently extracted

Tender No./ID, Organisation, Publication Date, Submission Deadline, Pre-Bid Meeting Date,
Opening Date, Estimated Value, EMD, Tender Fee, State, Opportunity Type.

This is a representative subset, not the full ~25-field list in the original brief (scope of
work, eligibility criteria, required documents, contact information, etc. are not yet
extracted). Extending the field list is a matter of adding more label patterns to
`chanakya.webcrawl.tender_extraction`, or adding an LLM-assisted pass with the same
evidence-grounding discipline this project already uses elsewhere (`pipeline.analyze`'s
`_evidence_in` check).

## 6. Export

From the Tenders tab, **Export CSV** / **Export XLSX** downloads every extracted tender with
its source URL and, for every field, both the value and its source excerpt — the exported file
is as traceable as the on-screen view.

## 7. If a site blocks the crawl

If a target site's `robots.txt` disallows the paths you need, or every page comes back
`skipped_offsite`/`failed`, this tool reports that honestly rather than attempting to bypass
it (no CAPTCHA solving, no identity rotation — see `CRAWLER_LIMITATIONS_AND_COMPLIANCE.md`). In
that case, either confirm the site structure with an authorized access method, or supply
documents manually through the existing manual-import path (`chanakya.connectors.manual`).
