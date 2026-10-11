"""Follow-up alerts for notices BD is already working on.

Two alerts, both off by default:

* corrigendum alert: a material change (deadline, value, EMD, corrigendum,
  status, title, description, documents) to a watched notice. Told once per
  change event and recipient.
* deadline alert: one reminder per threshold in ``email.deadline_alert_days``
  when a watched, still open notice is that close to its closing date.

A notice is "watched" when its review status is Shortlisted, Reviewing,
Qualified, Partner needed or Pursuing, or when it is priority P1 and BD has not
closed it out (Rejected, Expired, Won, Lost).

Changes are detected by the ordinary scan: every pass re-reads the portal
listings and ``pipeline/store.py`` records a ``ChangeEvent`` for each difference.
Nothing here fetches anything. Delivery follows the new-P1 alert: one message
per recipient, one ``EmailDelivery`` row per item, a failed send stays pending,
and a dry run is keyed separately so a preview does not use the alert up.
"""

from __future__ import annotations

import time
from datetime import timedelta
from email.message import EmailMessage
from email.utils import make_msgid

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import ChangeEvent, EmailDelivery, Opportunity, ScanRun
from ..models import ReviewStatus, TenderStatus
from ..security import is_safe_link
from ..timeutil import days_remaining, format_ist, now_utc
from .email import (
    Digest, EmailError, EmailProvider, FileEmailProvider, _deliver, _env, build_provider, idempotency_key,
)
from .text import plain

CORRIGENDUM_KIND = "corrigendum_alert"
DEADLINE_KIND = "deadline_alert"

# Review statuses that mean "BD is working on this".
WATCHED_REVIEW = (ReviewStatus.SHORTLISTED.value, ReviewStatus.REVIEWING.value, ReviewStatus.QUALIFIED.value,
                  ReviewStatus.PARTNER_NEEDED.value, ReviewStatus.PURSUING.value)
# Review statuses that mean "BD is done with this": a P1 score alone no longer makes it watched.
CLOSED_REVIEW = (ReviewStatus.REJECTED.value, ReviewStatus.EXPIRED.value, ReviewStatus.WON.value,
                 ReviewStatus.LOST.value)
# Change types worth an alert. "new" is the P1 alert's job; "retendered" is recorded on a new notice.
ALERT_CHANGE_TYPES = ("deadline_changed", "value_changed", "corrigendum_added", "status_changed",
                      "document_changed", "details_changed")

_FIELD_LABELS = {
    "closing_at": "Closing date", "opening_at": "Bid opening date", "estimated_value_inr": "Estimated value (INR)",
    "emd_inr": "EMD (INR)", "corrigendum": "Corrigendum", "status": "Status", "title": "Title",
    "description": "Description", "documents": "Documents",
}
_DONE = ("sent", "dry_run")
_FOOTER = "Confirm every change and date on the official portal before acting."


def watched_clause():
    """SQL condition for the notices BD cares about."""
    return or_(Opportunity.review_status.in_(WATCHED_REVIEW),
               and_(Opportunity.priority == "P1", Opportunity.filter_decision != "rejected",
                    Opportunity.review_status.not_in(CLOSED_REVIEW)))


def is_watched(o: Opportunity) -> bool:
    if o.review_status in WATCHED_REVIEW:
        return True
    return o.priority == "P1" and o.filter_decision != "rejected" and o.review_status not in CLOSED_REVIEW


def _recipients(settings: Settings) -> list[str]:
    recipients = [r for r in settings.email.to + settings.email.cc if r]
    if not recipients:
        raise EmailError("no recipients configured (email.to / email.cc or CHANAKYA_EMAIL_TO)")
    return recipients


def _delivered(session: Session, key: str) -> bool:
    row = session.scalar(select(EmailDelivery).where(EmailDelivery.idempotency_key == key))
    return row is not None and row.status in _DONE


def _notice(o: Opportunity, settings: Settings) -> dict:
    left = days_remaining(o.closing_at)
    return {
        "priority": o.priority or "Not scored", "review": o.review_status, "title": o.title,
        "organisation": o.organisation or "Organisation not stated", "platform": o.platform,
        "tender_id": o.source_tender_id or o.ref_no or o.id, "closing": format_ist(o.closing_at) or "not stated",
        "days_left": None if left is None or left < 0 else int(left), "status": o.status,
        "next_action": (o.intelligence or {}).get("recommended_next_action"),
        "url": o.official_url if is_safe_link(o.official_url, settings.http.allowed_domains) else None,
    }


def _short(title: str) -> str:
    return title if len(title) <= 80 else title[:80].rsplit(" ", 1)[0] + "..."


