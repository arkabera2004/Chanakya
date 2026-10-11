# 02 — Government Source Research

Research date: **2026-10-10**. Pages were fetched with an automated research fetcher that respects
robots.txt; the build sandbox itself could not reach the portals (outbound allowlist), so live
connector runs remain to be done from the deployment network (`chanakya sources test`).

Status vocabulary:
- **Verified working** — page fetched, structure and fields observed, no login/CAPTCHA to read.
- **Verified restricted** — fetched or checked, and access is restricted (robots disallow, CAPTCHA, login). Not automated.
- **Candidate** — exists (linked from an official page) but structure not confirmed.
- **Unsupported** — no permitted automated path identified.

## 1. What was observed

### GeM-CPPP (eprocure.gov.in/cppp) — the national aggregator
- CPPP is operated by NIC with the Department of Expenditure and aggregates central notices
  (GePNIC eProcurement + ePublishing + XML feeds from other systems); its newsletters state GeM bids are
  also shown on CPPP. The menu exposes separate feeds: *Central - Active Tenders* (`cpppdata`),
  *States - Active Tenders* (`mmpdata`), *GeM - Active Bids* (`gemdata`), plus Active Corrigendums,
  Result of Tenders, Cancelled Tenders, High value Tenders and Global Tenders.
- `latestactivetendersnew/cpppdata` returned a 7-column table — Sl.No · e-Published Date · Bid
  Submission Closing Date · Tender Opening Date · Title/Ref.No./Tender Id · Organisation Name ·
  Corrigendum — 10 rows per page, newest first, "Total Tenders : 29625", and a pager whose links carry
  a base64-encoded `url` parameter (`…?page=2`). No login or CAPTCHA is needed to browse.
- The same page carries a keyword search form with an **image CAPTCHA**; *Tender Search*, *Search by
  Product* and *Tenders by Classification* also require the CAPTCHA. Chanakya never submits them.
- Detail links (`/cppp/tendersfullview/<token>`) embed a Unix timestamp; re-requesting one later
  returned "Invalid Url". They are therefore stored as expiring links; the Tender ID is the key.
- `highvaluetenders` returned the same table layout (558 tenders, 56 pages); Tender IDs follow the
  GePNIC form `YYYY_ORG_SEQ_VERSION` (e.g. `2026_UPMRC_294005_1`).
- `/robots.txt` on eprocure.gov.in returned **404** (no robots policy published); pages carry
  `meta robots: noindex` (indexing, not access). The Terms of Use disclaim accuracy and ask users to
  verify with the department; the copyright policy says content belongs to the publishing
  departments and reproduction needs permission. Chanakya stores listing metadata for internal BD
  use, cites the source and always links back — it does not republish.

- **Result of Tenders (awards)**, checked 2026-10-10 through `PoliteClient` (5 requests, 3 s gap): the
  home page links "Result of Tenders" and "Bid Awards" to `/cppp/resultoftendersnew`, which redirects to
  `/cppp/resultoftendersnew/cpppdata`. That page is a search form only (organisation, keyword, tender id,
  location, AOC published year, AOC status) with an image CAPTCHA. It shows no results table and no pager
  until the form is submitted, so awards cannot be listed automatically. Verified restricted for
  listing; a person can save a result page and load it with `chanakya awards import` (doc 08).

### GePNIC portals (eprocure.gov.in/eprocure/app, etenders.gov.in/eprocure/app, state portals)
- The home page shows *Latest Tenders* (Tender Title · Reference No · Closing Date · Bid Opening Date)
  and *Latest Corrigendums*, refreshed every 15 minutes per the page. Links are Tapestry
  `DirectLink` URLs with `session=T` (session-bound, not permanent).
- *Advanced Search*, *Tenders by Classification* and document downloads require a CAPTCHA.
  Classification values seen include EOI, Empanelment and QCBS — useful for later targeting.
- etenders.gov.in serves NHAI, AAI, Ordnance factories, TCIL and others on the same software.
- The CPPP newsletter states GePNIC runs in 800+ central organisations and ~31 states/UTs, so one
  connector, configured per base URL, can cover many state portals.

