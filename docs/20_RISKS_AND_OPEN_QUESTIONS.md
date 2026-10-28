# 20 — Risks and Open Questions

## Verified vs. not yet verified

| Claim | Status | How to verify |
|---|---|---|
| CPPP `cpppdata` listing structure (columns, Tender ID format, pagination) | **BLOCKED_BY_CAPTCHA, investigated 2026-10-29** (see evidence log below): the SPA's own "Latest Active Tenders" listing requires a solved CAPTCHA to return any results — a stricter policy than the old portal. No code fix is possible without CAPTCHA-solving, which this project does not do. | Stays `unsupported` until/unless the portal removes this gate or offers a non-CAPTCHA public listing path |
| CPPP `mmpdata` / `gemdata` share the same template | **Moot** — the whole CPPP site has moved to the CAPTCHA-gated SPA described above; not re-tested individually | — |
| GePNIC active-tender list and detail-page layout | **LIVE-VERIFIED 2026-10-29**: `chanakya sources test` against `gepnic_assam`, `gepnic_mp`, `gepnic_kerala`, `gepnic_up`, `gepnic_rajasthan` — all 5 returned `OK`, 11 real records parsed each, from the current live portal (not fixtures) | Remaining GePNIC state portals not yet spot-checked; detail-page (`fetch_detail`) parser is still only tested on a synthetic fixture |
| Documents require CAPTCHA on CPPP/GePNIC; GeM/IREPS need login | Observed / documented on the portals (doc 02) | — (no bypass will be attempted) |
| Ollama Cloud API, auth, usage fields, no `format` in cloud | From docs.ollama.com (2026-10-10) | `chanakya llm check`; one dry run in cloud mode |
| gpt-oss:20b / gpt-oss:120b available to the account and plan | **Unknown** — model pages could not be fetched in the build session | `chanakya llm check` lists visible models |
| gpt-oss produces valid JSON reliably without `format` | **Unknown** — mitigated by validation + one repair retry | Watch `invalid_output` rate in `ai_usage_events` during shadow runs |
| Wayam capabilities | From wayam.ai pages (doc 04); 14 accelerators known only from homepage cards | BD review of `wayam_capabilities.yaml` |
| Wayam turnover, years, certifications, government references | **Not published** → profile is null | BD/finance provide verified values |
| Monthly LLM cost < $5 | Modelled (doc 19) with measured prompt sizes and assumed volumes | Two weeks of `chanakya costs` |
| Docker image builds and runs | Dockerfile written; **not built** (no daemon in the build sandbox) | CI `docker` job |

## CPPP live investigation — 2026-10-29 evidence log

Read-only investigation of `eprocure.gov.in/cppp/`'s current (post-SPA-migration) public
behavior, via ordinary browser navigation through the site's own menus — no synthesized
requests, no CAPTCHA solving or bypass attempted, no credentials used. No secrets, cookies,
or session tokens recorded below (none were observed on these public, unauthenticated pages).

**Navigation path:** Home → "Search Tender / Bids" menu → "Latest Active Tenders" (the
direct replacement for the old `/latestactivetendersnew/cpppdata` listing).

