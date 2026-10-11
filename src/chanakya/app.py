"""Wiring: builds the database session, HTTP client, LLM provider and pipeline
from settings, and runs the end-to-end flow used by the CLI and scheduler."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .config import Settings
from .connectors import NETWORK_TYPES
from .db import ScanRun, init_db, make_engine, session_factory
from .desklock import append_event, database_dir
from .http import PoliteClient
from .llm import build_provider
from .llm.base import LLMProvider
from .obs import emits_run_finished
from .pipeline.runner import Pipeline, RunStats
from .reporting.email import EmailError, send_digest, send_p1_alert, weekly_digest_due
from .reporting.excel import export_workbook
from .reporting.followups import send_corrigendum_alert, send_deadline_alert

log =logging.getLogger("chanakya")


@dataclass
class RunResult:
    run: ScanRun
    stats: RunStats
    report_path: Path | None = None
    report_id: int | None = None
    email_status: list[tuple[str, str]] | None = None
    email_error: str | None = None
    alerts_sent: int = 0
    corrigendum_alerts_sent: int = 0
    deadline_alerts_sent: int = 0

    @property
    def exit_code(self) -> int:
        """0 ok · 2 every source failed · 3 partial (some sources or email failed) · 1 run error."""
        if self.run.status == "failed" and self.run.error:
            return 1
        if self.run.status == "failed":
            return 2
        if self.run.status == "partial" or self.email_error or any(s != "sent" and s != "dry_run" for _, s in (self.email_status or [])):
            return 3
        return 0


@contextmanager
def open_session(settings: Settings):
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    sf = session_factory(engine)
    session = sf()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_http_client(settings: Settings) -> PoliteClient:
    return PoliteClient(settings.http)


def needs_network(settings: Settings, source_ids: list[str] | None) -> bool:
    return any(s.enabled and s.type in NETWORK_TYPES and (not source_ids or s.id in source_ids) for s in settings.sources)


@emits_run_finished  # every run ends with exactly one structured run_finished log record (obs.py)
def run_pipeline(settings: Settings, session: Session, *, source_ids: list[str] | None = None,
                 since: datetime | None = None, max_pages: int | None = None, export: bool = True,
                 email: bool = False, dry_run: bool = False, trigger: str = "manual",
                 provider: LLMProvider | None = None, http_client: PoliteClient | None = None,
                 assess_scope: str = "changed", skip_scan: bool = False, on_event=None,
                 should_stop=None, only_ids: list[str] | None = None,
                 skip_assess: bool = False) -> RunResult:
    owns_client = False
    if http_client is None and not skip_scan and needs_network(settings, source_ids):
        http_client, owns_client = make_http_client(settings), True
    if provider is None and not skip_assess:  # a scan-only run makes no model call
        provider = build_provider(settings)
    pipe = Pipeline(settings, session, provider=provider, http_client=http_client)
    pipe.should_stop = should_stop
    run = pipe.start_run(trigger=trigger, dry_run=dry_run)
    stats = RunStats()
    result = RunResult(run, stats)

    def listener(event: dict) -> None:
        stamped = append_event(database_dir(settings), run.id, event)
        if on_event is not None:
            on_event(stamped)

    pipe.on_event = listener
    stopped = False
    try:
        if not skip_scan:
            stopped = bool(pipe.scan(run, stats, source_ids=source_ids, since=since, max_pages=max_pages))
        if stopped:
            pipe.finish_run(
                run, stats,
                error="stopped before the list was finished, so stored notices could be assessed",
                status="partial",
            )
        elif skip_assess:  # `chanakya scan`: acquire only
            pipe.finish_run(run, stats)
        else:
            pipe.assess(run, stats, scope=assess_scope, only_ids=only_ids)
            pipe.finish_run(run, stats)
    except Exception as exc:  # noqa: BLE001 - record and surface any run-level failure
        session.rollback()
        log.exception("run failed")
        pipe.finish_run(run, stats, error=f"{type(exc).__name__}: {exc}")
        return result
    finally:
        if owns_client and http_client is not None:
            http_client.close()
    if export and not stopped:
        result.report_path, result.report_id = export_workbook(session, settings, pipe.kb, run)
    if email or (dry_run and settings.email.to):
        from .db import ExportedReport
        report = session.get(ExportedReport, result.report_id) if result.report_id else None
        preview = dry_run or not email
        weekly = settings.email.digest == "weekly"
        try:
            # The weekly digest goes out once per week; other runs that week stay quiet.
            if not weekly or preview or weekly_digest_due(session, settings):
                rows = send_digest(session, settings, run, report, dry_run=preview,
                                   kind="weekly_digest" if weekly else "daily_digest")
                result.email_status = [(r.recipient, r.status) for r in rows]
            if settings.email.alert_on_new_p1:
                alerts = send_p1_alert(session, settings, run, dry_run=preview)
                result.alerts_sent = len({r.idempotency_key for r in alerts if r.status in ("sent", "dry_run")})
                failed = [(r.recipient, r.status) for r in alerts if r.status not in ("sent", "dry_run")]
                if failed:
                    result.email_status = (result.email_status or []) + failed[:1]
            # Follow-up alerts for notices BD is working on: same preview rule as the P1 alert.
            for flag, send, counter in ((settings.email.alert_on_corrigendum, send_corrigendum_alert, "corrigendum_alerts_sent"),
                                        (settings.email.alert_on_deadline, send_deadline_alert, "deadline_alerts_sent")):
                if not flag:
                    continue
                alerts = send(session, settings, run, dry_run=preview)
                setattr(result, counter, len({r.idempotency_key for r in alerts if r.status in ("sent", "dry_run")}))
                failed = [(r.recipient, r.status) for r in alerts if r.status not in ("sent", "dry_run")]
                if failed:
                    result.email_status = (result.email_status or []) + failed[:1]
        except EmailError as exc:
            result.email_error = str(exc)
    return result