def _send(session: Session, settings: Settings, provider: EmailProvider, alert: Digest, rcpt: str,
          keys: list[str], kind: str, run: ScanRun | None, dry_run: bool, sleep) -> list[EmailDelivery]:
    """One message to one recipient, recorded on one delivery row per key."""
    es = settings.email
    rows = []
    for key in keys:
        row = session.scalar(select(EmailDelivery).where(EmailDelivery.idempotency_key == key))
        if row is None:
            row = EmailDelivery(idempotency_key=key, run_id=run.id if run else None, kind=kind, recipient=rcpt,
                                status="pending")
            session.add(row)
        rows.append(row)
    session.flush()
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = alert.subject, es.sender, rcpt
    msg["Message-ID"] = make_msgid(domain=es.sender.split("@")[-1] if "@" in es.sender else None)
    msg["X-Chanakya-Kind"] = kind
    msg.set_content(alert.text)
    msg.add_alternative(alert.html, subtype="html")
    _deliver(provider, msg, rows, es.max_retries, dry_run, sleep)
    return rows


# ---------------------------------------------------------------- corrigendum alert
def _corrigendum_key(event: ChangeEvent, recipient: str, dry_run: bool) -> str:
    return idempotency_key(CORRIGENDUM_KIND + (":dry" if dry_run else ""), None, f"change:{event.id}", recipient)


def pending_corrigendum_alerts(session: Session, settings: Settings, recipient: str, *, dry_run: bool = False,
                               now=None) -> list[tuple[Opportunity, list[ChangeEvent]]]:
    """Material changes to watched notices this recipient has not been told about, grouped by notice.

    Only changes detected in the last ``email.corrigendum_alert_window_days`` count, so
    switching the alert on does not replay the whole change history.
    """
    since = (now or now_utc()) - timedelta(days=settings.email.corrigendum_alert_window_days)
    found = session.execute(
        select(ChangeEvent, Opportunity)
        .join(Opportunity, Opportunity.id == ChangeEvent.opportunity_id)
        .where(ChangeEvent.material.is_(True), ChangeEvent.change_type.in_(ALERT_CHANGE_TYPES),
               ChangeEvent.detected_at >= since, watched_clause())
        .order_by(ChangeEvent.detected_at, ChangeEvent.id)
    ).all()
    grouped: dict[str, tuple[Opportunity, list[ChangeEvent]]] = {}
    for event, opp in found:
        if _delivered(session, _corrigendum_key(event, recipient, dry_run)):
            continue
        grouped.setdefault(opp.id, (opp, []))[1].append(event)
    return list(grouped.values())


def build_corrigendum_alert(groups: list[tuple[Opportunity, list[ChangeEvent]]], settings: Settings, *,
                            dry_run: bool = False) -> Digest:
    items = []
    for opp, events in groups:
        item = _notice(opp, settings)
        item["changes"] = [{"label": _FIELD_LABELS.get(e.field or "", (e.field or e.change_type).replace("_", " ").capitalize()),
                            "old": e.old_value or "Not stated", "new": e.new_value or "Not stated",
                            "at": format_ist(e.detected_at) or "not recorded"} for e in events]
        items.append(item)
    items = plain(items)
    total = sum(len(it["changes"]) for it in items)
    if len(items) == 1:
        subject = "CHANAKYA | Change to a notice you are tracking: " + _short(items[0]["title"])
    else:
        subject = f"CHANAKYA | {total} changes to {len(items)} notices you are tracking"
    if dry_run:
        subject = "[DRY RUN] " + subject
    html = _env.get_template("corrigendum_alert.html.j2").render(subject=subject, items=items, total=total, dry_run=dry_run)
    lines = [subject, ""]
    for it in items:
        lines += [f"[{it['priority']} | {it['review']}] {it['title']}",
                  f"  {it['organisation']} | Tender ID {it['tender_id']} | closes {it['closing']}"]
        for c in it["changes"]:
            lines += [f"  Changed: {c['label']} (seen {c['at']})", f"    Was: {c['old']}", f"    Now: {c['new']}"]
        lines += [f"  {it['url'] or ''}", ""]
    lines.append("You are told about each change once. " + _FOOTER)
    return Digest(subject, html, "\n".join(lines), [opp.id for opp, _ in groups])


def send_corrigendum_alert(session: Session, settings: Settings, run: ScanRun | None = None, *,
                           provider: EmailProvider | None = None, dry_run: bool = False,
                           sleep=time.sleep) -> list[EmailDelivery]:
    """One message per recipient listing every material change they have not been told about.

    Returns one delivery row per (change event, recipient). Nothing pending means no message.
    """
    results: list[EmailDelivery] = []
    for rcpt in _recipients(settings):
        groups = pending_corrigendum_alerts(session, settings, rcpt, dry_run=dry_run)
        if not groups:
            continue
        if dry_run:
            provider = FileEmailProvider(settings.email.outbox_dir / "dry_run")
        provider = provider or build_provider(settings)
        events = [e for _, evs in groups for e in evs]
        rows = _send(session, settings, provider, build_corrigendum_alert(groups, settings, dry_run=dry_run), rcpt,
                     [_corrigendum_key(e, rcpt, dry_run) for e in events], CORRIGENDUM_KIND, run, dry_run, sleep)
        if not dry_run and all(r.status == "sent" for r in rows):
            for e in events:
                e.notified = True
        session.commit()
        results.extend(rows)
    return results


