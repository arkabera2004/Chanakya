"""Chanakya command-line interface.

Exit codes: 0 success · 1 run/config error · 2 every source failed ·
3 partial (a source, the LLM budget or email delivery needs attention).
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional

import typer
from sqlalchemy import func, select

from . import __version__
from .app import open_session, run_pipeline
from .awards_cli import awards_app
from .config import Settings, load_settings
from .dbcli import db_app
from .desklock import LockHeld, acquire, lock_path, release, stop_requested, writer_lock
from .obs import configure_logging
from .timeutil import format_ist, now_ist, now_utc, parse_portal_datetime

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Chanakya — government opportunity intelligence for Wayam AI")
sources_app = typer.Typer(help="Inspect and test procurement sources")
llm_app = typer.Typer(help="Check the configured Ollama provider")
cap_app = typer.Typer(help="Capability knowledge base")
app.add_typer(sources_app, name="sources")
app.add_typer(llm_app, name="llm")
app.add_typer(cap_app, name="capabilities")
app.add_typer(awards_app, name="awards")
app.add_typer(db_app, name="db")

_state: dict = {}


def settings() -> Settings:
    if "settings" not in _state:
        try:
            _state["settings"] = load_settings(_state.get("config"))
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"Configuration error: {exc}", fg="red", err=True)
            raise typer.Exit(1) from exc
        configure_logging(_state["settings"], verbose=bool(_state.get("verbose")))
    return _state["settings"]


@app.callback()
def main(config: Annotated[Optional[Path], typer.Option("--config", "-c", help="Path to chanakya.yaml")] = None,
         verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    # Reports contain characters a default Windows console code page cannot encode.
    # Piped or redirected output must not crash on them.
    for stream in (sys.stdout, sys.stderr):
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    _state["config"] = config
    _state.pop("settings", None)  # a new invocation must not reuse another config's settings
    _state["verbose"] = verbose
    # Format comes from CHANAKYA_LOG_FORMAT here; settings() applies app.log_format once the config loads.
    configure_logging(None, verbose=verbose)


def _since(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = parse_portal_datetime(value)
    if dt is None:
        typer.secho(f"Cannot parse --since {value!r} (use YYYY-MM-DD)", fg="red", err=True)
        raise typer.Exit(1)
    return dt


def _print_run(result) -> None:
    s = result.stats
    r = result.run
    typer.secho(f"\nRun {r.id} — {r.status.upper()}", bold=True,
                fg={"ok": "green", "partial": "yellow"}.get(r.status, "red"))
    for sid, info in s.sources.items():
        line = f"  {sid:<22} {info.get('status', '?'):<8} pages={info.get('pages', 0)} seen={info.get('seen', 0)} " \
               f"new={info.get('new', 0)} updated={info.get('updated', 0)} dup={info.get('duplicate', 0)}"
        typer.echo(line + (f"  ERROR: {info['error']}" if info.get("error") else ""))
    typer.echo(f"  assessed={s.assessed} rejected={s.rejected} low={s.low} candidate={s.candidates} strong={s.strong} "
               f"expired_marked={s.expired_marked}")
    typer.echo(f"  AI[{s.llm_mode}]: triage={s.triage_calls} deep={s.deep_calls} cached={s.llm_cached} "
               f"budget_skipped={s.llm_skipped_budget} failed={s.llm_failed} deferred={s.deep_deferred} "
               f"cost=${s.llm_cost_usd:.4f}")
    for n in s.coverage_notes:
        typer.secho(f"  ! {n}", fg="yellow")
    for b in s.budget_blocked_reasons:
        typer.secho(f"  ! budget: {b}", fg="yellow")
    if r.error:
        typer.secho(f"  ERROR: {r.error}", fg="red")
    if result.report_path:
        typer.echo(f"  report: {result.report_path}")
    if getattr(result, "alerts_sent", 0):
        typer.echo(f"  P1 alert: {result.alerts_sent} notice deliveries")
    if getattr(result, "corrigendum_alerts_sent", 0):
        typer.echo(f"  corrigendum alert: {result.corrigendum_alerts_sent} change deliveries")
    if getattr(result, "deadline_alerts_sent", 0):
        typer.echo(f"  deadline alert: {result.deadline_alerts_sent} reminder deliveries")
    if result.email_status:
        for rcpt, st in result.email_status:
            typer.echo(f"  email {rcpt}: {st}")
    if result.email_error:
        typer.secho(f"  email error: {result.email_error}", fg="red")


# ---------------------------------------------------------------- setup
@app.command()
def init(force: Annotated[bool, typer.Option(help="Overwrite config/chanakya.yaml")] = False) -> None:
    """Create config/chanakya.yaml from the example, data folders and the database."""
    root = Path.cwd()
    example, target = root / "config/chanakya.example.yaml", root / "config/chanakya.yaml"
    if example.exists() and (force or not target.exists()):
        shutil.copy(example, target)
        typer.echo(f"wrote {target}")
    st = load_settings(target if target.exists() else None)
    for d in (st.app.data_dir, st.app.reports_dir, st.app.inbox_dir / "documents", st.app.documents_dir, st.email.outbox_dir):
        d.mkdir(parents=True, exist_ok=True)
    with open_session(st):
        pass
    from .pgsupport import safe_url
    typer.echo(f"database ready: {safe_url(st.app.database_url)}")  # a PostgreSQL URL can carry a password


@app.command()
def version() -> None:
    typer.echo(__version__)


# ---------------------------------------------------------------- sources
@sources_app.command("list")
def sources_list() -> None:
    st = settings()
    from .db import Source
    with open_session(st) as s:
        db = {x.id: x for x in s.scalars(select(Source))}
        typer.echo(f"{'ID':<22}{'TYPE':<9}{'ON':<4}{'VERIFICATION':<22}{'HEALTH':<11}LAST SUCCESS (IST)")
        for cfg in st.sources:
            row = db.get(cfg.id)
            typer.echo(f"{cfg.id:<22}{cfg.type:<9}{'yes' if cfg.enabled else 'no':<4}{cfg.verification_status:<22}"
                       f"{(row.health if row else 'unknown'):<11}{format_ist(row.last_success_at) if row and row.last_success_at else '-'}")


@sources_app.command("test")
def sources_test(source: Annotated[Optional[list[str]], typer.Option("--source", "-s")] = None) -> None:
    """Fetch and parse the first listing page of each source (no database writes)."""
    st = settings()
    from .app import make_http_client
    from .connectors import NETWORK_TYPES, build_connector
    client = make_http_client(st)
    failures = 0
    try:
        for cfg in st.sources:
            if source and cfg.id not in source:
                continue
            conn = build_connector(cfg, client if cfg.type in NETWORK_TYPES else None)
            h = conn.health_check()
            failures += 0 if h.ok else 1
            typer.secho(f"{cfg.id:<22} {'OK ' if h.ok else 'FAIL'} {h.detail} ({h.sample_count} records, {h.latency_ms} ms)",
                        fg="green" if h.ok else "red")
    finally:
        client.close()
    raise typer.Exit(2 if failures else 0)


# ---------------------------------------------------------------- pipeline
@contextmanager
def _writer(st: Settings, mode: str):
    """One writer per database: refuse to start while a watch or another command holds it."""
    try:
        with writer_lock(st, mode):
            yield
    except LockHeld as exc:
        typer.secho(f"{mode}: another writer holds the database (pid {exc.pid}, {exc.mode}). "
                    "Stop it first, or wait for it to finish.", fg="red", err=True)
        raise typer.Exit(1) from exc


@app.command()
def scan(source: Annotated[Optional[list[str]], typer.Option("--source", "-s")] = None,
         since: Annotated[Optional[str], typer.Option(help="Re-scan notices published since YYYY-MM-DD")] = None,
         max_pages: Annotated[Optional[int], typer.Option()] = None) -> None:
    """Acquire new/changed notices only (no analysis, no export)."""
    st = settings()
    with _writer(st, "scan"), open_session(st) as s:
        # Same entry point as every other run, so a scan also ends with one run_finished record.
        res = run_pipeline(st, s, source_ids=source, since=_since(since), max_pages=max_pages,
                           skip_assess=True, export=False, trigger="scan")
        _print_run(res)
        raise typer.Exit(res.exit_code)


@app.command()
def analyze(scope: Annotated[str, typer.Option(help="changed | all")] = "changed",
            new_only: Annotated[bool, typer.Option("--new-only", help="Alias for --scope changed")] = False,
            llm_mode: Annotated[Optional[str], typer.Option(help="Override llm.mode: cloud|local|disabled|fake")] = None) -> None:
    """Classify, analyse and score stored opportunities."""
    st = settings()
    if llm_mode:
        st.llm.mode = llm_mode  # type: ignore[assignment]
    with _writer(st, "analyze"), open_session(st) as s:
        res = run_pipeline(st, s, skip_scan=True, export=False, assess_scope="changed" if new_only else scope, trigger="analyze")
        _print_run(res)
        raise typer.Exit(res.exit_code)


@app.command()
def run(source: Annotated[Optional[list[str]], typer.Option("--source", "-s")] = None,
        since: Annotated[Optional[str], typer.Option()] = None,
        max_pages: Annotated[Optional[int], typer.Option()] = None,
        export: Annotated[bool, typer.Option("--export/--no-export")] = True,
        email: Annotated[bool, typer.Option("--email/--no-email")] = False,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="Never send email; render digest to outbox/dry_run")] = False,
        llm_mode: Annotated[Optional[str], typer.Option(help="Override llm.mode: cloud|local|disabled|fake")] = None,
        trigger: Annotated[str, typer.Option(hidden=True)] = "manual") -> None:
    """Complete pipeline: scan → analyse → score → Excel → (email)."""
    st = settings()
    if llm_mode:
        st.llm.mode = llm_mode  # type: ignore[assignment]
    with _writer(st, "scan"), open_session(st) as s:
        res = run_pipeline(st, s, source_ids=source, since=_since(since), max_pages=max_pages, export=export,
                           email=email and not dry_run, dry_run=dry_run, trigger=trigger)
        _print_run(res)
        raise typer.Exit(res.exit_code)


@app.command()
def export(fmt: Annotated[str, typer.Option("--format")] = "xlsx") -> None:
    """Export the current opportunity book to Excel."""
    if fmt != "xlsx":
        typer.secho("only --format xlsx is supported", fg="red")
        raise typer.Exit(1)
    st = settings()
    from .db import ScanRun
    from .pipeline.knowledge import load_knowledge_base
    from .reporting.excel import export_workbook
    with open_session(st) as s:
        run_ = s.scalar(select(ScanRun).order_by(ScanRun.started_at.desc()))
        path, _ = export_workbook(s, st, load_knowledge_base(st.app.capabilities_file), run_)
        typer.echo(str(path))


@app.command()
def report(latest: Annotated[bool, typer.Option("--latest")] = True,
           limit: Annotated[int, typer.Option()] = 15) -> None:
    """Print the top opportunities to the console."""
    st = settings()
    from .reporting.excel import select_opportunities
    with open_session(st) as s:
        opps = [o for o in select_opportunities(s, active_only=True) if o.priority in ("P1", "P2", "P3")][:limit]
        if not opps:
            typer.echo("No relevant active opportunities stored yet. Run `chanakya run`.")
            return
        for o in opps:
            intel = o.intelligence or {}
            typer.secho(f"[{o.priority} {o.relevance_score:5.1f}] {o.title[:110]}", bold=True)
            typer.echo(f"    {o.organisation or '-'} | {o.source_tender_id or o.ref_no or o.id} | closes {format_ist(o.closing_at) or 'n/a'}")
            typer.echo(f"    fit: {intel.get('relevant_solution') or '-'} | eligibility: {o.eligibility_status}")
            for f in intel.get("flags") or []:
                typer.secho(f"    ! {f}", fg="yellow")


@app.command()
def email(latest: Annotated[bool, typer.Option("--latest")] = True,
          dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
          kind: Annotated[str, typer.Option(help="digest | weekly | alert | corrigendum | deadline")] = "digest") -> None:
    """Send (or dry-run) the digest for the latest run and report, the weekly digest, or one of the alerts."""
    if kind not in ("digest", "weekly", "alert", "corrigendum", "deadline"):
        typer.secho("--kind must be digest, weekly, alert, corrigendum or deadline", fg="red", err=True)
        raise typer.Exit(1)
    st = settings()
    from .db import ScanRun
    from .reporting.email import EmailError, send_digest, send_p1_alert
    from .reporting.excel import latest_report
    with open_session(st) as s:
        run_ = s.scalar(select(ScanRun).where(ScanRun.status.in_(["ok", "partial"])).order_by(ScanRun.started_at.desc()))
        rep = latest_report(s)
        try:
            if kind == "alert":
                rows = send_p1_alert(s, st, run_, dry_run=dry_run)
                if not rows:
                    typer.echo("no P1 notice is waiting for an alert")
                    raise typer.Exit(0)
            elif kind in ("corrigendum", "deadline"):
                from .reporting.followups import send_corrigendum_alert, send_deadline_alert
                rows = (send_corrigendum_alert if kind == "corrigendum" else send_deadline_alert)(s, st, run_, dry_run=dry_run)
                if not rows:
                    typer.echo("no change to a tracked notice is waiting for an alert" if kind == "corrigendum"
                               else "no tracked notice is due a deadline reminder")
                    raise typer.Exit(0)
            else:
                rows = send_digest(s, st, run_, rep, dry_run=dry_run,
                                   kind="weekly_digest" if kind == "weekly" else "daily_digest")
        except EmailError as exc:
            typer.secho(f"email error: {exc}", fg="red")
            raise typer.Exit(3) from exc
        bad = 0
        # An alert has one delivery row per notice; report each recipient once.
        for r in {row.recipient: row for row in rows}.values():
            typer.echo(f"{r.recipient}: {r.status}" + (f" ({r.last_error})" if r.last_error else ""))
            bad += r.status not in ("sent", "dry_run")
        raise typer.Exit(3 if bad else 0)


@app.command()
def status() -> None:
    """Show recent runs, source health and the opportunity book."""
    st = settings()
    from .db import Opportunity, ScanRun, Source
    with open_session(st) as s:
        typer.secho("Recent runs", bold=True)
        for r in s.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(5)):
            typer.echo(f"  {r.id}  {r.status:<8} {format_ist(r.started_at)}  new={r.stats.get('new', 0)} "
                       f"cost=${r.stats.get('llm_cost_usd', 0):.4f}")
        typer.secho("Sources", bold=True)
        for src in s.scalars(select(Source)):
            typer.echo(f"  {src.id:<22} {src.health:<11} failures={src.consecutive_failures} "
                       f"hwm={format_ist(src.high_water_mark) or '-'} {('ERROR: ' + src.last_error[:80]) if src.last_error else ''}")
        typer.secho("Opportunities", bold=True)
        for prio, n in s.execute(select(Opportunity.priority, func.count()).where(Opportunity.status == "active")
                                 .group_by(Opportunity.priority).order_by(Opportunity.priority.asc().nulls_first())):
            typer.echo(f"  {prio or 'unassessed'}: {n}")


@app.command()
def costs(last_days: Annotated[int, typer.Option("--last-days")] = 30) -> None:
    """LLM usage and estimated cost over a period."""
    st = settings()
    from .reporting.insights import usage_by_model_purpose, usage_total
    since = now_utc() - timedelta(days=last_days)
    with open_session(st) as s:
        lines = usage_by_model_purpose(s, since)  # the desk Costs page reads the same lines
        typer.echo(f"{'MODEL':<24}{'PURPOSE':<10}{'CALLS':>7}{'CACHED':>8}{'BLOCKED':>9}{'IN TOK':>10}{'OUT TOK':>10}{'COST USD':>11}")
        for m, p, u in lines:
            typer.echo(f"{m:<24}{p:<10}{u.calls:>7}{u.cache_hits:>8}{u.blocked:>9}{u.input_tokens:>10}{u.output_tokens:>10}{u.cost_usd:>11.4f}")
        typer.echo(f"Total last {last_days} days: ${usage_total(lines).cost_usd:.4f}  (budget/day ${st.budget.max_cost_per_day_usd})")


@app.command("import")
def import_file(path: Path,
                tender_id: Annotated[Optional[str], typer.Option(help="Attach documents to this Tender ID instead")] = None) -> None:
    """Import a CSV/XLSX/YAML of notices into the manual inbox, or attach documents to a Tender ID."""
    st = settings()
    from .security import safe_filename
    if tender_id:
        dest = st.app.inbox_dir / "documents" / safe_filename(tender_id)
        dest.mkdir(parents=True, exist_ok=True)
        files = [path] if path.is_file() else [p for p in path.iterdir() if p.is_file()]
        for f in files:
            shutil.copy(f, dest / safe_filename(f.name))
        typer.echo(f"attached {len(files)} document(s) to {tender_id}; run `chanakya analyze` to process them")
        return
    manual = next((s_ for s_ in st.sources if s_.type == "manual"), None)
    if manual is None or manual.path is None:
        typer.secho("no 'manual' source configured", fg="red")
        raise typer.Exit(1)
    manual.path.mkdir(parents=True, exist_ok=True)
    shutil.copy(path, manual.path / safe_filename(path.name))
    typer.echo(f"queued {path.name} in {manual.path}; run `chanakya run --source {manual.id}`")


@app.command()
def review(opportunity_id: str,
           set_status: Annotated[Optional[str], typer.Option("--status", help="New|Reviewing|Shortlisted|Qualified|Partner needed|Pursuing|Rejected|Expired|Won|Lost")] = None,
           label: Annotated[Optional[str], typer.Option(help="relevant | irrelevant (feeds calibration)")] = None,
           note: Annotated[Optional[str], typer.Option()] = None,
           reviewer: Annotated[Optional[str], typer.Option()] = None) -> None:
    """Record a business-development decision or relevance label."""
    st = settings()
    from .db import Opportunity, UserReviewFeedback
    from .models import ReviewStatus
    with open_session(st) as s:
        o = s.get(Opportunity, opportunity_id)
        if o is None:
            typer.secho("unknown opportunity id", fg="red")
            raise typer.Exit(1)
        if set_status:
            o.review_status = ReviewStatus(set_status).value
        if label and label not in ("relevant", "irrelevant"):
            raise typer.BadParameter("label must be relevant or irrelevant")
        s.add(UserReviewFeedback(opportunity_id=o.id, review_status=set_status, relevance_label=label, note=note,
                                 reviewer=reviewer))
        s.commit()
        typer.echo(f"{o.id}: {o.review_status}")


@app.command()
def benchmark(dataset: Annotated[Path, typer.Option()] = Path("tests/fixtures/benchmark/labeled_tenders.yaml"),
              llm_accuracy: Annotated[float, typer.Option(help="Assumed accuracy of the simulated LLM")] = 0.9,
              out: Annotated[Optional[Path], typer.Option(help="Write markdown results here")] = None) -> None:
    """Compare LLM-everything vs keyword+LLM vs the Chanakya funnel on a labelled set."""
    st = settings()
    from .benchmark import format_markdown, run_benchmark
    res = run_benchmark(st, dataset, llm_accuracy=llm_accuracy)
    md = format_markdown(res)
    typer.echo(md)
    if out:
        out.write_text(md, encoding="utf-8")


def _report_target(st: Settings, path: Path) -> Path:
    """A report or proposal file must never land on the live config, the KB or an existing config file."""
    target = path.resolve()
    protected = {p.resolve() for p in (st.config_path, st.app.capabilities_file) if p}
    config_dir = st.config_path.resolve().parent if st.config_path else None
    if target in protected or (config_dir is not None and target.parent == config_dir and target.exists()):
        typer.secho(f"refusing to write to {path}: calibration output never overwrites configuration. "
                    "Choose another file and copy the weights across by hand.", fg="red", err=True)
        raise typer.Exit(1)
    return target


@app.command()
def calibrate(out: Annotated[Optional[Path], typer.Option(help="Write the markdown report here")] = None,
              write_proposal: Annotated[Optional[Path], typer.Option(
                  "--write-proposal", help="Write a YAML fragment with the proposed scoring.weights here")] = None,
              min_labels: Annotated[Optional[int], typer.Option(
                  help="Override calibration.min_labels for this run")] = None) -> None:
    """Propose scoring weights from BD review labels. A proposal only: no config or score is changed."""
    st = settings()
    if min_labels is not None and min_labels < 2:
        typer.secho("--min-labels must be at least 2", fg="red", err=True)
        raise typer.Exit(1)
    targets = {name: _report_target(st, p) for name, p in (("out", out), ("proposal", write_proposal)) if p}
    from .pipeline.calibrate import format_report, proposal_yaml, run_calibration
    with open_session(st) as s:
        rep = run_calibration(s, st, min_labels=min_labels)
    typer.echo(format_report(rep))
    if "out" in targets:
        targets["out"].write_text(format_report(rep), encoding="utf-8")
        typer.echo(f"report written to {targets['out']}")
    if "proposal" in targets:
        fragment = proposal_yaml(rep)
        if fragment is None:
            typer.echo("no proposal file written: there is no proposal (see the report above)")
        else:
            targets["proposal"].write_text(fragment, encoding="utf-8")
            typer.echo(f"proposal written to {targets['proposal']} (not applied; a human must copy it into the config)")


@app.command()
def winloss(out: Annotated[Optional[Path], typer.Option(help="Write the markdown summary here")] = None,
            limit: Annotated[int, typer.Option(help="Rows per table")] = 20) -> None:
    """Pursued, won and lost notices by buyer, capability and platform (read-only)."""
    st = settings()
    target = _report_target(st, out) if out else None
    from .pipeline.winloss import format_winloss, winloss_summary
    with open_session(st) as s:
        md = format_winloss(winloss_summary(s), limit=limit)
    typer.echo(md)
    if target:
        target.write_text(md, encoding="utf-8")


@app.command()
def schedule(daily_at: Annotated[Optional[str], typer.Option(help="HH:MM IST; default from config")] = None,
             email: Annotated[bool, typer.Option("--email/--no-email")] = True,
             once: Annotated[bool, typer.Option(help="Run once at the next slot then exit")] = False) -> None:
    """Simple in-process daily scheduler (IST). Prefer cron/systemd/Azure Container Apps jobs in production."""
    st = settings()
    at = daily_at or st.schedule.daily_at
    hh, mm = (int(x) for x in at.split(":"))
    while True:
        now = now_ist()
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        while st.schedule.weekdays_only and nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        typer.echo(f"next run at {nxt:%Y-%m-%d %H:%M} IST")
        time.sleep(max(1, (nxt - now_ist()).total_seconds()))
        try:
            with writer_lock(st, "scan"), open_session(st) as s:
                res = run_pipeline(st, s, email=email, trigger="schedule")
                _print_run(res)
        except LockHeld as exc:
            # A watch already keeps the book current; the daily slot is skipped, not queued.
            typer.echo(f"schedule: skipped, another writer holds the database (pid {exc.pid}, {exc.mode})")
            if once:
                raise typer.Exit(1) from exc
            continue
        if once:
            raise typer.Exit(res.exit_code)


@app.command()
def watch(pause_s: Annotated[int, typer.Option(help="Seconds to wait after a pass that covered every source")] = 600,
          retry_s: Annotated[int, typer.Option(help="Seconds to wait after a truncated pass or an error")] = 30,
          email: Annotated[bool, typer.Option("--email/--no-email")] = False) -> None:
    """Keep scanning every enabled public source. A failed site, a page cap, or the end of a pass does not stop the loop.

    Listings only. Search forms and document downloads that ask for a CAPTCHA are not submitted.
    """
    st = settings()
    path = lock_path(st)
    started_at = now_utc().isoformat()
    if not started_at.endswith("Z"):
        started_at += "Z"
    try:
        acquire(path, {
            "pid": os.getpid(), "desk": "live", "mode": "watch",
            "started_at": started_at, "pause_s": pause_s, "retry_s": retry_s,
        })
    except LockHeld as exc:
        print(f"watch: already running (pid {exc.pid})", flush=True)
        raise typer.Exit(1) from exc
    enabled = [s for s in st.sources if s.enabled]
    print(f"watch: {len(enabled)} sources, pause {pause_s}s when covered, {retry_s}s when a window is unfinished", flush=True)

    def narrate(event: dict) -> None:
        message = event.get("message")
        if message:
            print(message, flush=True)

    def wait(seconds: float) -> bool:
        """Sleep, but return True as soon as the desk asks the loop to stop."""
        deadline = time.monotonic() + seconds
        while True:
            if stop_requested(path):
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(1.0, left))

    try:
        while True:
            if stop_requested(path):
                print("watch: stopped", flush=True)
                break
            started = time.monotonic()
            try:
                print(f"watch: pass starting at {now_ist():%Y-%m-%d %H:%M} IST", flush=True)
                with open_session(st) as s:
                    res = run_pipeline(
                        st, s, email=email, trigger="watch", on_event=narrate,
                        should_stop=lambda: stop_requested(path),
                    )
                _print_run(res)
                if stop_requested(path):
                    print("watch: stopped", flush=True)
                    break
                unfinished = any("coverage may be partial" in n for n in res.stats.coverage_notes)
                delay = retry_s if unfinished or res.exit_code == 1 else pause_s
                print(
                    f"watch: pass finished in {time.monotonic() - started:.0f}s "
                    f"(exit {res.exit_code}). Next pass in {delay}s.",
                    flush=True,
                )
            except KeyboardInterrupt:
                print("watch: stopped", flush=True)
                raise
            except Exception as exc:  # noqa: BLE001 — the loop is the product; one pass must not end it
                delay = retry_s
                print(f"watch: pass failed ({type(exc).__name__}: {exc}). Next pass in {delay}s.", flush=True)
            if wait(delay):
                print("watch: stopped", flush=True)
                break
    finally:
        release(path, os.getpid())


@app.command()
def healthcheck(max_age_hours: Annotated[float, typer.Option(help="Fail if the last successful run is older")] = 36) -> None:
    """Container health check: config loads, database reachable, recent successful run."""
    st = settings()
    from .db import ScanRun
    try:
        with open_session(st) as s:
            last = s.scalar(select(ScanRun).where(ScanRun.status.in_(["ok", "partial"])).order_by(ScanRun.started_at.desc()))
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"unhealthy: database error {type(exc).__name__}")
        raise typer.Exit(1) from exc
    if last is None:
        typer.echo("healthy: no runs yet")
        return
    age = (now_utc() - last.started_at).total_seconds() / 3600
    if age > max_age_hours:
        typer.echo(f"unhealthy: last successful run {age:.1f} h ago")
        raise typer.Exit(1)
    typer.echo(f"healthy: last run {last.id} ({last.status}) {age:.1f} h ago")


@app.command()
def metrics(fmt: Annotated[str, typer.Option("--format", help="prometheus | json")] = "prometheus",
            out: Annotated[Optional[Path], typer.Option(help="Write here atomically (node-exporter textfile collector)")] = None) -> None:
    """Latest run, source health and last-24-hours totals for a platform monitor (read-only)."""
    if fmt not in ("prometheus", "json"):
        typer.secho("--format must be prometheus or json", fg="red", err=True)
        raise typer.Exit(1)
    st = settings()
    from .obs import metrics_text, write_metrics_file
    with open_session(st) as s:
        text = metrics_text(s, fmt)
    if out:
        write_metrics_file(out, text)
    else:
        typer.echo(text, nl=False)


# ---------------------------------------------------------------- llm / kb
@llm_app.command("check")
def llm_check() -> None:
    """Verify credentials and list models visible to the configured provider."""
    st = settings()
    from .llm import build_provider
    from .llm.base import LLMError
    try:
        p = build_provider(st)
        if p is None:
            typer.echo("llm.mode is 'disabled' — no provider configured")
            return
        models = p.list_models()
    except LLMError as exc:
        typer.secho(f"LLM check failed: {exc}", fg="red")
        raise typer.Exit(1) from exc
    typer.echo(f"mode={st.llm.mode}; {len(models)} models visible")
    for name in (st.llm.fast_model, st.llm.deep_model):
        typer.secho(f"  {name}: {'available' if name in models else 'NOT LISTED — update config'}",
                    fg="green" if name in models else "red")
    for m in models[:60]:
        typer.echo(f"  - {m}")


@app.command()
def serve(host: Annotated[str, typer.Option()] = "127.0.0.1",
          port: Annotated[int, typer.Option()] = 8787) -> None:
    """Start the desk API. The web console in web/ talks to this process."""
    from .server import serve as run_server
    typer.echo(f"Chanakya desk API on http://{host}:{port}")
    typer.echo("In another terminal: cd web && npm install && npm run dev")
    run_server(host, port)


@cap_app.command("validate")
def cap_validate() -> None:
    st = settings()
    from .pipeline.knowledge import load_knowledge_base
    kb = load_knowledge_base(st.app.capabilities_file)
    typer.echo(f"OK: {len(kb.capabilities)} capabilities, version {kb.version}")
    for c in kb.capabilities:
        typer.echo(f"  {c.id:<20} {c.offering_status:<18} {c.confidence:<7} {c.name}")


def entrypoint() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)

