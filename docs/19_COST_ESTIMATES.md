# 19 — Cost Estimates

All LLM figures below are produced by `scripts/cost_model.py`, which builds real prompts with the
production prompt builders and prices them with the list in `config/chanakya.example.yaml`
(Ollama Cloud price list, read on 2026-10-10). Re-run it after changing models, prices or budgets.
Token counts use Chanakya's conservative estimator (≈3.5 characters per token); Ollama's reported
counts replace estimates in production.

## 1. LLM inference (Ollama Cloud)

### Measured prompt sizes and per-call cost

Prices: gpt-oss:20b $0.07/M in, $0.3/M out; gpt-oss:120b $0.15/M in, $0.6/M out (Ollama Cloud list, standard rate).

| Call | Input tokens (measured) | Output tokens (assumed) | Cost per call (USD) |
|---|---|---|---|
| Fast triage (gpt-oss:20b) | 988 | 160 | 0.000117 |
| Deep analysis with document excerpt (gpt-oss:120b) | 4,885 | 700 | 0.001153 |
| Baseline: deep model per notice, listing data + full 21-capability catalogue (gpt-oss:120b) | 3,156 | 700 | 0.000893 |
| Worst case deep call at the caps (6,000 in / 1,200 out) | 6,000 | 1,200 | 0.001620 |

Worst case for one run at every cap (15 deep + 45 triage calls): **$0.0315** (the configured `max_cost_per_run_usd` is $0.50).

### Monthly projection

Assumptions: 5% of new/changed notices reach CANDIDATE/STRONG; 60% of those need triage; 8 deep analyses/day (limited by documents BD imports); 30 days/month; unchanged notices cost nothing (content-hash cache).

| New/changed notices per day | Funnel: triage/day | Funnel: deep/day | Funnel USD/month | LLM on every notice USD/month | Funnel share |
|---|---|---|---|---|---|
| 500 | 15 | 8 | 0.33 | 13.40 | 2.5% |
| 1,500 | 45 | 8 | 0.43 | 40.20 | 1.1% |
| 6,000 | 180 | 8 | 0.91 | 160.81 | 0.6% |
| 15,000 | 450 | 8 | 1.86 | 402.03 | 0.5% |

Sensitivity at 6,000 notices/day:

| Candidate rate | Deep/day | Funnel USD/month |
|---|---|---|
| 5% | 8 | 0.91 |
| 10% | 15 | 1.78 |
| 20% | 15 | 3.05 |
| 20% | 30 | 3.57 |


Reading the table:

- At the volumes Chanakya will actually see (central + state CPPP feeds, ≈1,500 to 6,000 new or changed
  notices a day), the funnel costs **under $1 per month** at the base assumptions and **≈$1.8 to 3.6 per
  month** in the pessimistic cases. Sending every notice to the deep model would cost ≈$40 to 160 per
  month for worse recall (doc 11).
- The 30-deep-analyses-per-day row needs two runs a day or a higher `max_deep_analyses_per_run`
  (default 15).
- The default budget (`$0.50`/run, `$2.00`/day) is a safety net roughly 15× above the worst case of a
  run that hits every count cap.

## 2. Ollama plans (ollama.com/pricing, read 2026-10-10)

| Plan | Price | Included usage | Concurrency | Fit for Chanakya |
|---|---|---|---|---|
| Free | $0 | Starter usage credits; access to starter models; buy usage credits to unlock all models | 1 concurrent request | Sufficient if gpt-oss:20b/120b are available to the account (open question — check with `chanakya llm check`); Chanakya calls sequentially |
| Pro | $20/month (or $200/year) | $60 of usage credits per month; larger pro models | 3 | Covers projected usage many times over |
| Max | $100/month | $300 of usage credits per month | 10 | Not needed |
| Team (early access) | $500/month | $1,000 shared credits | — | Not needed |

Usage is metered in tokens at each model's rate; plan credits are used first, then purchased credits.
Some models are listed with lower off-peak rates; the default 07:30 IST run (02:00 UTC) is outside the
listed weekday peak window (12:00 to 18:00 UTC). Prices change — the price list in the config is the single
place to update.

**Recommendation:** start on pay-as-you-go credits (or Free, if the gpt-oss models are included), set
the budget caps as shipped, and review `chanakya costs` weekly during the shadow phase. Upgrade only if
concurrency or a pro-only model becomes necessary.

## 3. Local inference (llm.mode: local) — alternative

Zero per-token cost; the cost moves to hardware. gpt-oss:20b needs a machine with roughly 16 GB of
GPU or unified memory and gpt-oss:120b far more (figures from the model publisher's announcements, not
verified in this build — confirm on the Ollama model page before sizing). For Chanakya's volume (tens of
calls a day) a dedicated GPU server is not economical compared with ≈$1/month of cloud usage; local mode
is worth it only for data-residency requirements or if a GPU machine already exists.

## 4. Infrastructure

| Item | Sizing | Cost basis |
|---|---|---|
| Compute | 1 vCPU / 1 to 2 GB, a few minutes per day (demo run: 1.6 s, 112 MB peak; real runs dominated by polite fetching, ~3 s per page) | Scheduled container job billed per second, or the smallest VM — check the provider's calculator for the chosen region |
| Storage | SQLite + 30 workbooks + documents: < 5 GB in year one (assumption: < 10 MB per day of documents) | Standard file share or managed disk |
| Database (optional) | Smallest managed PostgreSQL tier | Only when the dashboard arrives |
| Email | Existing Microsoft 365 / Google Workspace mailbox or a transactional email service | Typically no extra cost at one digest per day |
| CI | GitHub Actions minutes for tests + image build | Within typical free/included minutes for a small repo (verify the organisation's plan) |

Infrastructure prices are not quoted here because they depend on the provider, region and existing
agreements; at this sizing they are expected to be the smallest line items on any cloud bill.

## 5. People time (the real cost)

| Activity | Estimate (assumption) |
|---|---|
| BD daily review of the digest | 10 to 20 minutes |
| Downloading documents for shortlisted notices (CAPTCHA, manual) | 5 minutes per notice |
| KB tuning during shadow phase | 1 to 2 hours per week for 2 to 3 weeks |
| Engineering upkeep (portal layout changes, upgrades) | 2 to 4 hours per month |

## 6. Assumptions to revisit after two weeks of real runs

- Share of notices reaching CANDIDATE (5% assumed; the live snapshot was biased towards technology
  sources and is not representative of the full CPPP feed).
- Output tokens per call (160 triage / 700 deep assumed; gpt-oss reasoning at `think: low` adds output
  tokens — measure from `ai_usage_events`).
- Deep analyses per day (bounded by how many documents BD imports).
