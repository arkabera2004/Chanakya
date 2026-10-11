"""Email digests with idempotent delivery.

Providers: ``smtp`` (STARTTLS/SSL) and ``file`` (writes .eml files — the test
provider). Microsoft Graph / Gmail API plug in by implementing ``EmailProvider``.
Every (report, recipient, kind) has an idempotency key; a sent message is never
sent twice, and a failed email never re-triggers a scan. Chanakya only emails
the configured internal recipients — it never contacts government officials.
"""

from __future__ import annotations

import hashlib
import logging
import smtplib
import time
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Protocol

from jinja2 import Environment, PackageLoader, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import AIUsageEvent, ChangeEvent, EmailDelivery, ExportedReport, Opportunity, ScanRun
from ..models import TenderStatus
from ..security import is_safe_link, redact
from .text import plain
from ..timeutil import days_remaining, format_ist, ist_date_str, now_ist, now_utc

log = logging.getLogger(__name__)


class EmailError(Exception):
    pass


class EmailAuthError(EmailError):
    pass


class EmailProvider(Protocol):
    def send(self, msg: EmailMessage) -> str: ...


class FileEmailProvider:
    """Writes messages to an outbox directory (test/dry-run provider)."""

    def __init__(self, outbox: Path) -> None:
        self.outbox = outbox
        outbox.mkdir(parents=True, exist_ok=True)

    def send(self, msg: EmailMessage) -> str:
        mid = msg["Message-ID"] or make_msgid()
        name = hashlib.sha256(mid.encode()).hexdigest()[:16] + ".eml"
        (self.outbox / name).write_bytes(bytes(msg))
        return mid


class SMTPEmailProvider:
    def __init__(self, settings: Settings) -> None:
        self.s = settings.email
        self.user = settings.secret(self.s.username_env)
        self.password = settings.secret(self.s.password_env)

    def send(self, msg: EmailMessage) -> str:
        try:
            if self.s.smtp_security == "ssl":
                server = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30)
            with server:
                if self.s.smtp_security == "starttls":
                    server.starttls()
                if self.user and self.password:
                    server.login(self.user, self.password)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailAuthError("SMTP authentication failed") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(redact(f"SMTP error: {exc}", [self.password])) from exc
        return msg["Message-ID"]


def build_provider(settings: Settings) -> EmailProvider:
    if settings.email.provider == "smtp":
        return SMTPEmailProvider(settings)
    return FileEmailProvider(settings.email.outbox_dir)


_env = Environment(loader=PackageLoader("chanakya", "reporting/templates"),
                   autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)


@dataclass
class Digest:
    subject: str
    html: str
    text: str
    item_ids: list[str]


