# 21 — Desk UI and API plan

Written 2026-10-10, after the first live watch and the assessment of the notices it stored.
Built the same day: the lock, watch and analyse routes, lead filters, source cards, and the icon map are in the desk.
The watch stays stopped until someone starts it. This plan does not change the pipeline rules in docs 08, 10 and 13.

The desk is the same pipeline as the CLI, shown while it works. Vira narrates each source and
each keep or set-aside. Practice and Live are two databases, not two products.

## One desk (2026-10-10)

The Practice / Live toggle is gone. The desk reads one config and one database, and every route below is called without a `desk` parameter.
Where this plan says Practice, read: the API started on the synthetic inbox with `CHANAKYA_CONFIG=config/chanakya.demo.yaml chanakya serve`. The desk then shows a "Demo data" chip, calls its button "Run a demo pass", and offers no watch. Where it says Live, read: the desk.

## Closed after the build (2026-10-10)

Four things this plan asked for were missing from the first build and are now in:

- **One writer includes the CLI.** `run`, `scan`, `analyze` and `schedule` take the same lock as `watch`. A second writer exits 1.
- **A dead pass is not a running pass.** A `running` row with no live lock holder is shown as `stopped`. The next writer closes it as `partial`.
- **The budget note reads the right field.** The cap is recorded in `budget_blocked_reasons` and `llm_skipped_budget`, not in the coverage notes. The desk and Passes now show both, with the count still waiting.
- **Passes show the bands.** Each run records P1 to P4 counts for what it assessed. Runs stored before this show no band line. Leads page through the book 200 at a time.

## Costs and Products (2026-10-10)

Two read-only pages, served by `src/chanakya/desk_insights.py`. The sums live in `src/chanakya/reporting/insights.py`, which `chanakya costs` and the workbook's Product-Wise sheet also call, so the desk, the CLI and the Excel file cannot disagree.

| Method | Path | Role |
|---|---|---|
| GET | `/api/costs` | Spend today, last 7 and last 30 IST days, the caps from `budget`, usage by purpose and by model, cache hits, refused calls, notices still waiting, the last finished pass, and 30 daily buckets |
| GET | `/api/products?top=` | Active P1 to P3 notices grouped by the capability that leads each match, in knowledge-base order. `top` (default 5, max 50) trims each list, never the counts |
| GET | `/api/capabilities` | Id, name, offering and offering status of every capability in the knowledge base |

- **Costs (`/costs`).** A day runs from 00:00 IST, the same window the budget guard uses. A call is a request that reached a model. A cache hit and a call the budget refused are counted on their own. Money is shown with enough places that a real cost never reads as zero. The chart is an inline SVG in `web/src/components/spend-chart.tsx`: one series in the accent token, a value on hover, touch or arrow keys, and the same days as a table.
- **Products (`/products`).** One section per capability, titled with its offering. A notice is listed once, under its strongest match, as in the sheet. A section with nothing says so. The offering picker is remembered in the browser (`chanakya-products-view`), which is the saved view for a product owner until the desk has accounts.
- **Leads.** The capability filter lists names from `/api/capabilities` and still sends the capability id. `/leads?capability=CAP-...` opens the book with that filter set.

## Documents and the company profile (2026-10-10)

Two write screens. Deep analysis needs the tender documents, and the portals keep those behind a CAPTCHA that is never solved. Eligibility needs Wayam's own figures, and those are never guessed. Both now have a place on the desk.

| Method | Path | Role |
|---|---|---|
| GET | `/api/opportunities/{id}/documents` | The documents a pass would find for this notice, in the order it reads them, with the limits |
| POST | `/api/opportunities/{id}/documents?filename=` | Store one file. The file is the raw request body. 409 while a watch or a pass holds the database |
| DELETE | `/api/opportunities/{id}/documents/{name}?folder=` | Remove a document from this notice's inbox folder. Same lock |
| POST | `/api/opportunities/{id}/analyze` | Start a job that assesses this one notice again. The desk follows it on `GET /api/job` |
| GET | `/api/profile` | `company_profile` as eligibility reads it, and whether a saved overlay exists |
| PUT | `/api/profile` | Save the profile. Every field is optional. null means not stated |

