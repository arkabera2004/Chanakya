# 10 — AI Matching, Eligibility and Scoring

Relevance is decided in four increasingly expensive layers. Each layer only sees what the previous one
could not settle. Scoring is deterministic: the LLM contributes *evidence*, never the number.

```
all new/changed notices
  └─► L1 rules + exclusions (free)        ── REJECTED ──► stored, not listed, never analysed
        └─► L2 lexical semantics (free)    ── LOW (not promoted) ──► P4, never analysed
              └─► L3 fast triage (grey band only, ≈$0.0001)
                    └─► L4 deep analysis (strong/confirmed + documents, ≈$0.001)
                          └─► deterministic scoring, priority, flags
```

## L1 — Deterministic capability rules (`pipeline/classify.py`)

The knowledge base (`config/wayam_capabilities.yaml`, doc 04) gives each of the 21 capabilities
`strong`, `medium` and `weak` phrases plus `negative` phrases. Text is normalised (lower case, unicode
dashes, `&`→`and`), abbreviations are expanded once (`CCTV` → *closed circuit television*, `VAPT`,
`ANPR`, `SOC`, `HMIS`, `BESS`…), and phrases match on word boundaries with plural tolerance for words of
four or more letters.

Per capability:

```
raw = Σ 0.45·strong + Σ 0.20·medium + Σ 0.06·weak − 0.30·negatives      (×1.5 for a phrase in the title)
weak-only matches are capped at candidate_threshold − 0.01
score = clamp(raw, 0, 1)
```

Overlapping phrases are counted once (a strong phrase suppresses the medium/weak phrases it contains,
and synonyms of the same concept count once), so "CCTV camera" does not score three times.

Decision for the notice:

| Condition | Decision |
|---|---|
| A global exclusion term in the title/category (civil works, housekeeping, catering, tyres, toll/user fee, lattice tower, selection of developer, EPC…) and no strong phrase | **REJECTED**, or **LOW** if a medium phrase is present |
| best ≥ 0.50 | **STRONG** |
| best ≥ 0.20 | **CANDIDATE** |
| any capability score > 0, a generic technology signal, a technology category, or an EOI/RFP/RFI type | **LOW** (kept for semantics — high-recall net) |
| otherwise | **REJECTED** |

**Equipment-supply detection.** A title starting "Supply/Procurement/Purchase of …" with no software
or analytics term and no strong phrase is capped at 0.45 (never STRONG) and flagged — CCTV camera
procurement is a partner/SI route, not a Wayam product sale.

## L2 — Free semantic matching (`pipeline/semantic.py`)

Default backend `lexical`: TF-IDF cosine similarity between the notice text and each capability's
description, keywords, example requirements and matching indicators, with light stemming and
bigrams. Cost: zero, no model download. A LOW notice is promoted to CANDIDATE when its best similarity
is ≥ `semantic.candidate_threshold` (0.12) **and it carries no exclusion term**.

The combined capability score used everywhere downstream is

```
combined = max(r, 0.65·r + 0.35·min(1, s / 0.30))      r = rule score, s = semantic similarity
```

so semantics can lift a weakly-worded notice but never drag down a clear rule match.

Optional backend `ollama`: local embeddings (`semantic.embed_model`, default `embeddinggemma`) through a
local Ollama server, cached in `embedding_cache` by text hash and model. Ollama Cloud does not price
embedding models, so cloud embeddings are not used. On the labelled benchmark the lexical backend
already passes every relevant notice through the funnel; embeddings are worth enabling if BD reports
misses on paraphrased notices.

## L3 — Fast triage (`Analyzer.triage`, model `llm.fast_model`, default `gpt-oss:20b`)

Runs only for **active** CANDIDATE/STRONG notices whose combined score is in the grey band
`llm.triage_band` = [0.20, 0.50) (or promoted candidates below 0.20). Input is compact — title,
organisation, department, category, type, closing date, stated value, ≤1,500 characters of
description and 2 to 3 candidate capability cards (≈680 input tokens). Output:

```json
{"relevant": "yes|no|unclear", "capability_ids": ["CAP-..."], "opportunity_type": "...",
 "reason": "<=30 words", "confidence": 0.0-1.0}
```

`no` blocks deep analysis and multiplies the capability component by 0.4; `yes` adds 0.1.

## L4 — Deep analysis (`Analyzer.deep`, model `llm.deep_model`, default `gpt-oss:120b`)