def build_digest(session: Session, settings: Settings, run: ScanRun | None, *, dry_run: bool = False,
                 attached: bool = True, limit: int = 15, weekly: bool = False) -> Digest:
    """The digest for one run, or (``weekly``) for the seven days up to now."""
    es = settings.email
    week_start = now_utc() - timedelta(days=7)
    q = select(Opportunity).where(Opportunity.status == TenderStatus.ACTIVE.value,
                                  Opportunity.relevance_score >= es.min_score,
                                  Opportunity.filter_decision != "rejected")
    opps = list(session.scalars(q).all())
    if es.closing_within_days is not None:
        opps = [o for o in opps if o.closing_at and 0 <= (days_remaining(o.closing_at) or -1) <= es.closing_within_days]
    opps.sort(key=lambda o: (o.priority or "P9", -(o.relevance_score or 0)))
    all_active = list(session.scalars(select(Opportunity).where(Opportunity.status == "active",
                                                                Opportunity.filter_decision != "rejected")).all())
    items = []
    for o in opps[:limit]:
        intel = o.intelligence or {}
        concerns = "; ".join((intel.get("eligibility_blockers") or [])[:2])
        items.append({
            "priority": o.priority, "score": o.relevance_score, "platform": o.platform, "title": o.title,
            "organisation": o.organisation or "Organisation not stated", "department": o.department,
            "tender_id": o.source_tender_id or o.ref_no or o.id, "closing": format_ist(o.closing_at) or "not stated",
            "solution": intel.get("relevant_solution") or "Not recorded", "eligibility": o.eligibility_status,
            "concerns": concerns, "flags": intel.get("flags") or [], "next_action": intel.get("recommended_next_action"),
            "url": o.official_url if is_safe_link(o.official_url, settings.http.allowed_domains) else None,
            "is_new": (o.first_seen_at >= week_start) if weekly else bool(run and o.first_seen_run == run.id),
        })
    items = plain(items)
    stats = (run.stats if run else {}) or {}
    ai_calls = 0
    ai_cost = 0.0
    if run:
        for e in session.scalars(select(AIUsageEvent).where(AIUsageEvent.run_id == run.id)):
            if not e.cache_hit and e.result != "budget_blocked":
                ai_calls += 1
            ai_cost += e.est_cost_usd or 0.0
    counts = {
        "p1": sum(1 for o in all_active if o.priority == "P1"),
        "p2": sum(1 for o in all_active if o.priority == "P2"),
        "closing_soon": sum(1 for o in all_active if o.priority in ("P1", "P2", "P3") and o.closing_at
                            and 0 <= (days_remaining(o.closing_at) or -1) <= settings.scoring.closing_soon_days),
    }
    date = ist_date_str()
    subject = es.subject_template.format(date=date)
    period = "this run"
    new_count, updated_count = stats.get("new", 0), stats.get("updated", 0)
    if weekly:
        subject = "Weekly | " + subject
        period = "in the last 7 days"
        new_count = sum(1 for o in all_active if o.first_seen_at >= week_start)
        updated_count = int(session.scalar(
            select(func.count(func.distinct(ChangeEvent.opportunity_id))).where(ChangeEvent.detected_at >= week_start)
        ) or 0)
    if dry_run:
        subject = "[DRY RUN] " + subject
    ctx = {"subject": subject, "date": date, "items": items, "stats": {"new": new_count, "updated": updated_count}, "period": period,
           "counts": counts, "closing_days": settings.scoring.closing_soon_days, "min_score": es.min_score,
           "coverage_notes": plain(stats.get("coverage_notes", [])[:5]), "dry_run": dry_run, "attached": attached,
           "ai": {"calls": ai_calls, "cost": ai_cost, "mode": stats.get("llm_mode", settings.llm.mode)}}
    html = _env.get_template("digest.html.j2").render(**ctx)
    lines = [subject, "", f"{ctx['stats']['new']} new, {ctx['stats']['updated']} updated; P1 {counts['p1']}, P2 {counts['p2']}, "
             f"closing soon {counts['closing_soon']}", ""]
    for it in items:
        lines += [f"[{it['priority']} | {it['score']}] {it['title']}", f"  {it['organisation']} | Tender ID {it['tender_id']} | closes {it['closing']}",
                  f"  Fit: {it['solution']} | Eligibility: {it['eligibility']}", f"  Next: {it['next_action']}",
                  f"  {it['url'] or ''}", ""]
    if not items:
        lines.append(f"No opportunities met the threshold (score >= {es.min_score}).")
    lines.append("Scores are decision support; eligibility is not assumed. Verify on the official portal.")
    return Digest(subject, html, "\n".join(lines), [o.id for o in opps[:limit]])


def idempotency_key(kind: str, report_id: int | None, run_id: str | None, recipient: str) -> str:
    return hashlib.sha256(f"{kind}|{report_id}|{run_id}|{recipient.lower()}".encode()).hexdigest()