- **Documents (on `/leads/[id]`).** Routes are in `src/chanakya/desk_documents.py`. A file is stored in `<inbox_dir>/documents/<notice id>/`, the tree `chanakya import --tender-id` writes and `documents_for` in `pipeline/runner.py` reads. The desk lists exactly what that function returns, so what a person sees attached is what a pass opens.
- **An upload is untrusted.** The name goes through `safe_filename`. The size is capped at `http.max_download_mb` while the body is read. The type is taken from the bytes with `detect_type`: PDF, DOCX, XLSX and ZIP are kept, anything else is refused with the reason. A ZIP is opened with `safe_extract_zip` in a scratch folder and kept as one document only if it holds something readable. A notice takes `budget.max_documents_per_tender` documents, because a pass reads no more. Nothing is parsed for content at upload: reading is the pass's job.
- **Analyse this notice.** One notice is still a pass: the configured model, the budget guard, the cache and the event file are the same. The run is recorded with trigger `notice`, so Passes shows it as "One notice".
- **Company profile (`/profile`).** Routes are in `src/chanakya/desk_profile.py`. The page shows the eight credentials eligibility can read, each one optional and empty until a person fills it. It says how each is used, including the two that are only kept on record today (MSME and DPIIT).
- **Where the profile is saved.** In `<data_dir>/<config name>.profile.yaml`, for example `data/chanakya.profile.yaml`. `load_settings` lays it over `company_profile` from the YAML on every load, so the CLI, a watch and the desk assess against the same figures. The YAML stays the base and is never rewritten, so its comments survive. The file is named after the config it overlays, so the demo and example configs never pick up real figures.
- **Re-assess the book.** After a save the page offers `POST /api/analyze` with scope `all`. Until then, stored notices keep the eligibility they were given.

## What exists today

| Surface | What it does |
|---|---|
| `web/` | Next.js console. Chronos tokens in `web/src/styles/tokens.css`. Vira orb from the Wayam mark. |
| Pages | Desk `/`, Leads `/leads` and `/leads/[id]`, Passes `/runs`, Sources `/sources` |
| Shell | Practice / Live toggle. Default is Practice, and the choice is forgotten on refresh. |
| Icons | `lucide-react` only. Nav: Radar, Telescope, Activity, Server. Theme: SunMedium, Moon. Size 16, stroke 1.75. |
| API | FastAPI in `src/chanakya/server.py`, started with `chanakya serve` on `127.0.0.1:8787`. The Next app rewrites `/api/*` to that port. |
| Live scan | `chanakya watch` is a CLI loop. It writes `data/chanakya.db`. The desk does not start it, stop it, or show its lines. |

Practice loads `config/chanakya.demo.yaml` and `data/demo/chanakya_demo.db` (one synthetic source named `demo`).
Live loads `config/chanakya.yaml` and `data/chanakya.db` (the public portals). A pass the desk starts
is one in-memory job. A second start returns 409. The CLI watch is a second writer on the same SQLite file.

On 2026-10-10 the watch was stopped mid-list and `chanakya analyze` scored the book already stored:
4,904 notices, 12 P1, 37 P2, 78 P3, 4,777 P4. Those rows are on the Live desk after a reload.
They do not appear while Practice is selected.

## What to make better, in order

1. **One writer.** The desk must know when a watch is running and must refuse a second live pass.
2. **Live is visible.** Sources, counts, and new notices update while a watch or an analysis is in progress. The Practice / Live choice is remembered.
3. **A book a person can work.** Filter and review the scored book. P4 stays out of the default list.
4. **Sources tell the truth.** Health, pages read, why a portal was skipped, and the public-list limit on GePNIC.
5. **Icons that mean one thing.** One library, one size scale, a fixed map from meaning to icon. The priority chip stays a letter, not an icon alone.
6. **The watch belongs to the desk.** Start, stop, and narrate `watch` from the API, with the same events the home page already renders.

Do these in that order. A new visual layer on top of a desk that still opens on the demo source does not help BD.

## Screens

### Desk (`/`)

Keep the four beats: Open sources, Match capabilities, Keep or set aside, Recommend.

| State | What Vira shows |
|---|---|
| Standing by | Greeting from `GET /api/brief`, last pass id and status, counts for this desk. |
| Watch running | The source she is on, pages read, notices stored so far. Orb mood `focused`. |
| Analysis running | “Scoring stored notices.” Counts of P1 to P4 as they commit. |
| Pass finished | Strong / review / set-aside, model calls, cost, and the budget note when the call cap was hit. |
| Writer busy | The scan button is disabled. The copy says a watch already holds the live database. |

The event column keeps the latest 400 lines. Each judged notice links to `/leads/[id]`.
Practice button label stays “Run a practice pass”. Live button label becomes “Scan live portals” for one pass and “Watch live portals” for the loop. They are two actions.

### Leads (`/leads`)

