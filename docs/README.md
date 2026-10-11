# Chanakya documentation

| # | Document | Contents |
|---|---|---|
| 01 | [Executive summary](01_EXECUTIVE_SUMMARY.md) | What was built, key findings, results, what remains |
| 02 | [Government source research](02_GOVERNMENT_SOURCE_RESEARCH.md) | Portal-by-portal access, structure, restrictions, priority |
| 03 | [Open-source comparison](03_OPEN_SOURCE_COMPARISON.md) | Tender projects and libraries evaluated, licences, decisions |
| 04 | [Wayam capability matrix](04_WAYAM_CAPABILITY_MATRIX.md) | Generated from [`config/wayam_capabilities.yaml`](../config/wayam_capabilities.yaml) — the machine-readable KB |
| 05 | [Product requirements](05_PRODUCT_REQUIREMENTS.md) | Users, goals, functional/non-functional requirements, acceptance status |
| 06 | [System architecture](06_SYSTEM_ARCHITECTURE.md) | Components, run sequence, cost gates, decisions |
| 07 | [Data model](07_DATA_MODEL.md) | Tables, identity and deduplication, change tracking |
| 08 | [Source connector design](08_SOURCE_CONNECTOR_DESIGN.md) | Contract, CPPP, GePNIC, manual import, failure handling |
| 09 | [Document processing](09_DOCUMENT_PROCESSING.md) | Extraction, safety, sections, bounded excerpts, eligibility patterns |
| 10 | [AI matching and scoring](10_AI_MATCHING_AND_SCORING.md) | Rules, semantics, triage, deep analysis, eligibility, scoring, priorities |
| 11 | [Token cost optimisation](11_TOKEN_COST_OPTIMIZATION.md) | Cost levers, benchmark, budget guard, usage accounting |
| 12 | [Reporting and notifications](12_REPORTING_AND_NOTIFICATIONS.md) | Workbook, digest, providers, idempotency, exit codes |
| 13 | [Security and compliance](13_SECURITY_AND_COMPLIANCE.md) | Boundaries, threat model, secrets, data protection |
| 14 | [Tech stack decisions](14_TECH_STACK_DECISIONS.md) | ADRs |
| 15 | [API and CLI specification](15_API_AND_CLI_SPECIFICATION.md) | Commands, config schema, import format, Python contracts |
| 16 | [Implementation roadmap](16_IMPLEMENTATION_ROADMAP.md) | Status and phases |
| 17 | [Test strategy](17_TEST_STRATEGY.md) | Suite, fixtures, validation, gaps |
| 18 | [Deployment and CI/CD](18_DEPLOYMENT_AND_CICD.md) | Local, Docker, Azure, CI, runbook |
| 19 | [Cost estimates](19_COST_ESTIMATES.md) | Measured per-call costs, monthly projections, Ollama plans |
| 20 | [Risks and open questions](20_RISKS_AND_OPEN_QUESTIONS.md) | Verified vs unverified, risk register, questions for Wayam |
| 21 | [Desk UI and API plan](21_DESK_UI_AND_API.md) | Screens, icon map, current HTTP API, watch/analyse routes, build order |
| — | [Build master prompt](CHANAKYA_BUILD_MASTER_PROMPT.md) | Prompt for a coding agent to maintain or extend Chanakya |
| — | [validation/BENCHMARK.md](validation/BENCHMARK.md) · [validation/LIVE_SNAPSHOT_2026-10-10.md](validation/LIVE_SNAPSHOT_2026-10-10.md) | Benchmark and live-notice validation |
