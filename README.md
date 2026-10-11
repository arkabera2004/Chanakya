# Chanakya

Government procurement opportunity intelligence for **Wayam AI**. Chanakya watches Indian public
procurement portals, filters tens of thousands of notices down to the few that fit Wayam's
offerings, analyses them with the least possible paid-LLM usage, and delivers a prioritised Excel
workbook and email digest: with every claim traceable to its official source.

```
portals ─▶ connectors ─▶ normalise ─▶ dedupe/version ─▶ rules ─▶ semantics ─▶ documents ─▶ selective LLM ─▶ score ─▶ Excel / email
            (CPPP, GePNIC,          (SQLite/Postgres)    (free)     (free)                  (Ollama, budgeted)  (deterministic)
             manual import)
```

## Desk

The web console is the same pipeline, narrated by Vira. It reads one config: `config/chanakya.yaml`, with `OLLAMA_API_KEY` from `.env`. The plan for screens, icons, and the watch API is [docs/21_DESK_UI_AND_API.md](docs/21_DESK_UI_AND_API.md).

```bash
chanakya serve
cd web && npm install && npm run dev
```

Open http://localhost:3000. A pass writes the same database, workbook, and review log as the CLI.

To show the desk without the network or a key, start the API on the synthetic inbox. The header then says "Demo data".

```bash
python scripts/make_demo_data.py
CHANAKYA_CONFIG=config/chanakya.demo.yaml chanakya serve
```

## Status (2026-10-10)

| Area | State |
|---|---|
| CPPP listing connector (central/state/GeM feeds) | Built against the live page structure observed 2026-10-10; fixture-tested. **Live run not possible from the build sandbox** (network allowlist): run `chanakya sources test` once deployed. |
| GePNIC connector (eprocure / etenders / state portals) | Listing + detail parsing built and fixture-tested; home-page variant verified against live rows. Live pagination unverified. |
| Manual import (CSV/XLSX/YAML + documents) | Working, tested. Compliant path for GeM bidplus and IREPS (both disallow automated access) and PSU sites. |
| Rules + semantic funnel, eligibility, scoring | Working, tested, benchmarked. |
| Ollama Cloud / local provider | Implemented against the documented API with mocked tests. **Not exercised with a real key** (none provided). |
| Excel (8 sheets) + email (SMTP / file) | Working, tested (file provider). SMTP tested with mocks only. Digest daily or weekly, plus an optional alert for each new P1 (`email.alert_on_new_p1`). |
| Docker / CI | Files provided; image **not built here** (no Docker daemon). CI runs the full test suite. |
| Email alerts | Digest daily or weekly, alert for each new P1, corrigendum alert and deadline reminders for tracked notices. Tested with the file provider only. |
| Desk | Desk, Leads, Products, Passes, Sources, Costs and Profile pages. Documents can be attached to a notice and analysed from its page. Checked in a headless browser. |
| PostgreSQL + Alembic | Migrations in `src/chanakya/migrations`; `chanakya db current\|upgrade\|stamp\|copy-from`. Suite passes on SQLite and on a local PostgreSQL 18; the CI Postgres job has never run. The live SQLite database is not yet stamped. |
| Logs and metrics | `app.log_format: json`, one `run_finished` event per run, `chanakya metrics`, `GET /api/metrics`. Example alert rules in doc 18 are not loaded into a monitor. |
| Learning loop | `chanakya calibrate` proposes weights from BD labels and never applies them; `chanakya winloss`. Verified on synthetic labels only: there are no real labels yet. |
| Award notices | Parser and `chanakya awards import\|report` built. The CPPP results page is a CAPTCHA form, so `awards scan` is disabled and the parser is unverified against a real page. |
| BOQ tables | Ruled PDF tables reach the analyser as rows. Tested on generated PDFs only. |

313 automated tests pass offline (`pytest -q`). See `docs/01_EXECUTIVE_SUMMARY.md` for findings and
`docs/20_RISKS_AND_OPEN_QUESTIONS.md` for what still needs validation.

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# Offline demo: synthetic records, simulated AI, no network or key needed
python scripts/make_demo_data.py
chanakya --config config/chanakya.demo.yaml run --dry-run
chanakya --config config/chanakya.demo.yaml report

