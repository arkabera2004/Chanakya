# 09 — Document Processing

Implemented in `src/chanakya/documents/extract.py`, called from `Pipeline._process_documents`.

## Where documents come from

Every portal researched (CPPP, GePNIC, GeM, IREPS) puts tender documents behind a CAPTCHA or a login
(doc 02). Chanakya does not solve, bypass or outsource CAPTCHAs. Documents therefore arrive through:

1. `chanakya import <file|folder> --tender-id <ID>` — copies files to `data/inbox/documents/<ID>/`.
2. A folder dropped manually at `data/inbox/documents/<Tender ID | CHK-id | ref with / → _>/`.
3. `document_paths` column in a manual CSV/XLSX/YAML import (`;`-separated, relative to the file).
4. Direct links from sources that publish documents openly (`access: direct`), downloaded through
   `PoliteClient.download` with the domain allowlist and the 25 MB cap. None of the MVP sources do.

Only notices classified **CANDIDATE** or **STRONG** have their documents parsed; rejected and LOW
notices never cost CPU or tokens. Per notice: at most `budget.max_documents_per_tender` (3) files and
`budget.max_pages_per_document` (80) pages each.

## Pipeline

```
file ──► detect_type (magic bytes, never the extension)
        ├─ %PDF ─────► pdfplumber per page ─► <40 chars & images & ocr_enabled ─► tesseract (pypdfium2 render ×2)
        │              └─ page has ruled tables ─► rows rendered as "a | b | c" inside [TABLE n] ... [END TABLE n]
        ├─ DOCX ─────► python-docx paragraphs + tables → pseudo-pages of 40 blocks (for citations)
        ├─ XLSX ─────► openpyxl read-only, values only, ≤2,000 rows per sheet, one page per sheet
        ├─ HTML ─────► BeautifulSoup text (script/style/nav removed)
        ├─ text ─────► UTF-8 with replacement
        ├─ ZIP ──────► safe_extract_zip → recurse once (nested archives not expanded)
        └─ OLE/other ► "unsupported … manual review required"
     ──► quality score ──► identify_sections ──► build_context (bounded, cited excerpt)
```

Every extracted page keeps `(document, page, method)` so any downstream claim can cite
`RFP.pdf p.12 §Eligibility Criteria`.

## Safety controls

| Threat | Control |
|---|---|
| Path traversal / absolute paths / symlinks in ZIP | Rejected member-by-member (`security.safe_extract_zip`) |
| Zip bombs | ≤200 members, ≤200 MB total, compression ratio ≤100 (configurable) |
| Encrypted archives | Rejected → manual review |
| Malformed PDFs | Per-page `try/except`; a corrupt page is recorded, the rest continue |
| Disguised file types | Detection by magic bytes; legacy `.doc/.xls` (OLE) are not parsed |
| Oversized downloads | `http.max_download_mb` (25) enforced while streaming |
| Untrusted content | Text is data only; screened for instruction-like phrases before any LLM call (doc 13) |
| Untrusted filenames | `security.safe_filename` for anything written to disk |
| Table content | Rows are data, never instructions: cells are flattened to one line, cannot forge the table markers, and the excerpt goes through the same injection screening as other text |

A failed or rejected document never fails the run: the error is stored in
`opportunity_documents.error`, the document is flagged `needs_manual_review`, and the opportunity
keeps its listing-level assessment.

## Quality and manual review

`quality` = share of pages with ≥ `documents.min_chars_per_page` (40) characters, counting a page as
half if under 45% of its characters are letters (tables of numbers, garbled encodings). A document is
flagged **needs manual review** when quality < 0.5, no pages were extracted, or extraction failed /
was rejected / was unsupported. Scanned PDFs without OCR land here, as do Hindi-only documents
without the `hin` tesseract language pack.

OCR is off by default (`documents.ocr_enabled: false`) because it is slow and needs system packages;
the Docker image installs tesseract when built with `--build-arg INSTALL_OCR=true`. Only pages with no usable
text layer are OCR-ed, and the OCR result is used only when it is longer than the native text.

## Section identification

Heading heuristics: a line of 4 to 110 characters that looks like a heading (numbered, `Section/Chapter/
Annexure` prefix, ALL CAPS, or ending with `:`) and matches one of 13 canonical patterns:

`scope_of_work`, `technical_requirements`, `eligibility`, `experience`, `financial_qualification`,
`emd`, `performance_security`, `evaluation`, `deliverables`, `boq`, `timeline`, `key_dates`,
`contract_conditions`.

`boq` (added with table extraction) matches headings such as "Bill of Quantities", "BOQ", "Schedule of
Quantities", "Schedule of Rates", "Price Schedule", "Price Bid" and "Financial Bid". Lines inside a
`[TABLE n]` block are never treated as headings.

Text following a heading belongs to that section until the next recognised heading or the end of the
document. Section metadata (name, heading, page range) is stored in `opportunity_documents.sections`.

## Tables and BOQs (`documents/tables.py`)

