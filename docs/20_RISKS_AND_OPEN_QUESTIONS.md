# 20 — Risks and Open Questions

## Verified vs. not yet verified

| Claim | Status | How to verify |
|---|---|---|
| CPPP `cpppdata` listing structure (columns, Tender ID format, pagination) | Observed on 2026-10-10 via web fetch; parser tested on a reconstruction of that page | `chanakya sources test -s cppp_central` from the deployment network |
| CPPP `mmpdata` / `gemdata` share the same template | **Assumed** (same site template) | `sources test` for each |
| GePNIC active-tender list and detail-page layout | Home-page rows observed; list/detail parsers tested on **synthetic** pages | Enable `gepnic_central`, run `sources test`, save a real page as a fixture |
| Documents require CAPTCHA on CPPP/GePNIC; GeM/IREPS need login | Observed / documented on the portals (doc 02) | — (no bypass will be attempted) |
| Ollama Cloud API, auth, usage fields, no `format` in cloud | From docs.ollama.com (2026-10-10) | `chanakya llm check`; one dry run in cloud mode |
| gpt-oss:20b / gpt-oss:120b available to the account and plan | **Unknown** — model pages could not be fetched in the build session | `chanakya llm check` lists visible models |
| gpt-oss produces valid JSON reliably without `format` | **Unknown** — mitigated by validation + one repair retry | Watch `invalid_output` rate in `ai_usage_events` during shadow runs |
| Wayam capabilities | From wayam.ai pages (doc 04); 14 accelerators known only from homepage cards | BD review of `wayam_capabilities.yaml` |
| Wayam turnover, years, certifications, government references | **Not published** → profile is null | BD/finance provide verified values |
| Monthly LLM cost < $5 | Modelled (doc 19) with measured prompt sizes and assumed volumes | Two weeks of `chanakya costs` |
| Docker image builds and runs | Dockerfile written; **not built** (no daemon in the build sandbox) | CI `docker` job |

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