**Requests observed** (via the browser's own network activity during that navigation —
timestamps are this investigation's wall-clock order, not individually logged per request):

| # | Method | Path (under `/cppp/cpppapi/`) | Status | Purpose (inferred from UI effect) |
|---|---|---|---|---|
| 1 | GET | `login/getServerStatus` | 200 | Session/server health check on page load |
| 2 | POST | `server/generateCaptcha` | 200 | Issues the CAPTCHA image shown next to the Search button |
| 3 | POST | `cpppTenderDetailsPublic/cppporganisationNameList` | 200 | Populates the "Organisation Name" filter dropdown |
| 4 | POST | `cpppTenderDetailsPublic/cpppProductCategoryList` | 200 | Populates the "Product Category" filter dropdown |
| 5 | POST | `cpppTenderDetailsPublic/cpppSortOptionList` | 200 | Populates the "Sort Option" dropdown |
| 6 | POST | `cpppTenderDetailsPublic/getCpppCentralTenderGried` | 200 | The tender-listing grid endpoint itself |

**Rendered result:** both the "Central - Active Tenders" and "State - Active Tenders" tabs
show **"Total Count: 0"** and an empty results table on page load, with a field labelled
"Please enter the captcha*" (marked required) immediately next to the Search button. No
tender rows are shown unless Search is clicked, which requires a correctly solved CAPTCHA.

**What this establishes:**
- Request #6 (the actual listing grid) returns HTTP 200 with an empty result set on an
  unauthenticated page load — its JSON response body/schema was not captured (this
  investigation's tooling reads request metadata, not bodies, and re-issuing the same call
  outside the page's own navigation flow to read it was treated as a synthesized request and
  avoided per this phase's read-only boundary).
- Filter-metadata endpoints (#3–#5: organisation names, product categories, sort options) are
  open and CAPTCHA-free — the gating is specific to the listing/search endpoint, not the
  whole `/cpppapi` surface.
- A real tender listing requires submitting a solved CAPTCHA (request #2 mints the
  challenge). This project does not solve or bypass CAPTCHAs, so no further request was made
  and the actual tender-record response schema remains unobserved.

**What was NOT established (explicit blockers):**
- The `getCpppCentralTenderGried` request/response field schema for a non-empty result.
- Whether `/cpppapi` exposes any CAPTCHA-free path to tender data (e.g. a documents/results
  API, an RSS/export feed, or the GeM-bid mirror) — not explored this phase; would need its
  own targeted, low-rate investigation within the same read-only/no-bypass boundary.
- Pagination behavior, since no non-empty result set was ever reached.

**Conclusion:** `cppp_central`/`cppp_states`/`cppp_gem` (same application) stay
`unsupported`. This is a portal-side access-policy change (CAPTCHA-gated search), not a
connector defect — no amount of parser rewriting fixes it, and implementing against it would
require solving CAPTCHAs, which is out of scope by this project's own explicit rule.

## Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Portal layout changes break parsing | Medium | High (silent misses) | Header-driven parsing; `StructureChangedError` → source marked failed, exit 3, coverage note; fixture-based tests; `sources test` in runbook | Eng |
| R2 | Portals block or rate-limit the job (IP reputation, geo, bot rules) | Medium | High | Polite rate (≥3 s/host), robots, honest User-Agent, India-region hosting, circuit breaker; manual import fallback | Eng |
| R3 | Relevant notices worded unusually are missed | Medium | High | High-recall net (tech categories, EOI/RFP types, tech signals), lexical semantics, triage band; BD labels → KB updates; embeddings switch | BD + Eng |
| R4 | BD ignores the digest because of noise | Medium | High | Precision-first rules, relevance gate, P4 suppression, flags instead of inflated scores; calibrate in shadow phase | BD |
| R5 | Eligibility misjudged | Medium | High | Never assumed; "Unknown — human review required" by default; evidence quotes with page citations; human sign-off before bidding | BD |
| R6 | LLM hallucination (values, requirements, capabilities) | Medium | Medium | Post-validation against source text; capability allowlist; deterministic scores; confidence + basis shown | Eng |
| R7 | Prompt injection in tender text | Low | Medium | Untrusted-data delimiting, screening, model has no tools, typed outputs only (doc 13) | Eng |
| R8 | Cost overrun (misconfiguration, model change) | Low | Low to Medium | Per-run/day/call/token caps checked before every call; unpriced-model warning; `chanakya costs` | Eng |
| R9 | Ollama outage or key revoked | Low | Medium | Pipeline continues deterministically; flags "AI ANALYSIS FAILED/NOT RUN"; retried next run; local mode as fallback | Eng |
| R10 | Documents never imported → deep analysis never runs | High | Medium | `DEEP ANALYSIS WAITING FOR DOCUMENTS` flag; next action names the portal and Tender ID; Phase 5 shared-folder workflow | BD |
| R11 | Legal/terms-of-use concerns about automated access | Low to Medium | High | Public listing pages only, low rate, robots honoured, no CAPTCHA/login automation; legal review before enabling each source | Legal |
| R12 | Single-host SQLite data loss | Low | Medium | WAL, online backups with rotation, restore runbook; Postgres in Phase 4 | Eng |
| R13 | Hindi/regional-language notices | Medium | Medium | Synonyms are English-only today; OCR `hin` pack optional; add Hindi keywords to the KB; test gpt-oss on Hindi samples | BD + Eng |
| R14 | Timezone/date parsing errors on deadlines | Low | High | Day-first IST parsing, unparseable → "Not stated" (never guessed); tests; BD verifies on the portal before acting | Eng |
| R15 | Key or password leakage | Low | High | Env-only secrets, redaction, no secrets in config/DB/logs; rotate on exposure | Eng |

## Open questions for Wayam

1. **Credentials:** audited turnover (last 3 years), years since incorporation, certifications
   (ISO 9001/27001, CMMI), MSME/Udyam and DPIIT status, CERT-In empanelment, number of Indian government
   projects. Without these, eligibility stays *Unknown*.
2. **Strategy:** which buyers and sectors matter most (smart cities, railways, power, health, police)?
   Are partner/SI routes (camera supply + analytics) of interest, or only software-led notices?
3. **Thresholds:** minimum contract value worth bidding; minimum days needed to prepare a bid.
4. **Geography:** which state portals to enable first?
5. **Capabilities:** confirm the 21 capability entries, especially those marked custom build (ANPR,
   mining, healthcare RCM, application modernisation) and the accelerators known only from homepage
   cards.
6. **Operations:** who downloads documents for shortlisted notices; who owns KB updates; digest
   recipients; preferred hosting (Azure?) and email system.
7. **LLM account:** which Ollama plan/credits; is sending tender text to a cloud model acceptable, or is
   local mode required for some sources?

## Open technical questions

- Exact layouts of `mmpdata`/`gemdata` feeds and GePNIC active lists from the production network.
- Whether GePNIC detail pages can be fetched within a session without hitting a CAPTCHA at polite rates.
- gpt-oss JSON compliance and real token usage at `think: low`; whether a smaller model suffices for triage.
- Whether lexical semantics holds recall on the full CPPP stream (embeddings switch ready).
- Retention policy for raw payloads and documents.

## Assumptions made in this build

- Daily cadence at 07:30 IST is sufficient (notices typically stay open for weeks).
- English-language listings dominate central feeds.
- One scheduled writer; no concurrent runs.
- BD reviews every P1/P2 before any action; Chanakya is decision support only.
