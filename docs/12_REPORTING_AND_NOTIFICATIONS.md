# 12 — Reporting and Notifications

## Excel workbook (`reporting/excel.py`)

File name `chanakya_opportunities_<YYYY-MM-DD>_<HHMM>_<run suffix>.xlsx` in `app.reports_dir`; the
newest `app.report_retention` (30) workbooks are kept. Every export is recorded in `exported_reports`
(path, SHA-256, rows, run). Opened with `chanakya export` or produced by `chanakya run`.

| # | Sheet | Contents |
|---|---|---|
| 1 | **Executive Summary** | Generated time (IST), run ID/status, sources checked (ok / failed-blocked / skipped), new and updated notices, active P1 to P3, P1, P2, closing within 7 days, expired/closed, P1/P2 needing eligibility review, filtered as unrelated, AI mode, billable AI calls, tokens, estimated cost, budget skips, deferred deep analyses, coverage notes, and a standing caution that scores are decision support |
| 2 | **All Opportunities** | Every assessed, non-rejected notice; 35 required columns + Flags, Score Breakdown, Reference No., Data Completeness, AI Analysis, Review Status |
| 3 | **High Priority** | Active P1/P2 — brief columns incl. flags and next action |
| 4 | **Closing Soon** | Active P1 to P3 closing within `scoring.closing_soon_days`, soonest first |
| 5 | **Product-Wise** | Offering → capability → opportunities, so each product owner sees their pipeline |
| 6 | **Eligibility Review** | One row per extracted requirement: criterion, requirement text, explicit/inferred, rule/AI source, assessment, evidence (document, page, section, quote) |
| 7 | **Change History** | Change events (new, deadline/value/status change, corrigendum, document, retender) with old → new values |
| 8 | **Scan Statistics** | Per source per run: status, timings, pages, seen/new/updated/unchanged/duplicates/malformed, error, coverage note; plus AI usage by model and purpose |

Required columns (sheet 2): Internal Opportunity ID · Tender ID / Bid Number · Tender Title · Government
Organization · Department · State / Region · Procurement Platform · Opportunity Type · Publication Date
(IST) · Closing Date (IST) · Days Remaining · Estimated Value · Currency · Scope Summary · Relevant Wayam
Product · Capability Match · Relevance Score · Priority · Eligibility Status · Eligibility Requirements ·
Turnover Requirement · Past Experience Requirement · Certification Requirements · EMD · Hardware
Requirement · Partner Requirement · Technical Risks · Business Recommendation · Suggested Next Action ·
Official Tender URL · Tender Document URL · Evidence Reference · Confidence · Last Verified (IST) ·
Current Tender Status.

Presentation rules:

- Sorted by priority, then score, then closing date. Header row frozen, auto-filter on, wrapped text,
  priority cells coloured (P1 green, P2 amber, P3 blue, P4 grey), flags in red.
- Missing values are written as **"Not stated"** in grey — never blank-filled with guesses.
- Dates are IST wall-clock values with an explicit "(IST)" header (Excel has no time zones).
- Estimated values appear only when stated by the source or verified in the source text after AI
  extraction; otherwise "Not stated".
- "Scope Summary" says "Listing-level notice: …" when no documents were analysed.

Spreadsheet safety:

- Any cell starting with `=`, `+`, `-`, `@`, tab or CR is prefixed with `'` and stored as text, so a
  tender title like `=HYPERLINK(...)` cannot become a formula (formula/CSV injection).
- Hyperlinks are created only for `https` URLs on allowlisted government hosts; anything else is plain
  text.
- No macros, no external links, no embedded objects.

## Console report

`chanakya report --limit 15` prints the top active opportunities (priority, score, closing date, title,
organisation, offering, eligibility, flags) — useful over SSH or in CI logs.

## Email digest (`reporting/email.py`, template `reporting/templates/digest.html.j2`)

Subject (configurable): `CHANAKYA | Wayam AI Government Opportunities | <YYYY-MM-DD>`.

Body (HTML with a plain-text alternative):

1. Run summary — new / updated notices, active P1 and P2 counts, closing soon, and coverage notes
   (failed/blocked sources, partial pagination, unpriced models). A dry run is bannered "DRY RUN — not sent".
2. Up to 15 opportunities with score ≥ `email.min_score` (default 50), optionally only those closing
   within `email.closing_within_days`: priority, score, platform, title, organisation, Tender ID,
   closing date (IST), relevant Wayam solution, eligibility status, key concerns, flags, next action and
   the official link (allowlisted hosts only). New-in-this-run items are marked.
3. A footer: scores are decision support, eligibility is not assumed, confirm on the official portal;
   AI calls and estimated cost for the run and the AI mode.

The workbook is attached when `email.attach_excel` is true and the file is ≤ `max_attachment_mb` (10 MB);
otherwise it is not attached (the footer then omits "Full workbook attached") and the workbook stays in
`reports_dir`. Jinja2 autoescaping is on, so tender text is escaped
in HTML (tested with `<script>` in a title).

### Providers

| Provider | Use | Notes |
|---|---|---|
| `file` (default) | Development, tests, demos | Writes `.eml` files to `email.outbox_dir` |
| `smtp` | Production | STARTTLS (587), implicit TLS (465) or none (internal relays only); credentials from env vars named in `username_env` / `password_env` |
| dry run | `chanakya run --dry-run` / `chanakya email --dry-run` | Always renders to `outbox/dry_run/` regardless of provider; never contacts a server |

Recipients come from `email.to` / `email.cc` or `CHANAKYA_EMAIL_TO` (comma-separated). Each recipient
gets an individual message.

### Idempotency and failure handling

