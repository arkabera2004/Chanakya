"""New-P1 alert and weekly digest (file email provider, simulated model)."""

from __future__ import annotations

import email as email_lib
import email.policy
from datetime import datetime

from sqlalchemy import select

from chanakya.app import run_pipeline
from chanakya.db import EmailDelivery, Opportunity
from chanakya.reporting.email import (
    EmailError, pending_p1_alerts, send_digest, send_p1_alert, weekly_digest_due, weekly_period_key,
)
from chanakya.timeutil import IST

from .conftest import sample_records, write_records


def scored(settings, session):
    write_records(settings.sources[0].path, sample_records())
    return run_pipeline(settings, session, email=False)


def p1_ids(session) -> list[str]:
    return list(session.scalars(select(Opportunity.id).where(Opportunity.priority == "P1", Opportunity.status == "active")))


def read(path):
    return email_lib.message_from_bytes(path.read_bytes(), policy=email.policy.default)


def outbox(settings, sub: str = "") -> list:
    folder = settings.email.outbox_dir / sub if sub else settings.email.outbox_dir
    return sorted(folder.glob("*.eml"))


def test_each_p1_is_alerted_once_per_recipient(settings, session):
    scored(settings, session)
    ids = p1_ids(session)
    assert ids, "the sample inbox must produce at least one P1"
    rows = send_p1_alert(session, settings)
    assert {r.status for r in rows} == {"sent"} and {r.kind for r in rows} == {"p1_alert"}
    assert len(rows) == len(ids) * 2                      # one row per notice and recipient (to + cc)
    assert len(outbox(settings)) == 2                     # but one message per recipient
    msg = read(outbox(settings)[0])
    assert "P1" in msg["Subject"] and msg["X-Chanakya-Kind"] == "p1_alert"
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert all(session.get(Opportunity, i).title in body for i in ids)
    assert "—" not in body and "–" not in body
    # Nothing new: no rows, no messages.
    assert send_p1_alert(session, settings) == []
    assert len(outbox(settings)) == 2


def test_only_the_notice_that_is_new_to_p1_is_alerted_next_time(settings, session):
    scored(settings, session)
    send_p1_alert(session, settings)
    before = len(outbox(settings))
    promoted = session.scalar(select(Opportunity).where(Opportunity.priority != "P1", Opportunity.status == "active"))
    promoted.priority = "P1"
    promoted.filter_decision = "strong"
    session.commit()
    rows = send_p1_alert(session, settings)
    assert len(rows) == 2 and len(outbox(settings)) == before + 2
    body = read(max(outbox(settings), key=lambda f: f.stat().st_mtime_ns)).get_body(preferencelist=("plain",)).get_content()
    assert promoted.title in body
    assert body.count("[P1 |") == 1                       # the earlier P1s are not repeated


def test_a_failed_alert_is_retried_and_a_dry_run_does_not_use_it_up(settings, session):
    scored(settings, session)
    settings.email.cc = []

    class Down:
        def send(self, msg):
            raise EmailError("relay down")

    failed = send_p1_alert(session, settings, provider=Down(), sleep=lambda s: None)
    assert {r.status for r in failed} == {"failed"} and all(r.attempts == settings.email.max_retries for r in failed)
    assert pending_p1_alerts(session, settings.email.to[0])
    dry = send_p1_alert(session, settings, dry_run=True)
    assert {r.status for r in dry} == {"dry_run"} and outbox(settings, "dry_run")
    assert pending_p1_alerts(session, settings.email.to[0])      # the preview did not count as telling them
    sent = send_p1_alert(session, settings)
    assert {r.status for r in sent} == {"sent"}
    assert not pending_p1_alerts(session, settings.email.to[0])
    assert session.scalar(select(EmailDelivery).where(EmailDelivery.status == "failed")) is None


def test_a_run_sends_the_alert_only_when_it_is_switched_on(settings, session):
    write_records(settings.sources[0].path, sample_records())
    quiet = run_pipeline(settings, session, email=True)
    assert quiet.alerts_sent == 0
    assert not session.scalars(select(EmailDelivery).where(EmailDelivery.kind == "p1_alert")).all()
    settings.email.alert_on_new_p1 = True
    loud = run_pipeline(settings, session, email=True)
    assert loud.alerts_sent == len(p1_ids(session)) * 2 and loud.exit_code == 0
    again = run_pipeline(settings, session, email=True)
    assert again.alerts_sent == 0


def test_weekly_period_starts_on_the_configured_day(settings):
    thursday = datetime(2026, 10, 8, 9, 0, tzinfo=IST)
    assert weekly_period_key(settings, thursday) == "2026-10-05"        # Monday of that week
    settings.schedule.weekly_digest_day = "fri"
    assert weekly_period_key(settings, thursday) == "2026-10-02"        # the Friday before
    assert weekly_period_key(settings, datetime(2026, 10, 9, 9, 0, tzinfo=IST)) == "2026-10-09"


def test_weekly_digest_goes_out_once_a_week_however_many_runs_there_are(settings, session):
    settings.email.digest = "weekly"
    write_records(settings.sources[0].path, sample_records())
    first = run_pipeline(settings, session, email=True)
    assert sorted(status for _, status in first.email_status) == ["sent", "sent"]
    assert not weekly_digest_due(session, settings)
    sent = len(outbox(settings))
    msg = read(outbox(settings)[0])
    assert msg["Subject"].startswith("Weekly | ")
    assert "in the last 7 days" in msg.get_body(preferencelist=("html",)).get_content()
    second = run_pipeline(settings, session, email=True)
    assert second.email_status is None and len(outbox(settings)) == sent     # same week: quiet
    # Next week the digest is due again.
    assert weekly_digest_due(session, settings, datetime(2026, 12, 1, 9, 0, tzinfo=IST))


def test_weekly_digest_counts_the_week_not_the_run(settings, session):
    scored(settings, session)
    run_pipeline(settings, session, email=False)          # a second run that finds nothing new
    rows = send_digest(session, settings, None, None, kind="weekly_digest")
    assert {r.kind for r in rows} == {"weekly_digest"}
    text = read(outbox(settings)[0]).get_body(preferencelist=("plain",)).get_content()
    new_this_week = int(text.split(" new,")[0].split()[-1])
    assert new_this_week >= 4                             # the notices stored this week, not "0 new this run"