def send_digest(session: Session, settings: Settings, run: ScanRun | None, report: ExportedReport | None, *,
                provider: EmailProvider | None = None, dry_run: bool = False, kind: str = "daily_digest",
                sleep=time.sleep) -> list[EmailDelivery]:
    """Send the digest once per recipient. ``kind="weekly_digest"`` is keyed by the week, not the run."""
    es = settings.email
    weekly = kind == "weekly_digest"
    recipients = [r for r in es.to + es.cc if r]
    if not recipients:
        raise EmailError("no recipients configured (email.to / email.cc or CHANAKYA_EMAIL_TO)")
    attach = es.attach_excel and report is not None and Path(report.path).exists() and \
        Path(report.path).stat().st_size <= es.max_attachment_mb * 1024 * 1024
    digest = build_digest(session, settings, run, dry_run=dry_run, attached=attach, weekly=weekly)
    week = weekly_period_key(settings) if weekly else None
    if dry_run:
        provider = FileEmailProvider(es.outbox_dir / "dry_run")
    provider = provider or build_provider(settings)
    results = []
    for rcpt in recipients:
        key = idempotency_key(kind + (":dry" if dry_run else ""), None if weekly else (report.id if report else None),
                              week if weekly else (run.id if run else None), rcpt)
        row = session.scalar(select(EmailDelivery).where(EmailDelivery.idempotency_key == key))
        if row and row.status in ("sent", "dry_run"):
            results.append(row)
            continue
        if row is None:
            row = EmailDelivery(idempotency_key=key, report_id=report.id if report else None,
                                run_id=run.id if run else None, kind=kind, recipient=rcpt, status="pending")
            session.add(row)
            session.flush()
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = digest.subject, es.sender, rcpt
        msg["Message-ID"] = make_msgid(domain=es.sender.split("@")[-1] if "@" in es.sender else None)
        msg["X-Chanakya-Idempotency-Key"] = key
        msg.set_content(digest.text)
        msg.add_alternative(digest.html, subtype="html")
        if attach:
            p = Path(report.path)
            msg.add_attachment(p.read_bytes(), maintype="application",
                               subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=p.name)
        _deliver(provider, msg, [row], es.max_retries, dry_run, sleep)
        session.commit()
        results.append(row)
    return results


def _deliver(provider: EmailProvider, msg: EmailMessage, rows: list[EmailDelivery], max_retries: int,
             dry_run: bool, sleep) -> None:
    """Send one message and record the outcome on every delivery row it covers."""
    for attempt in range(max_retries):
        status, error, message_id, retry = "failed", None, None, True
        try:
            message_id = provider.send(msg)
            status = "dry_run" if dry_run else "sent"
        except EmailAuthError as exc:
            error, retry = str(exc), False  # a wrong password does not get better on retry
        except EmailError as exc:
            error = str(exc)[:500]
        for row in rows:
            row.attempts += 1
            row.status, row.last_error = status, error
            if message_id:
                row.provider_message_id, row.sent_at = message_id, now_utc()
        if status != "failed" or not retry or attempt == max_retries - 1:
            return
        sleep(2 ** attempt)


# ---------------------------------------------------------------- weekly digest
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def weekly_period_key(settings: Settings, now=None) -> str:
    """The IST date the current digest week started on (the configured weekday, on or before today)."""
    today = (now or now_ist()).date()
    day = settings.schedule.weekly_digest_day.strip().lower()[:3]
    start = _WEEKDAYS.index(day) if day in _WEEKDAYS else 0
    return (today - timedelta(days=(today.weekday() - start) % 7)).isoformat()


def weekly_digest_due(session: Session, settings: Settings, now=None) -> bool:
    """True until every recipient has this week's digest. A missed day is caught up on the next run."""
    week = weekly_period_key(settings, now)
    for rcpt in [r for r in settings.email.to + settings.email.cc if r]:
        key = idempotency_key("weekly_digest", None, week, rcpt)
        row = session.scalar(select(EmailDelivery).where(EmailDelivery.idempotency_key == key))
        if row is None or row.status != "sent":
            return True
    return False


# ---------------------------------------------------------------- new P1 alert
def pending_p1_alerts(session: Session, recipient: str, *, dry_run: bool = False) -> list[Opportunity]:
    """Active P1 notices this recipient has not been alerted about. Each notice alerts once, ever."""
    kind = "p1_alert" + (":dry" if dry_run else "")
    done = ("sent", "dry_run")
    opps = session.scalars(
        select(Opportunity)
        .where(Opportunity.status == TenderStatus.ACTIVE.value, Opportunity.priority == "P1",
               Opportunity.filter_decision != "rejected")
        .order_by(Opportunity.relevance_score.desc().nulls_last(), Opportunity.first_seen_at.asc(), Opportunity.id.asc())
    ).all()
    pending = []
    for o in opps:
        row = session.scalar(select(EmailDelivery).where(
            EmailDelivery.idempotency_key == idempotency_key(kind, None, o.id, recipient)))
        if row is None or row.status not in done:
            pending.append(o)
    return pending