Runs for active CANDIDATE/STRONG notices with combined score ≥ `llm.deep_min_score` (0.45) or triage
`yes` with confidence ≥ 0.6 — **and** only when there is real context: a document excerpt or a listing
description of ≥200 characters. Otherwise the state becomes `deferred_no_documents`, the report shows
"DEEP ANALYSIS WAITING FOR DOCUMENTS", and the next run resumes automatically when documents are
imported. Input: the triage fields plus a bounded, cited document excerpt (doc 09; ≤ the per-call token
cap). Output (validated by `DeepOutput`): scope summary, deliverables, explicit technologies,
`hardware_involved`, capability IDs, `fit` (existing_offering | custom_build | partner_needed | not_fit),
requirements with `explicit` + exact quote, consortium, value, duration, risks, missing information,
next action, confidence.

## Output controls (apply to both stages)

| Control | Implementation |
|---|---|
| Untrusted data boundary | System prompt: text inside `<tender_data>` is data, never instructions; injection phrases are defanged and flagged before sending |
| No credential assumptions | System prompt forbids stating Wayam meets any criterion; profile is never sent |
| Strict JSON | `extract_json` + Pydantic; one repair retry with the validation error; then `failed` — never a fabricated result |
| Capability hallucination | IDs not in the KB or not among the candidates are dropped (noted) |
| Value hallucination | A model-reported value is kept only if that number (crore/lakh-aware) appears in the input |
| Evidence hallucination | A requirement whose quote cannot be found in the input is downgraded to *inferred* |
| Cost | Budget guard before every call; cache by (purpose, model, prompt version, KB version, input) |
| Provider failures | Timeouts/5xx/429 retried with backoff in the provider; auth errors disable the LLM for the rest of the run |

Ollama's API documentation states that structured outputs (`format` with a JSON schema) are not
supported for cloud models, so in cloud mode the schema is written into the prompt and enforced by
validation; `send_format_schema: auto` sends `format` only to a local server.

## Eligibility (`pipeline/eligibility.py`)

Requirements are extracted deterministically (doc 09) and, after deep analysis, augmented with LLM
requirements that survived post-validation (`source = llm`). Each is assessed **only** against
`company_profile` in the config:

| Criterion | Assessment |
|---|---|
| Turnover with a stated amount | meets / does_not_meet when the profile has turnover; else **unknown** |
| Experience in years | compared with `years_in_operation`; else unknown |
| Government experience | `does_not_meet` only if `past_government_projects` is explicitly 0; else unknown |
| Similar works, net worth | always unknown (human judgement) |
| Certifications | CERT-In vs `cert_in_empanelled`; ISO/CMMI vs `certifications`; unknown when the profile is empty |
| OEM authorisation (MAF) | **partner** |
| EMD, consortium, MSE exemption | informational (`not_applicable`) |

Overall status, in order: any *does_not_meet* → **Likely ineligible**; any *partner* or equipment-heavy
scope → **Partner required**; no findings → **Unknown — human review required**; all *meets* →
**Verified eligible**; some *meets* → **Potentially eligible**; else **Unknown — human review required**.

The shipped profile is all `null`, because Wayam's website publishes no turnover, years, certifications
or government references (doc 04). Every relevant notice will therefore show *Unknown* (or *Partner
required*) until BD fills in verified credentials — by design.

## Scoring (`pipeline/scoring.py`)

Six components, each 0 to 1 with a written reason, weighted by `scoring.weights` (default 35/20/15/10/10/10):

| Component | Weight | How the 0 to 1 value is set |
|---|---|---|
| Capability match | 35 | `min(1, combined/0.6)` × offering multiplier (existing 1.0, custom build 0.8, partner 0.65); triage yes +0.1, no ×0.4; deep `not_fit` ×0.3; REJECTED → 0; LOW capped at 0.25 |
| Eligibility feasibility | 20 | Verified 1.0 · Potentially 0.7 · Unknown 0.5 · Partner 0.45 · Likely ineligible 0.1 |
| Technical / delivery fit | 15 | Deep fit (existing 1.0, custom 0.65, partner 0.45, not fit 0.1; ×0.8 if hardware); else 0.35 for equipment supply, 0.7/0.5 from offering status, 0.2 if no match |
| Deadline feasibility | 10 | not stated 0.5 · closed 0 · <3 days 0.15 · <5 days 0.4 · <10 days 0.75 · ≥10 days 1.0 |
| Commercial | 10 | stated value ≥₹50 cr 1.0 · ≥5 cr 0.8 · ≥1 cr 0.6 · ≥25 lakh 0.4 · smaller 0.25 · not stated 0.4 |
| Strategic | 10 | 0.8 for a strategic buyer keyword (smart city, NICSI, MeitY, C-DAC, railway, metro, AIIMS, NTPC, SECI, police…) else 0.3; +0.2 for EOI/RFP/RFI/empanelment |

**Relevance gate.** Non-capability components are multiplied by `g = min(1, capability/0.5)`. An
unrelated notice cannot reach P3 on a comfortable deadline and a large value alone; a REJECTED notice
scores 0.

