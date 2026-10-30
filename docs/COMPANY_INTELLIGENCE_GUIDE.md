# Company Website Intelligence — User Guide

Covers the company-website crawl workflow. Built after the government crawler was working and
verified, per the project's own phase ordering. Reuses the identical crawler (same-origin
discovery, SSRF protection, robots.txt/sitemap handling) — the only difference is what gets
extracted once a page is fetched.

## 1. Add a company website

Same flow as a government website (`GOVERNMENT_CRAWLER_USER_GUIDE.md` §1), but set
**Category** to **Company Website**. This single field is what switches the crawl's extraction
behavior — there is no separate "company crawler" codepath, just a branch in
`chanakya.webcrawl.runner` keyed on `Website.category`.

## 2. What gets extracted

On every successfully fetched HTML page, this looks for:

- **Company name** — from the page's `<title>` tag.
- **Industries**, **Technology capabilities**, **Certifications**, **Locations** — each from a
  single labeled line (e.g. "Industries: Smart Cities, Industrial Safety") split on commas.
- **Contact email / phone** — a plain regex match for an email address or a phone-number-shaped
  string on the page.

Each found value carries the exact line it came from, same evidence discipline as the tender
extractor. A field not found is absent, not fabricated — and **an absent field is never treated
as proof the company lacks that capability** (this is an explicit instruction from the original
brief this module was built against: a company's website not mentioning "cloud" doesn't mean
the company has no cloud offering, it means this page didn't say so).

## 3. Where results show up

The crawl detail view shows a **Company** tab (replacing the Tenders tab for a
company-category website) with one card per page that had at least one company fact found,
each with a link back to the source page.

## 4. What's not built yet

- **Document extraction for company facts**: today, company extraction runs over fetched
  pages only, not downloaded PDF/DOC/DOCX documents (tender extraction does both). Company
  sites rarely publish company facts as PDFs, so this was deprioritized — extending it is a
  straightforward repeat of the tender-extraction document-handling pattern if a real use case
  needs it.
- **Re-crawl / change detection**: running a second crawl job against the same company website
  produces an independent new set of rows; there is no diffing against the previous crawl to
  surface "what changed." The data is there (each `WebsiteCompanyProfile` row has a
  `created_at` and belongs to a specific `crawl_job_id`), so change detection is a query/UI
  feature to add on top, not a schema change.
- **No merged single profile**: results are per-page, not merged into one canonical company
  profile record. A company's "About" page and "Contact" page each produce their own row; a
  future aggregation step (keep first non-null value per field across all pages, in a
  deterministic page order) would need to be added if a single consolidated profile is
  required.