Default list is P1 to P3 and anything still unscored. P4 and rejected rows appear only when “Include what she set aside” is on.

Add filters the API already almost supports, then the ones it does not:

| Filter | Now | Add |
|---|---|---|
| Text search | title, buyer, id | keep |
| Priority | P1 to P4 | keep, default hidden P4 |
| Closing | — | closing within 7 / 14 / 30 days |
| Fit | — | capability id (`CAP-AGENT`, `CAP-CV-VA`, …) |
| Buyer state | — | state |
| Review | — | New, Reviewing, Shortlisted |

The row shows priority chip, title, buyer, fit, close date, score. The open lead keeps the score breakdown, eligibility, flags, evidence line, and official URL. Review actions (Shortlisted, Rejected, relevant / irrelevant) sit on the lead, calling the review endpoint that already exists.

Empty, loading, and error are three different lines. A failed fetch is not an empty book.

### Passes (`/runs`)

One row per `scan_runs` record: time in IST, trigger (`watch`, `analyze`, `desk`, `manual`, `notice`), status, new, assessed, P1-band counts, model cost, coverage notes. A run left `running` because the process was stopped shows as stopped, with the error written when it was closed.

While a pass is `running`, this page polls. It does not wait for the in-memory job to finish.

### Sources (`/sources`)

One card per configured source, from `configured_sources`, joined to the `sources` row.

Show: name, type, enabled, verification, health, consecutive failures, last error, pages on the last pass, high-water mark. For GePNIC, a fixed line: the public home list is about ten notices; the active-tender search asks for a CAPTCHA and is not submitted. For a host whose robots.txt disallows the site, the card says restricted and points at the CPPP state feed or manual import. Maharashtra is the current example.

### Not a new page yet

Nothing is waiting here now. Cost charts are on `/costs`. The document drop is on the lead page and the company profile is on `/profile`: see "Documents and the company profile" above. `chanakya import --tender-id` still works and writes to the same folders.

## Icon system

One library: `lucide-react`. Do not add a second set, and do not use emoji as icons.

| Use | Size | Stroke |
|---|---|---|
| Navigation | 16 | 1.75 |
| Inline, in a chip or a table | 14 | 1.75 |
| Empty state | 20 | 1.5 |

Colour comes from the text token of the control (`text-secondary`, `text-primary`, `text-error`). Do not paint icons in one-off hex values. The only non-token mark is the existing 10px Wayam orange dot in the wordmark, and the Vira orb.

| Meaning | Icon | Where |
|---|---|---|
| Desk | `Radar` | Nav. Already used. |
| Leads | `Telescope` | Nav. Already used. |
| Passes | `Activity` | Nav. Already used. |
| Sources | `Server` | Nav. Already used. |
| Light / dark | `SunMedium` / `Moon` | Header. Already used. |
| Open menu | `Menu` | Header, below the `lg` breakpoint. Replaces the word “Menu”. |
| Practice | `FlaskConical` | Desk toggle, before the word Practice. |
| Live | `Radio` | Desk toggle, before the word Live. |
| Watch running | `Radio` with a pulse | Header status. Same icon as Live, so “live” and “on air” stay one idea. |
| One-shot scan | `Play` | Primary button. |
| Stop | `Square` | Stop the watch. |
| Score stored notices | `ListChecks` | Analyse action. Also "Analyse this notice" on a lead and "Re-assess the book" on the profile: the same act on one notice or on all. |
| CPPP source | `Landmark` | Source card. |
| GePNIC source | `Building2` | Source card. |
| Manual / fixture | `FolderInput` | Source card. |
| Healthy source | `CircleCheck` | Health line. |
| Failing source | `CircleAlert` | Health line. |
| Restricted (robots or CAPTCHA) | `ShieldBan` | Health line. The pass does not bypass it. |
| Closing soon | `CalendarClock` | Lead row, when close is inside 7 days. |
| Official notice | `ExternalLink` | Lead, next to the portal URL. |
| Set aside | `EyeOff` | The include-rejected control. |
| Shortlisted | `Bookmark` | Review action. |
| Model budget hit | `Gauge` | Pass summary. |
| Email settings | `Mail` | Passes, the line that says what an emailed pass sends. |
| Open a dropdown | `ChevronDown` | The custom `Select` (`web/src/components/select.tsx`). `Check` marks the current option. |
| Search the book | `Search` | Leads search field. |
| Clear filters | `X` | Leads filter bar. Also closes Vira's panel, and takes a certificate off the profile. |
| Go back / go on | `ArrowLeft` / `ArrowRight` | Back to leads. All leads. |
| Current decision | `Check` | The review button that is already set. |
| Flag on a lead | `TriangleAlert` | Flags panel. |
| Source not contacted | `CircleDashed` | Health chip before the first pass. |
| Products | `Boxes` | Nav. The book grouped by Wayam offering. |
| Costs | `Coins` | Nav. Model spend against the caps. |
| Company profile | `BadgeCheck` | Nav. Also the verified-figures note at the top of the profile page. |
| A document on a notice | `FileText` | Lead, Documents panel, one per attached file. |
| Add documents | `Upload` | Lead, Documents panel, the drop area. Size 20, stroke 1.5, like an empty state. |
| Remove a document | `Trash2` | Lead, Documents panel, on a file a person added. |