- Key = SHA-256 of (kind, report ID, run ID, recipient). A key already `sent` (or `dry_run`) is never
  sent again — re-running `chanakya email` after a crash only sends to recipients that did not get it.
- Transient SMTP errors: up to `email.max_retries` (3) attempts with exponential backoff.
- Authentication errors: no retry; the delivery is marked `failed` with the error (credentials are never
  logged).
- Each delivery is recorded in `email_delivery_log` (status, attempts, provider message ID, last error).
- **A failed email never triggers a re-scan or re-analysis**; it affects only the exit code (3) so the
  scheduler can alert.

### When an email is sent

| Command | Email |
|---|---|
| `chanakya run` | Only with `--email` (or `email.enabled: true` in the scheduler) |
| `chanakya run --dry-run` | Rendered to `outbox/dry_run/` when recipients are configured |
| `chanakya email --latest` | Sends the digest for the latest run and workbook (idempotent) |
| `chanakya schedule --email` | Daily at `schedule.daily_at` IST |

### New-P1 alert (`email.alert_on_new_p1: true`)

On any run that emails (`run --email`, `watch --email`, the scheduler), each recipient gets one message
listing the active P1 notices they have not been told about. Delivery is recorded per notice and
recipient (`kind = p1_alert`), so a notice is alerted once, ever, even if its score later changes. A
failed send leaves the notice pending and the next run retries it. A dry run renders the alert to
`outbox/dry_run/` and does not use the alert up. The first run after switching it on alerts every P1
already in the book. `chanakya email --kind alert` sends the pending alert without a scan.

### Weekly digest (`email.digest: weekly`)

The digest goes out once per week, keyed by the IST date of `schedule.weekly_digest_day` on or before
today. Other runs in that week send no digest (alerts still go). If the configured day is missed, the
next emailed run sends it. The weekly digest counts notices first seen and notices changed in the last
seven days, where the daily digest counts the run. `chanakya email --kind weekly` sends it on demand.

### Follow-up alerts for tracked notices (`reporting/followups.py`)

Both alerts are off by default and cover the same set of **tracked** notices: review status
Shortlisted, Reviewing, Qualified, Partner needed or Pursuing, or priority P1 that BD has not closed
out (review status Rejected, Expired, Won or Lost). They follow the P1 alert's rules: sent on any run
that emails, one message per recipient, a failed send stays pending and is retried on the next run, a
dry run renders to `outbox/dry_run/` and does not use the alert up, and a failure affects only the exit
code (3). Templates: `corrigendum_alert.html.j2` and `deadline_alert.html.j2`, each with a plain-text
part.

**Corrigendum alert (`email.alert_on_corrigendum: true`).** Each recipient is told once about each
material change to a tracked notice: the field, the old value, the new value, when Chanakya saw it,
and the official link (allowlisted hosts only). The changes are the material `change_events` rows of
type deadline, value or EMD, corrigendum, status, title or description, and documents. A new notice is
the P1 alert's job, and automatic expiry is not material. Delivery is recorded per change event and
recipient (`kind = corrigendum_alert`). Only changes detected in the last
`email.corrigendum_alert_window_days` (7) are alerted, so switching the alert on does not replay the
whole change history; a notice shortlisted today is told about its changes from that window. A
cancelled or withdrawn tracked notice is alerted, since that is a status change BD needs.
`chanakya email --kind corrigendum` sends what is pending without a scan.

**Deadline alert (`email.alert_on_deadline: true`).** One reminder per notice at each threshold in
`email.deadline_alert_days` (default `[7, 2]`, days before closing). Only the tightest threshold a
notice is inside is due, so a notice first tracked with one day left gets the 2 day reminder and not
the 7 day one as well. Only notices with status `active` and a closing date still in the future
qualify: a closed, cancelled or expired notice is never reminded, and neither is one with no closing
date. Delivery is recorded per notice, threshold, closing date and recipient (`kind = deadline_alert`).
The closing date is part of the key so that a deadline moved by a corrigendum is reminded again at the
new date. `chanakya email --kind deadline` sends what is due without a scan.

#### How changes are detected, and what is not covered

Verified by tests: when a scan reads a notice it already holds and a material field differs,
`pipeline/store.py` writes the change events, and the corrigendum alert reports them.

`Connector.check_updates(keys)` stays unimplemented, deliberately. Neither CPPP nor the GePNIC portals
offer a public lookup of one notice by its ID: on both, a single notice is reached through a CAPTCHA
search or a session-bound detail link, and Chanakya does not bypass either. A fetch written against
those pages could not work, so none was written.

The limit that follows: a change is seen only when the scan re-reads the notice. The scheduled scan is
incremental. It reads each listing back to the source's high-water mark less `overlap_hours` (12), so
it re-reads notices published in that window and not older ones. A corrigendum to a notice published
two weeks ago is therefore **not** picked up by the ordinary daily run unless the portal lists the
notice again. Two things do pick it up today, both using only the public listings:

- `chanakya run --since <YYYY-MM-DD>` with a date on or before the oldest tracked notice's publication
  date. It re-reads the listings that far back, within each source's `max_pages` ceiling.
- A manual import of the notice (doc 08) after BD sees the corrigendum on the portal.

Not verified (needs a live network test): whether CPPP or a GePNIC portal moves a notice back to the
top of its listing when a corrigendum is published. Until that is checked, treat the corrigendum alert
as covering recently published notices and whatever the last `--since` re-scan reached, not as a
guarantee that every corrigendum is caught.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Run or configuration error (exception) |
| 2 | Every enabled source failed or was blocked |
| 3 | Partial — a source failed/blocked or an email delivery failed; the report was still produced |