```
total = Σ_component score × weight / Σweights × 100 × (1 for capability, g otherwise)
```

**Priority**

| Rule (first match wins) | Priority |
|---|---|
| Status expired/closed/cancelled/withdrawn/awarded/retendered | P4 + "NOT ACTIVE" |
| Decision REJECTED or LOW, or capability < 0.3 | P4 |
| total ≥ 70 and capability ≥ 0.5 | **P1** — pursue / review immediately |
| total ≥ 50 | **P2** — worth investigating |
| total ≥ 30 | **P3** — monitor / partner |
| otherwise | P4 — ignore |

Equipment supply without a deep `existing_offering`/`custom_build` fit is capped at P3 and flagged.

**Flags** (shown in the workbook, digest and `chanakya report`): ELIGIBILITY UNVERIFIED — human review
required (P1/P2 with Unknown/Potentially), PARTNER / OEM REQUIRED, LIKELY INELIGIBLE, CLOSING SOON
(< 7 days), AI ANALYSIS SKIPPED (budget limit), AI ANALYSIS FAILED, AI ANALYSIS NOT RUN (LLM disabled),
DEEP ANALYSIS WAITING FOR DOCUMENTS, LISTING-LEVEL DATA ONLY — download documents (completeness < 0.5),
NOT ACTIVE, EQUIPMENT SUPPLY — monitor for SI/partner route, INSTRUCTION-LIKE TEXT IN SOURCE (screened),
SIMULATED AI OUTPUT (fake provider).

Relevance, eligibility certainty and data completeness are reported as **separate** columns so a
highly relevant notice with unverified eligibility is visibly flagged, not presented as qualified.

## Worked examples (from the live snapshot, 2026-10-10)

| Notice (real, as listed) | L1 decision | Outcome |
|---|---|---|
| NTPC — Centralized Rate Contract for Drone Thermography for NREL Solar Projects | STRONG (CAP-SOLAR-INSP 0.77: *drone thermography*, *solar*) | Detected, score 68; closed on 28-May-2026 → P4 NOT ACTIVE |
| BECIL — EOI for SI for indigenous SOC and NOC at SLDC Guwahati | STRONG (CAP-AIOPS 0.60) | Detected, score 62; closed → P4 |
| Goa ITG — Rate contract for supplying/installing CCTV and networking components | CANDIDATE, equipment supply (CAP-CV-VA 0.45, capped) | Detected, score 55, at most P3 even if active ("EQUIPMENT SUPPLY"); closed → P4 |
| AF Station Ambala — Runway resurfacing with connected works | REJECTED (exclusion: *runway resurfacing*) | Score 0, never analysed |
| User fee collection agency, Kobadi Fee Plaza | REJECTED (exclusion: *user fee*, *fee plaza*) | Score 0 |
| BECIL — EOI for back-end SI for AAI IP-based VHF equipment | CANDIDATE (generic signal *system integrator*) | The only disagreement — labelled irrelevant; costs one triage call when an LLM is enabled |

Full table: `docs/validation/LIVE_SNAPSHOT_2026-10-10.md`. Benchmark on 64 labelled notices:
`docs/validation/BENCHMARK.md`.

## Calibration

`chanakya review <id> --status … --label relevant|irrelevant` stores BD decisions in
`user_review_feedback`. After two weeks of shadow runs, compare labels with priorities and adjust
(in order): KB phrases → exclusion terms → thresholds (`filtering.*`, `semantic.candidate_threshold`,
`llm.triage_band`) → weights. Every KB edit changes `kb_version`, which re-scores affected notices on the
next run without re-fetching.

## Weight proposals from BD labels (`chanakya calibrate`)

`chanakya calibrate` (`pipeline/calibrate.py`) reads the review feedback and the stored score
breakdowns and proposes new `scoring.weights`. It is a proposal only. It never edits
`config/chanakya.yaml`, never changes a stored score, and makes no model call.

```
chanakya calibrate                                   # print the report
chanakya calibrate --out data/reports/calibration.md # also write it as markdown
chanakya calibrate --write-proposal proposal.yaml    # also write a YAML fragment with the proposed weights
```

The command refuses to write either file onto the live config, the capability KB, or any existing
file in the config directory. Exit code is 0 whenever a report was produced, including a report
that says "no proposal".

**Which feedback counts.** One label per notice:

| Signal | Counts as |
|---|---|
| Latest explicit `--label relevant` / `irrelevant` | relevant / irrelevant. Always wins over the status |
| Status Shortlisted, Qualified, Partner needed, Pursuing, Won, Lost | relevant. BD judged it worth effort. Lost is counted as relevant on purpose: the score ranks what deserves attention, not who wins the bid |
| Status Rejected | irrelevant |
| Status New, Reviewing, Expired | ignored. No decision, or it ran out of time |