### Restricted portals
- **GeM bidplus** (`bidplus.gem.gov.in/all-bids`) and its robots.txt: the research fetcher was refused
  with *site disallows automated access*. → verified restricted.
- **IREPS** (`ireps.gov.in`): same refusal on robots.txt. → verified restricted.

### PSU / department websites (spot checks)
- **BECIL** `becil.com/tenders` — public HTML list (title, number, publish date, last date); includes
  EOIs for system-integrator partners (e.g. SOC/NOC for state load-despatch centres) and GeM-numbered
  RFPs it manages. → verified working (manual/additional connector candidate).
- **NTPC** `ntpctender.ntpc.co.in/NITDetails/NITs/<id>` — public NIT pages (NIT number, title, dates,
  EMD); bidding happens on a separate SAP e-tender site. → verified working for NIT metadata.
- **Info Tech Corporation of Goa** publishes NIT PDFs on goa.gov.in and bids on `eprocure.goa.gov.in`.

### Not found
- No official API, RSS feed or bulk export for CPPP/GePNIC was found (search of eprocure.gov.in and
  data.gov.in). Commercial scrapers of CPPP exist (Apify actors) but are not official sources.

## 2. Source research matrix

| Source | Official URL | Coverage | Relevant opportunity types | Access method | API | Auth | CAPTCHA | Terms / robots | Data fields | Attachments | Refresh | Difficulty | Reliability | Priority | Verification |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CPPP Central active | https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata | All central ministries, PSUs, autonomous bodies (≈29.6k active) | Tender, RFP, EOI, RFQ, empanelment | Server HTML table, paginated | No | None | Not for listing; yes for search | robots.txt 404; ToU: verify with dept; copyright with depts | Published, closing, opening, title, ref no, Tender ID, organisation, corrigendum flag | Detail link expires; docs behind CAPTCHA → manual | Continuous | Low | Medium (layout changes, load) | **MVP #1** | Verified working |
| CPPP State active | …/latestactivetendersnew/mmpdata | State GePNIC portals aggregated | Same | Same template (assumed) | No | None | As above | As above | Same + state via org | As above | Continuous | Low | Medium | **MVP #2** | Candidate (validate) |
| CPPP GeM bids | …/latestactivetendersnew/gemdata, /cppp/gemtender | GeM bids mirrored on CPPP | Bids, RA | Same template (assumed) | No | None | As above | As above | Bid no., dates, title, organisation | GeM docs (restricted) | Continuous | Low | Medium | P2 | Candidate |
| CPPP High value / Global | /cppp/highvaluetenders, /cppp/globaltenders | Subsets of central | Large RFPs | HTML table | No | None | No | As above | As central | As above | Continuous | Low | Medium | P3 (subset) | Verified working (high value) |
| CPPP Result of Tenders (awards) | https://eprocure.gov.in/cppp/resultoftendersnew/cpppdata (states: /mmpdata, not fetched) | Award of contract notices | Awards (competitor and price intelligence) | Search form only; results after CAPTCHA submit | No | None | **Yes, to list anything** | robots.txt 404 | Form fields seen: organisation, keyword, tender id, location, AOC year, AOC status. Result columns not observed | n/a | Unknown | n/a | n/a | Manual import (`awards import`); connector disabled | Verified restricted for listing; results parser candidate (synthetic fixture) |
| CPPP corrigenda / cancelled | /cppp/latestactivecorrigendumsnew, /cppp/cancelledtenders | Change & status feeds | Corrigenda, cancellations | HTML table (assumed) | No | None | Unknown | As above | Ref, dates, organisation | — | Continuous | Low | Medium | P2 (change tracking) | Candidate |
| CPPP search / by product / by classification | /cppp/tendersearch, /cppp/searchbyproduct | Filtered views | — | Form + CAPTCHA | No | None | **Yes** | — | — | — | — | — | — | Not automated | Verified restricted |
| GePNIC central | https://eprocure.gov.in/eprocure/app | Central GePNIC | Tender, EOI, empanelment, QCBS | HTML, session links | No | None to browse | Search & docs: yes | robots 404 | Title, ref, Tender ID, org chain, dates; detail: value, EMD, category, location, description | Docs behind CAPTCHA → manual | 15 min (per site) | Medium (sessions) | Medium | P2 (enrichment) | Home verified; active list candidate |
| Government eTenders | https://etenders.gov.in/eprocure/app | NHAI, AAI, OFB, TCIL… | Same | Same | No | None | Same | Not checked | Same | Same | 15 min | Medium | Medium | P2 | Home verified |
| State GePNIC portals | e.g. mahatenders.gov.in, tntenders.gov.in, eproc.punjab.gov.in, etenders.kerala.gov.in, mptenders.gov.in | State depts, smart cities, municipal bodies, state PSUs | Same | Same software | No | None | Same | Not checked | Same | Same | 15 min | Medium | Medium | P3 (after CPPP state feed check) | Candidate |
| CPPP ePublishing | https://eprocure.gov.in/epublish/app | Orgs that only publish | Tender notices | Not checked | — | — | — | — | — | — | — | — | — | P3 | Candidate |
| GeM bidplus | https://bidplus.gem.gov.in/all-bids | All GeM bids | Bids, RA, custom bids | JS + session | No public API found | Login to bid | — | **robots disallows automated access** | — | Restricted | — | — | — | Manual import only | Verified restricted |
| IREPS | https://www.ireps.gov.in | Indian Railways | Tenders | — | — | Login for most | — | **robots disallows automated access** | — | — | — | — | — | Manual import only | Verified restricted |
| BECIL tenders | https://www.becil.com/tenders | BECIL-managed projects, SI partner EOIs | EOI, RFP (incl. GeM-numbered) | Public HTML list | No | None | No | Not checked | Title, number, publish, last date | PDF links | Weekly-ish | Low | Medium | P3 (manual now, connector later) | Verified working |
| NTPC tender site | https://ntpctender.ntpc.co.in | NTPC / NTPC REL / NGEL NITs | Rate contracts, services | Public NIT pages | No | None for NIT | No | Not checked | NIT no., title, dates, EMD | Bidding on SAP portal | Continuous | Low | Medium | P3 | Verified working (NIT pages) |
| data.gov.in | https://data.gov.in | Open datasets | — | API | Yes (generic) | Key | — | — | No live tender dataset found | — | — | — | — | — | Unsupported |
| Commercial aggregators | Apify actors, tender18, tendersontime | Re-published CPPP/GeM | — | Paid scraping | — | — | — | Not official | — | — | — | — | — | Excluded | Not authoritative |