Priority stays the letter chip (`P1`-`P4`) with the existing semantic surfaces in `web/src/components/ui.tsx`. An icon without the letter fails when colour is the only signal.

The Vira orb is the agent. Do not put a face icon next to her, and do not replace the orb with a Lucide sparkle.

Favicon (`web/public/icon.png`) and the sidebar mark (`brand/mark-128.png`) are the Vira reference still, cropped to a dark rounded tile. The share card is `brand/og-card.png`.
Empty states and quiet corners use seven spot illustrations of Vira (`brand/art/*.webp`: leads, passes, sources, costs, products, documents, profile), shown through `Spot` in `web/src/components/vira/art.tsx`. They sit in a dark rounded tile in both themes, like the mark.
All of these were generated in Paper from the prompts in `docs/brand/GOOGLE_FLOW_PROMPTS.md`; the full-size originals are in `docs/brand/flow-stills/`. Functional icons stay Lucide.
The one accent is the token `--accent-vira` (`bg-vira`, `text-vira`). It marks the step Vira is on, score bars, and the current page. It is not a status colour.

## API today

All routes are local. CORS allows `localhost:3000` and `127.0.0.1:3000`. `desk` is `practice` or `live`. Omitted `desk` means practice.

| Method | Path | Role |
|---|---|---|
| GET | `/api/health` | `{status: ok}` |
| GET | `/api/metrics` | Prometheus text: latest run, source health, last-24-hours totals (`?format=json` for JSON). Same output as `chanakya metrics`; rules in doc 18 |
| GET | `/api/brief?desk=` | Counts, last run, top leads, configured sources, source health, Vira greeting |
| GET | `/api/opportunities?desk=&priority=&q=&include_rejected=` | Lead cards. Rejected hidden unless `include_rejected=true` |
| GET | `/api/opportunities/{id}?desk=` | Lead detail, score breakdown, findings, changes |
| POST | `/api/opportunities/{id}/review?desk=` | `{status, label, note, reviewer}` |
| GET | `/api/runs?desk=` | Last 30 runs |
| GET | `/api/job` | The in-memory desk job, or null |
| POST | `/api/runs` | `{desk}` starts one pass. 409 if a job is already running. |
| POST | `/api/vira` | `{desk, message, opportunity_id?}`. Answers from stored rows. Calls the fast model only when the rows do not already answer. |

`GET /api/job` is not per desk. The UI ignores events whose `job.desk` is the other desk.

## API to add

These routes make the watch part of the tool. They call the same `run_pipeline` the CLI uses. They do not grow a second scoring path.

### `GET /api/watch?desk=live`

```json
{
  "desk": "live",
  "running": true,
  "mode": "watch",
  "run_id": "run_20261010T135148_506f",
  "source_id": "cppp_central",
  "source_name": "GeM-CPPP — Central active tenders",
  "pages": 530,
  "notices_stored": 4904,
  "started_at": "2026-10-10T08:21:48Z",
  "pause_s": 600
}
```

`running: false` when no watch holds the live database. Practice always returns `running: false`. The fields come from the open `scan_runs` / `source_scan_runs` rows plus a lock file `data/watch.lock` written by the watch process (pid, desk, mode). The desk reads the lock. It does not guess from a stale `status=running` row.

### `POST /api/watch`

```json
{ "desk": "live", "action": "start" }
```

`action` is `start` or `stop`. Start launches the same loop as `chanakya watch` (pause 600s after a covered pass, 30s after a truncated one, no email). Stop sets a flag the loop checks between sources, then marks the open run `partial` with a clear error, the way a manual stop already does. 409 if a lock exists. 400 if `desk` is `practice` and `action` is `start`: practice stays a single pass from `POST /api/runs`.

### `POST /api/analyze`