# ---------------------------------------------------------------- deadline alert
def due_threshold(days_left: float | None, thresholds: list[int]) -> int | None:
    """The tightest threshold the notice is inside, or None. 1.5 days left with [7, 2] gives 2."""
    if days_left is None or days_left < 0:
        return None
    inside = [t for t in thresholds if days_left <= t]
    return min(inside) if inside else None


def _deadline_key(o: Opportunity, threshold: int, recipient: str, dry_run: bool) -> str:
    # The closing date is part of the key: an extended deadline is a new deadline and is reminded again.
    return idempotency_key(DEADLINE_KIND + (":dry" if dry_run else ""), None,
                           f"{o.id}|{threshold}|{o.closing_at.isoformat()}", recipient)


def pending_deadline_alerts(session: Session, settings: Settings, recipient: str, *, dry_run: bool = False,
                            now=None) -> list[tuple[Opportunity, int]]:
    """Watched, open notices inside a reminder threshold that this recipient has not been reminded of.

    Only the tightest threshold is due: a notice first seen with one day left gets the
    2 day reminder, not the 7 day one as well. Closed and expired notices never qualify.
    """
    now = now or now_utc()
    thresholds = settings.email.deadline_alert_days
    if not thresholds:
        return []
    opps = session.scalars(
        select(Opportunity)
        .where(Opportunity.status == TenderStatus.ACTIVE.value, Opportunity.closing_at.is_not(None),
               Opportunity.closing_at >= now, Opportunity.closing_at <= now + timedelta(days=max(thresholds)),
               watched_clause())
        .order_by(Opportunity.closing_at)
    ).all()
    pending = []
    for o in opps:
        threshold = due_threshold(days_remaining(o.closing_at, now), thresholds)
        if threshold is not None and not _delivered(session, _deadline_key(o, threshold, recipient, dry_run)):
            pending.append((o, threshold))
    return pending


def build_deadline_alert(due: list[tuple[Opportunity, int]], settings: Settings, *, dry_run: bool = False) -> Digest:
    items = []
    for opp, threshold in due:
        item = _notice(opp, settings)
        item["threshold"] = threshold
        items.append(item)
    items = plain(items)
    if len(items) == 1:
        subject = f"CHANAKYA | Closing in {_days(items[0]['days_left'])}: " + _short(items[0]["title"])
    else:
        subject = f"CHANAKYA | {len(items)} tracked notices are closing soon"
    if dry_run:
        subject = "[DRY RUN] " + subject
    html = _env.get_template("deadline_alert.html.j2").render(subject=subject, items=items, dry_run=dry_run)
    lines = [subject, ""]
    for it in items:
        lines += [f"[{it['priority']} | {it['review']}] {it['title']}",
                  f"  {it['organisation']} | Tender ID {it['tender_id']}",
                  f"  Closes {it['closing']} ({_days(it['days_left'])} left; {it['threshold']} day reminder)"]
        if it["next_action"]:
            lines.append(f"  Next: {it['next_action']}")
        lines += [f"  {it['url'] or ''}", ""]
    lines.append("You get one reminder per notice at each reminder point. " + _FOOTER)
    return Digest(subject, html, "\n".join(lines), [opp.id for opp, _ in due])


def _days(n: int | None) -> str:
    if n is None:
        return "an unknown time"
    return "less than a day" if n == 0 else ("1 day" if n == 1 else f"{n} days")


def send_deadline_alert(session: Session, settings: Settings, run: ScanRun | None = None, *,
                        provider: EmailProvider | None = None, dry_run: bool = False,
                        sleep=time.sleep) -> list[EmailDelivery]:
    """One message per recipient listing the watched notices that just came inside a reminder threshold.

    Returns one delivery row per (notice, threshold, recipient). Nothing due means no message.
    """
    results: list[EmailDelivery] = []
    for rcpt in _recipients(settings):
        due = pending_deadline_alerts(session, settings, rcpt, dry_run=dry_run)
        if not due:
            continue
        if dry_run:
            provider = FileEmailProvider(settings.email.outbox_dir / "dry_run")
        provider = provider or build_provider(settings)
        rows = _send(session, settings, provider, build_deadline_alert(due, settings, dry_run=dry_run), rcpt,
                     [_deadline_key(o, t, rcpt, dry_run) for o, t in due], DEADLINE_KIND, run, dry_run, sleep)
        session.commit()
        results.extend(rows)
    return results