## 3. Ranking and MVP selection

Ranked by relevance × coverage × legal accessibility × reliability ÷ effort:

1. **CPPP central (`cpppdata`)** — broadest legal, unauthenticated coverage; one parser.
2. **CPPP states (`mmpdata`)** — same parser; covers smart cities, state IT departments, municipal bodies.
3. **Manual import** — the compliant path for restricted portals (GeM, IREPS) and for all documents.
4. GePNIC central/etenders (detail enrichment), CPPP corrigendum/cancelled feeds (change tracking),
   CPPP GeM feed — enable after `chanakya sources test` confirms the layouts.
5. PSU sites (BECIL, NTPC, state ITG corporations) — small connectors once value is proven.

## 4. Access rules Chanakya enforces

- Only allowlisted hosts; robots.txt honoured; 3 s minimum gap per host by default; bounded retries;
  circuit breaker after repeated failures; honest User-Agent with a contact address.
- No CAPTCHA, login or anti-bot workaround. When a page turns into a CAPTCHA, the source is marked
  *restricted* in the run report and coverage is reported as reduced.
- Incremental discovery: listings are newest-first, so scans stop at the previous high-water mark
  (minus a 12 h overlap), keeping daily load to the pages actually published since the last run.

## 5. Open items

- Confirm `mmpdata` and `gemdata` table layouts and pagination live.
- Confirm GePNIC `FrontEndLatestActiveTenders` pagination works with a cookie jar (expected), and
  whether detail pages open without CAPTCHA on every portal.
- Ask NIC/DoE (support-eproc@nic.in) whether an XML/data feed can be provided to registered
  organisations — the CPPP newsletters describe an XML publishing channel.
- Decide per-portal politeness limits with real latency data.