Plain text extraction interleaves the columns of a table: a wrapped description lands on three lines
with the item number in the middle. For PDF pages that carry a ruled table, the rows are rendered
instead as structured lines, in reading order with the rest of the page:

```
BILL OF QUANTITIES
[TABLE 1 p.7, 6 columns; first row is the header]
Item | Description | Quantity | Unit | Rate | Amount
1 | Supply and installation of video analytics licence for 450 cameras | 450 | Nos | 12,500.00 | 56,25,000.00
[END TABLE 1]
Note: taxes extra.
```

- **Detection**: pdfplumber's default table finder (drawn lines only, no new dependency). A page with
  no drawn lines or rectangles is skipped before the finder runs. A table qualifies with at least 2
  rows, 2 columns and 4 filled cells, so page borders, title rules and single boxes are ignored.
  Pages with no qualifying table keep exactly the text they had before.
- **Tables across pages**: when the first table on a page has the same column count as the last table
  on the previous page, its marker reads `continues the table on p.N; columns: ...`, so every page
  carries the column names. A header row repeated at the top of the page is dropped.
- **Caps**: `budget.max_pages_per_document` applies as before (pages beyond it are never opened).
  `documents.max_table_rows_per_document` (300) bounds structured rows per document: past it, the
  remaining rows of that table follow as plain text and later pages use plain extraction, so the cap
  never drops content; the document records "table rows capped". Cells are cut at 160 characters and
  tables at 12 columns. The excerpt limits below are unchanged: a BOQ section gets one section's
  share of the excerpt, never the whole budget.
- **Untrusted content**: table rows are data. A `|` inside a cell becomes `/`, and `[TABLE` or
  `[END TABLE` inside a cell is defanged, so a cell cannot close the block or open another. The
  excerpt is screened by `screen_untrusted_text` before any model call, as for all document text.
- **Switch**: `documents.tables_enabled: false` restores plain text for every page.
- Any error in table handling falls back to the plain text of that page.

## Bounded excerpt for the LLM (`build_context`)

Whole documents are never sent to a model.

1. Sections are ordered by priority: scope → technical → eligibility → experience → financial → EMD →
   performance security → evaluation → deliverables → BOQ → timeline → key dates → contract conditions.
2. Each section contributes at most `max(600, max_chars/5)` characters, tagged with its citation.
3. The total is capped at `excerpt_chars(max_input_tokens_per_call)` =
   `(max_input_tokens_per_call − 2,000) × 3.2` characters (12,800 chars for the default 6,000-token
   cap), leaving room for the system prompt, schema, metadata and capability cards.
4. If no section was recognised, the first pages are used instead (still cited by page).
5. As a safety net, `Analyzer._fit_excerpt` trims the excerpt further (lowest-priority sections last)
   if the full request would still exceed the per-call cap, and records a note. A long document is
   therefore analysed on its most important sections rather than refused by the budget guard
   (regression test `test_long_document_excerpt_fits_input_budget`).

## Deterministic eligibility extraction

`pipeline/eligibility.py` runs regular expressions over the listing description and the eligibility,
experience, financial, EMD, technical, evaluation and contract sections:

| Criterion | Example pattern hits |
|---|---|
| `turnover` | "average annual turnover of Rs. 5 crore", "turnover … not less than ₹ 10 Cr" |
| `net_worth` | "positive net worth" |
| `experience_years` | "at least 3 years in …" |
| `similar_works` | "three similar completed works each of value …" |
| `govt_experience` | "experience with Central/State Government / PSU" |
| `certification` | ISO 9001/27001/20000, CMMI level, CERT-In empanelment, STQC |
| `oem_authorization` | "Manufacturer Authorisation Form", "OEM authorisation" |
| `consortium_no` / `consortium_yes` | "consortium not allowed" / "JV/consortium permitted" |
| `mse_exemption` | MSE / startup EMD or experience exemption |
| `emd` | "EMD Rs. 2,00,000", "bid security" |

Each finding stores the quoted requirement text, value and unit, and evidence (`listing` or
`document`, document name, page, section heading). Assessment against `company_profile` is covered in
doc 10.

## Change handling

Documents are identified by SHA-256. Re-importing the same file is a no-op; a different file with the
same name becomes a new version (`opportunity_documents.version`), records a `document_changed` event
and triggers re-assessment. A notice in state `deferred_no_documents` stores a fingerprint of its
document folder; when files appear, the next run re-assesses it automatically.

## Known limitations

- Heading detection is heuristic; tenders that use unusual headings fall back to the first pages.
- Only ruled tables are detected. A BOQ laid out with spaces and no drawn lines stays as plain text
  (the text-alignment finder gives too many false tables on prose pages).
- A table that continues on the next page is recognised by column count only; cells merged across
  the page break are not rejoined.
- A BOQ with no recognised heading stays inside whatever section precedes it.
- Tables in scanned (OCR) pages are not reconstructed.
- Table extraction was tested on synthetic PDFs built with reportlab, not yet on real tender BOQs.
- Legacy `.doc`/`.xls`, RAR and 7z are not parsed (manual review).
- OCR quality on Devanagari depends on the tesseract `hin` pack and scan quality.