```json
{ "desk": "live", "scope": "changed" }
```

Runs `skip_scan` assessment, the same as `chanakya analyze`. 409 if a watch or a job holds the database. `scope` is `changed` or `all`.

### Filters on `GET /api/opportunities`

| Query | Meaning |
|---|---|
| `closing_within_days` | `closing_at` within N days, still active |
| `capability` | match id in the stored capability list |
| `state` | buyer state |
| `review_status` | BD status |
| `limit` | default 200, max 500 |
| `offset` | stable order: priority, then score descending, then closing date |

The response adds `total`, so the UI can say “12 P1 of 4,904” without loading every P4.

### `GET /api/events?desk=&after=`

Poll the narration. `after` is the last event timestamp the UI has. Events are appended to `data/events-<run_id>.jsonl` by the pipeline listener, not only held in the desk process. That is how a watch started from the CLI and a watch started from the API show the same lines. The home page already renders `{at, kind, message, opportunity_id}`.

Keep `GET /api/job` until the event file replaces it. Then the home page reads `/api/events` and `/api/watch`.

## How the live scan sits in the tool

```
Practice toggle ──▶ demo yaml ──▶ demo sqlite ──▶ one synthetic source
Live toggle    ──▶ chanakya.yaml ──▶ data/chanakya.db ──▶ public portals
                                      ▲
                                      │ one writer
                         chanakya watch   or   POST /api/watch
                         chanakya analyze or   POST /api/analyze
```

Until `POST /api/watch` exists, the operator runs `chanakya watch` in a terminal and uses the Live toggle to read the book. They do not press “Scan live portals” during that watch. After the API owns the lock, the terminal and the button are the same process.

Scoring stays a separate step. A watch pass stores notices first and scores them at the end of the pass. Stopping a watch and then running analyse is the supported way to score a partial book. The desk button for that is “Score stored notices”.

## Build order

| Step | Work | Done when |
|---|---|---|
| 1 | Remember `chanakya-desk` in `localStorage`. Poll `GET /api/brief` every 15s on Live while any run is `running`. | Refresh keeps Live. New notices appear without a full reload. |
| 2 | Lock file and `GET /api/watch`. Disable the live scan button when the lock is held. | Two writers cannot start. |
| 3 | `POST /api/analyze` and a Score button on the desk. | The 10 Oct stop-and-score path is a button. |
| 4 | Opportunity filters and `total`. Leads default to P1 to P3. | A BD user can open the 12 P1 rows without scrolling P4. |
| 5 | Source cards: pages, high-water, restricted reason, GePNIC caption. | Maharashtra and a CAPTCHA skip are visible as policy, not as a crash. |
| 6 | Icon map above, applied in the shell, source cards, and lead rows. | One icon per meaning. No new library. |
| 7 | `POST /api/watch` and the event file. Home narration follows a watch the desk started. | Stopping the watch from the header closes the run as partial. |
| 8 | Review actions on the lead page. | A Shortlisted click writes `user_review_feedback`. |

Each step is shippable on its own. Steps 1 to 3 are the integration. Steps 4 to 6 are the book and the icons. Steps 7 to 8 make the desk the owner of the loop.

## Visual rules while building

- Stay on the Chronos tokens already in `web/src/styles/tokens.css`. New colours go in that file as semantic tokens, or they do not go in.
- Type stays Geist for text and Michroma for the wordmark and page titles.
- A screen shows the data, the reason, and the next action. It does not hide the pipeline behind a single score.
- Light and dark both have to be checked when a surface, a chip, or an icon colour changes.
- Desktop and a 390px width both have to be checked when the leads table or the source grid changes.

## Out of scope

- Submitting a CAPTCHA, logging into a portal, or ignoring robots.txt.
- A second scoring implementation in the frontend.
- Automatic bid submission, or writing to officials.
- Replacing SQLite in this plan. Two writers are forbidden. PostgreSQL is still doc 16 phase 4, and it becomes necessary only when the watch and the desk must write at the same time on purpose.
- Painting a new icon set. Lucide plus the orb is the set.

## Acceptance

- With Practice selected, Sources shows the synthetic inbox and Leads never shows a live tender id.
- With Live selected, Sources lists every enabled portal in `config/chanakya.yaml`, and Leads shows the scored book.
- Starting a watch while one is running returns 409 and the button stays disabled.
- Stopping a watch marks the open run `partial` and leaves the stored notices in place.
- “Score stored notices” raises priorities on that book and does not fetch a portal.
- Every new icon is in the table in this document. A meaning with no row does not get an icon until the row is added.