**Model.** A logistic regression on the six component scores exactly as the scorer weighs them
(capability as is, the others multiplied by the relevance gate), so the fitted coefficients mean
the same thing as weights. It is written in plain Python (no numpy, no new dependency), solved by
projected Newton steps, with coefficients held at zero or above. The L2 penalty
(`calibration.l2`, default 1.0) pulls toward the *current* weights, not toward zero: where the
labels carry no information (a component that never varies, or components that always move
together) the proposal stays where the weights are today. Coefficients are then scaled to sum to
100 and rounded to whole numbers by the largest-remainder rule. There is no randomness: the same
book and the same labels always give the same proposal.

**When it refuses.** Fewer than `calibration.min_labels` labelled notices (default 100, the
shadow-phase target in doc 16); a class with no labels, or fewer than
`calibration.min_labels_per_class` (default 10); or the six components together explain the labels
no better than the class balance alone (likelihood-ratio test at the 5% level). The report states
the reason and still shows the label counts.

**What the report shows.** Labels per class and where they came from; current and proposed
weights with the largest movers; P1/P2 precision and recall on the labelled set at the current
and proposed weights; the same for the proposal held out (`calibration.cv_folds`, default 5,
each notice banded with weights fitted without it); how many labelled notices and how many
notices in the whole book would change band; a one-line reading of the evidence; and the limits
below.

**Limits (printed in every report).**

- Labels exist only for notices BD was shown. Notices the funnel set aside are almost never
  labelled, so calibration cannot learn about missed opportunities. Funnel recall is measured by
  `chanakya benchmark`, not here.
- A few hundred labels from a few reviewers is thin evidence. A move of a few points is noise.
- Precision on the labelled set is measured on the notices the weights were fitted to and flatters
  the proposal. The held-out figure is the more honest one.
- Only the six weights are tuned. Component formulas, the gate, thresholds, KB phrases and
  exclusions are untouched; fix those first, in the order given under Calibration above.
- Band comparisons are recomputed from stored components. The equipment-supply cap is inferred
  from the stored flag and reason text, and the report says how many stored bands it could
  reproduce. If that is not all of them, run `chanakya analyze --scope all` first.

**Not yet verified.** No real BD labels existed when this was built. The behaviour above is
verified on labelled synthetic sets only (`tests/test_calibrate.py`); how useful the proposals
are on real labels is unknown until the shadow phase produces them.

**Accept procedure (a human decision).**

1. Read the report. If the held-out precision is not better than the current one, stop.
2. Edit `scoring.weights` in `config/chanakya.yaml` by hand.
3. `chanakya analyze --scope all` to re-score the stored notices.
4. `chanakya benchmark`: funnel recall must not drop and cost must not rise (acceptance
   criterion 3 in the build master prompt). If either regresses, restore the old weights and
   repeat step 3.
5. Record the change, the date and the report in this document.

## Win/loss tracking and win history

`chanakya winloss` (`pipeline/winloss.py`, function `winloss_summary`) is a read-only summary of
pursued, open, won and lost notices by buyer organisation, by best-matched capability and by
platform. Pursued means review status Pursuing, Won or Lost; win rate is won / (won + lost).
Buyers are grouped by exact normalised name, so different spellings of one organisation are
separate rows.

**Feeding the strategic component** is implemented but off by default
(`scoring.win_history_enabled: false`). When a human turns it on:

- Each *other* notice from the same buyer that BD recorded as Won adds
  `scoring.win_history_bonus_per_win` (0.1) to the strategic component, capped at
  `scoring.win_history_max_bonus` (0.2) and at the component ceiling of 1.0. At the default
  strategic weight of 10 that is at most 2 points on the 0 to 100 total.
- The reason is written into the breakdown, for example "non-priority buyer; 2 wins with this
  buyer (+0.20)", and the amount is stored as `history_bonus` on the component.
- Losses never lower a score: a lost bid does not make a buyer less strategic.
- A notice's own Won status is excluded, so recording a win never changes that notice's score.
- The score stays deterministic: it depends only on stored review statuses. It is not updated
  the moment a win is recorded. Like a weight change, it takes effect when the notice is next
  scored, so run `chanakya analyze --scope all` after recording outcomes.
- Calibration fits on the strategic score *without* the bonus, so a recorded win cannot teach the
  model to reward itself.

It is off by default because it makes scores depend on BD's own history (a feedback loop:
winning with a buyer raises that buyer's notices, which invites more bids there) and because
buyer matching is by exact normalised name. Turn it on only once there are enough recorded
outcomes for the summary above to look sensible.