# Real use
chanakya init                                   # creates config/chanakya.yaml + data/ + database
cp .env.example .env                            # add OLLAMA_API_KEY, SMTP credentials
chanakya sources test                           # check each portal can be fetched and parsed
chanakya llm check                              # verify the key and that configured models exist
chanakya run --dry-run                          # full pipeline, digest rendered to data/outbox/dry_run
chanakya run --email                            # full pipeline + email
```

Set `llm.mode: cloud` in `config/chanakya.yaml` to enable AI triage/analysis (default `disabled`:
everything deterministic still runs and the report says AI was not used).

## Commands

| Command | Purpose |
|---|---|
| `chanakya run [--source ID] [--since YYYY-MM-DD] [--email] [--dry-run] [--llm-mode MODE]` | Whole pipeline in one command |
| `chanakya scan` / `chanakya analyze [--scope changed\|all]` | Acquisition only / assessment only |
| `chanakya export --format xlsx` · `chanakya report` · `chanakya email --latest [--dry-run] [--kind digest\|weekly\|alert]` | Outputs |
| `chanakya status` · `chanakya costs --last-days 30` · `chanakya healthcheck` | Operations |
| `chanakya sources list\|test` · `chanakya llm check` · `chanakya capabilities validate` | Diagnostics |
| `chanakya import FILE` · `chanakya import DIR_OR_PDF --tender-id ID` | Manual notices / attach documents |
| `chanakya review CHK-… --status Shortlisted --label relevant` | Record BD decisions (feeds calibration) |
| `chanakya benchmark` · `chanakya schedule --daily-at 07:30` | Cost benchmark · simple daily scheduler (IST) |
| `chanakya serve` · `chanakya watch` | Desk API · keep scanning every enabled source |
| `chanakya metrics [--format prometheus\|json] [--out FILE]` | Run, source and 24-hour metrics for a monitor |
| `chanakya calibrate [--out FILE] [--write-proposal FILE]` · `chanakya winloss` | Weight proposal from BD labels (never applied) · pursued, won and lost |
| `chanakya awards import FILE` · `chanakya awards report` | Load a saved results page or CSV · who won what |
| `chanakya db current\|upgrade\|stamp\|copy-from` | Schema migrations and the SQLite to PostgreSQL move |

`run`, `scan`, `analyze`, `schedule` and `watch` each hold `data/watch.lock` while they write. A second writer exits `1` instead of starting.

Exit codes: `0` ok · `1` run/config error · `2` every source failed · `3` partial (a source failed or an email was not delivered; the report was still produced).

## Repository layout

```
config/            chanakya.example.yaml · chanakya.demo.yaml · wayam_capabilities.yaml (editable KB)
src/chanakya/      connectors/ pipeline/ documents/ llm/ reporting/ cli.py app.py db.py security.py http.py
tests/             313 offline tests; fixtures/ (observed portal rows, synthetic fixtures, labelled benchmark set)
scripts/           make_demo_data.py · validate_live_snapshot.py · render_capability_matrix.py · cost_model.py · backup.sh
docs/              01 to 21 design documents, CHANAKYA_BUILD_MASTER_PROMPT.md, validation/
```

## Principles built in

- **No LLM where rules suffice.** Unrelated notices are rejected for free; the cheap model only
  resolves ambiguity; the large model only sees strong matches with document text. Analyses are
  cached by content hash, and a budget guard stops paid calls (never silently).
- **No invented facts.** Eligibility is `Unknown` unless Wayam's credentials are configured; tender
  values and requirement quotes returned by a model are discarded unless found in the source text.
- **No access-control bypass.** robots.txt is honoured; CAPTCHA-protected search and documents are
  never automated: the manual import path covers them.
- **Untrusted input.** SSRF allowlist, safe archive extraction, formula-injection neutralisation,
  HTML autoescaping, prompt-injection screening, secrets never logged.