def _alert_item(o: Opportunity, settings: Settings) -> dict:
    intel = o.intelligence or {}
    left = days_remaining(o.closing_at)
    return {
        "score": o.relevance_score, "title": o.title, "organisation": o.organisation or "Organisation not stated",
        "platform": o.platform, "tender_id": o.source_tender_id or o.ref_no or o.id,
        "closing": format_ist(o.closing_at) or "not stated",
        "days_left": None if left is None else max(0, int(left)),
        "solution": intel.get("relevant_solution") or "Not recorded", "why": intel.get("why_match"),
        "eligibility": o.eligibility_status, "next_action": intel.get("recommended_next_action"),
        "url": o.official_url if is_safe_link(o.official_url, settings.http.allowed_domains) else None,
    }


def build_p1_alert(opps: list[Opportunity], settings: Settings, *, dry_run: bool = False) -> Digest:
    items = plain([_alert_item(o, settings) for o in opps])
    if len(items) == 1:
        title = items[0]["title"]
        subject = "CHANAKYA | New P1: " + (title if len(title) <= 80 else title[:80].rsplit(" ", 1)[0] + "...")
    else:
        subject = f"CHANAKYA | {len(items)} new P1 opportunities"
    if dry_run:
        subject = "[DRY RUN] " + subject
    html = _env.get_template("p1_alert.html.j2").render(subject=subject, items=items, dry_run=dry_run)
    lines = [subject, ""]
    for it in items:
        closes = f"closes {it['closing']}" + (f" ({it['days_left']} days left)" if it["days_left"] is not None else "")
        lines += [f"[P1 | {it['score']}] {it['title']}", f"  {it['organisation']} | Tender ID {it['tender_id']} | {closes}",
                  f"  Fit: {it['solution']} | Eligibility: {it['eligibility']}", f"  Next: {it['next_action']}",
                  f"  {it['url'] or ''}", ""]
    lines.append("Scores are decision support; eligibility is not assumed. Verify on the official portal.")
    return Digest(subject, html, "\n".join(lines), [o.id for o in opps])


def send_p1_alert(session: Session, settings: Settings, run: ScanRun | None = None, *,
                  provider: EmailProvider | None = None, dry_run: bool = False, sleep=time.sleep) -> list[EmailDelivery]:
    """One message per recipient listing every P1 notice they have not yet been told about.

    Delivery is recorded per notice and recipient, so a notice is never alerted twice
    and a failed send is retried on the next run without repeating the ones that landed.
    """
    es = settings.email
    recipients = [r for r in es.to + es.cc if r]
    if not recipients:
        raise EmailError("no recipients configured (email.to / email.cc or CHANAKYA_EMAIL_TO)")
    kind = "p1_alert" + (":dry" if dry_run else "")
    results: list[EmailDelivery] = []
    for rcpt in recipients:
        opps = pending_p1_alerts(session, rcpt, dry_run=dry_run)
        if not opps:
            continue
        if dry_run:
            provider = FileEmailProvider(es.outbox_dir / "dry_run")
        provider = provider or build_provider(settings)
        rows = []
        for o in opps:
            key = idempotency_key(kind, None, o.id, rcpt)
            row = session.scalar(select(EmailDelivery).where(EmailDelivery.idempotency_key == key))
            if row is None:
                row = EmailDelivery(idempotency_key=key, run_id=run.id if run else None, kind="p1_alert",
                                    recipient=rcpt, status="pending")
                session.add(row)
            rows.append(row)
        session.flush()
        alert = build_p1_alert(opps, settings, dry_run=dry_run)
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = alert.subject, es.sender, rcpt
        msg["Message-ID"] = make_msgid(domain=es.sender.split("@")[-1] if "@" in es.sender else None)
        msg["X-Chanakya-Kind"] = "p1_alert"
        msg.set_content(alert.text)
        msg.add_alternative(alert.html, subtype="html")
        _deliver(provider, msg, rows, es.max_retries, dry_run, sleep)
        session.commit()
        results.extend(rows)
    return results
