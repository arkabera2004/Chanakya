"""Corrigendum alert and deadline alert (file email provider, simulated model).

Every record here is SYNTHETIC (see ``sample_records`` in conftest): no real tender data.
"""

from __future__ import annotations

import email as email_lib
import email.policy
from datetime import timedelta

from sqlalchemy import select
from typer.testing import CliRunner

from chanakya import cli
from chanakya.app import run_pipeline
from chanakya.db import ChangeEvent, EmailDelivery, Opportunity
from chanakya.reporting.email import EmailError
from chanakya.reporting.followups import (
    due_threshold, pending_corrigendum_alerts, pending_deadline_alerts, send_corrigendum_alert, send_deadline_alert,
)
from chanakya.timeutil import now_utc

from .conftest import ist, sample_records, write_records

ROAD = "2026_SYNPWD_000102_1"        # not P1: watched only when BD shortlists it (closes in 15 days)
HELPLINE = "2026_SYNHLT_000103_1"    # closes in 4 days
CATERING = "2026_SYNCAT_000106_1"    # nobody is tracking this one


class Down:
    def send(self, msg):
        raise EmailError("relay down")


def opp(session, tender_id: str) -> Opportunity:
    return session.scalar(select(Opportunity).where(Opportunity.source_tender_id == tender_id))


def scored(settings, session, shortlist: tuple[str, ...] = ()):
    write_records(settings.sources[0].path, sample_records())
    run_pipeline(settings, session, email=False)
    for tender_id in shortlist:
        opp(session, tender_id).review_status = "Shortlisted"
    # Keep the P1 arm of "watched" out of these tests unless a test asks for it.
    for o in session.scalars(select(Opportunity).where(Opportunity.priority == "P1")):
        o.priority = "P2"
    session.commit()


def rescan(settings, session, changes: dict[str, dict], **run_kwargs):
    """Publish the same synthetic listing again with some fields changed, as a portal corrigendum would."""
    records = sample_records()
    for rec in records:
        rec.update(changes.get(rec["tender_id"], {}))
    write_records(settings.sources[0].path, records)
    return run_pipeline(settings, session, **{"email": False, **run_kwargs})


def read(path):
    return email_lib.message_from_bytes(path.read_bytes(), policy=email.policy.default)


def outbox(settings, sub: str = "") -> list:
    folder = settings.email.outbox_dir / sub if sub else settings.email.outbox_dir
    return sorted(folder.glob("*.eml"))


def material_events(session, tender_id: str) -> list[ChangeEvent]:
    return list(session.scalars(select(ChangeEvent).where(
        ChangeEvent.opportunity_id == opp(session, tender_id).id, ChangeEvent.material.is_(True),
        ChangeEvent.change_type != "new")))


# ---------------------------------------------------------------- corrigendum alert
def test_a_change_to_a_tracked_notice_is_told_once_per_recipient(settings, session):
    scored(settings, session, shortlist=(ROAD,))
    old_closing = opp(session, ROAD).closing_at
    rescan(settings, session, {ROAD: {"closing_at": ist(30), "estimated_value_inr": 12500000},
                               CATERING: {"closing_at": ist(12)}})
    events = material_events(session, ROAD)
    assert {e.field for e in events} == {"closing_at", "estimated_value_inr"}
    assert opp(session, ROAD).closing_at != old_closing

    rows = send_corrigendum_alert(session, settings)
    assert {r.status for r in rows} == {"sent"} and {r.kind for r in rows} == {"corrigendum_alert"}
    assert len(rows) == len(events) * 2                   # one row per change and recipient (to + cc)
    assert len(outbox(settings)) == 2                     # but one message per recipient
    msg = read(outbox(settings)[0])
    assert msg["X-Chanakya-Kind"] == "corrigendum_alert" and "Road resurfacing" in msg["Subject"]
    body = msg.get_body(preferencelist=("plain",)).get_content()
    closing = next(e for e in events if e.field == "closing_at")
    assert "Changed: Closing date" in body and "Changed: Estimated value (INR)" in body
    assert f"Was: {closing.old_value}" in body and f"Now: {closing.new_value}" in body
    assert "Now: 12500000" in body and "Was: Not stated" in body
    assert "Catering" not in body                         # changed too, but nobody is tracking it
    assert "—" not in body and "–" not in body
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert closing.new_value in html and "—" not in html and "–" not in html
    assert all(e.notified for e in events)
    # Nothing new: no rows, no messages.
    assert send_corrigendum_alert(session, settings) == []
    assert len(outbox(settings)) == 2


def test_only_the_new_change_is_told_next_time_and_the_link_is_allowlisted(settings, session):
    scored(settings, session, shortlist=(ROAD,))
    settings.email.cc = []
    opp(session, CATERING).review_status = "Pursuing"
    session.commit()
    rescan(settings, session, {ROAD: {"closing_at": ist(30)}})
    assert len(send_corrigendum_alert(session, settings)) == 1
    rescan(settings, session, {ROAD: {"closing_at": ist(30), "title": "Road resurfacing work (revised scope)"},
                               CATERING: {"emd_inr": 50000}})
    # The scan stores whatever link the source gave; only an allowlisted government host is linked.
    opp(session, ROAD).official_url = "https://eprocure.gov.in/eprocure/app"
    opp(session, CATERING).official_url = "https://evil.example.net/phish"
    session.commit()
    rows = send_corrigendum_alert(session, settings)
    assert len(rows) == 2 and len(outbox(settings)) == 2
    body = read(max(outbox(settings), key=lambda f: f.stat().st_mtime_ns)).get_body(preferencelist=("plain",)).get_content()
    assert body.count("Changed:") == 2 and "Changed: Title" in body and "Changed: EMD (INR)" in body
    assert "Changed: Closing date" not in body            # the earlier change is not repeated
    assert "https://eprocure.gov.in/eprocure/app" in body and "evil.example.net" not in body


def test_a_p1_notice_is_tracked_without_a_review_status_until_bd_closes_it_out(settings, session):
    scored(settings, session)
    rescan(settings, session, {ROAD: {"closing_at": ist(30)}})
    to = settings.email.to[0]
    assert pending_corrigendum_alerts(session, settings, to) == []       # review status New, not P1
    road = opp(session, ROAD)
    road.priority, road.filter_decision = "P1", "strong"
    session.commit()
    assert [o.source_tender_id for o, _ in pending_corrigendum_alerts(session, settings, to)] == [ROAD]
    road.review_status = "Rejected"
    session.commit()
    assert pending_corrigendum_alerts(session, settings, to) == []


def test_a_change_older_than_the_window_is_not_replayed(settings, session):
    scored(settings, session, shortlist=(ROAD,))
    rescan(settings, session, {ROAD: {"closing_at": ist(30)}})
    for e in material_events(session, ROAD):
        e.detected_at = now_utc() - timedelta(days=settings.email.corrigendum_alert_window_days + 1)
    session.commit()
    assert send_corrigendum_alert(session, settings) == []


def test_a_failed_corrigendum_alert_is_retried_and_a_dry_run_does_not_use_it_up(settings, session):
    scored(settings, session, shortlist=(ROAD,))
    settings.email.cc = []
    to = settings.email.to[0]
    rescan(settings, session, {ROAD: {"closing_at": ist(30)}})

    failed = send_corrigendum_alert(session, settings, provider=Down(), sleep=lambda s: None)
    assert {r.status for r in failed} == {"failed"} and all(r.attempts == settings.email.max_retries for r in failed)
    assert pending_corrigendum_alerts(session, settings, to)
    assert not any(e.notified for e in material_events(session, ROAD))
    dry = send_corrigendum_alert(session, settings, dry_run=True)
    assert {r.status for r in dry} == {"dry_run"} and outbox(settings, "dry_run")
    assert read(outbox(settings, "dry_run")[0])["Subject"].startswith("[DRY RUN] ")
    assert pending_corrigendum_alerts(session, settings, to)     # the preview did not count as telling them
    assert not outbox(settings)
    sent = send_corrigendum_alert(session, settings)
    assert {r.status for r in sent} == {"sent"}
    assert not pending_corrigendum_alerts(session, settings, to)
    assert session.scalar(select(EmailDelivery).where(EmailDelivery.status == "failed")) is None


# ---------------------------------------------------------------- deadline alert
def test_the_tightest_threshold_is_the_one_that_is_due():
    assert due_threshold(10, [7, 2]) is None
    assert due_threshold(7, [7, 2]) == 7 and due_threshold(4.2, [7, 2]) == 7
    assert due_threshold(1.5, [7, 2]) == 2 and due_threshold(0, [2, 7]) == 2
    assert due_threshold(-0.1, [7, 2]) is None and due_threshold(None, [7, 2]) is None
    assert due_threshold(3, []) is None


def test_one_reminder_per_threshold_per_notice_per_recipient(settings, session):
    scored(settings, session, shortlist=(HELPLINE, ROAD))    # helpline closes in 4 days, road in 15
    rows = send_deadline_alert(session, settings)
    assert {r.status for r in rows} == {"sent"} and {r.kind for r in rows} == {"deadline_alert"}
    assert len(rows) == 2 and len(outbox(settings)) == 2  # one notice, two recipients
    msg = read(outbox(settings)[0])
    assert msg["X-Chanakya-Kind"] == "deadline_alert" and "Closing in" in msg["Subject"]
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Speech analytics" in body and "7 day reminder" in body and "Road resurfacing" not in body
    assert "—" not in body and "–" not in body
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "7 day reminder" in html and "—" not in html and "–" not in html
    assert send_deadline_alert(session, settings) == []   # same threshold: quiet
    # Time passes: inside the 2 day threshold the notice is reminded once more, then never again.
    opp(session, HELPLINE).closing_at = now_utc() + timedelta(days=1, hours=6)
    session.commit()
    again = send_deadline_alert(session, settings)
    assert len(again) == 2 and len(outbox(settings)) == 4
    latest = read(max(outbox(settings), key=lambda f: f.stat().st_mtime_ns)).get_body(preferencelist=("plain",)).get_content()
    assert "2 day reminder" in latest and "1 day left" in latest
    assert send_deadline_alert(session, settings) == []


def test_a_closed_or_expired_notice_is_never_reminded(settings, session):
    scored(settings, session, shortlist=(HELPLINE, ROAD, "2026_SYNOLD_000105_1"))
    to = settings.email.to[0]
    assert opp(session, "2026_SYNOLD_000105_1").status == "expired"      # closed three days ago
    helpline, road = opp(session, HELPLINE), opp(session, ROAD)
    helpline.status = "cancelled"                         # inside the threshold, but withdrawn
    road.closing_at = now_utc() - timedelta(hours=2)      # still marked active, but the date has passed
    session.commit()
    assert pending_deadline_alerts(session, settings, to) == []
    assert send_deadline_alert(session, settings) == [] and not outbox(settings)
    # An untracked notice inside the threshold is not reminded either.
    helpline.status, helpline.review_status = "active", "New"
    session.commit()
    assert send_deadline_alert(session, settings) == []


def test_an_extended_deadline_is_reminded_again_at_the_same_threshold(settings, session):
    scored(settings, session, shortlist=(HELPLINE,))
    settings.email.cc = []
    assert len(send_deadline_alert(session, settings)) == 1
    opp(session, HELPLINE).closing_at = now_utc() + timedelta(days=20)   # a corrigendum moved the date out
    session.commit()
    assert send_deadline_alert(session, settings) == []
    opp(session, HELPLINE).closing_at = now_utc() + timedelta(days=6)    # and now the new date is a week away
    session.commit()
    assert len(send_deadline_alert(session, settings)) == 1


def test_a_failed_deadline_alert_is_retried_and_a_dry_run_does_not_use_it_up(settings, session):
    scored(settings, session, shortlist=(HELPLINE,))
    settings.email.cc = []
    to = settings.email.to[0]
    failed = send_deadline_alert(session, settings, provider=Down(), sleep=lambda s: None)
    assert {r.status for r in failed} == {"failed"} and all(r.attempts == settings.email.max_retries for r in failed)
    assert pending_deadline_alerts(session, settings, to)
    dry = send_deadline_alert(session, settings, dry_run=True)
    assert {r.status for r in dry} == {"dry_run"} and outbox(settings, "dry_run")
    assert pending_deadline_alerts(session, settings, to)        # the preview did not count as reminding them
    sent = send_deadline_alert(session, settings)
    assert {r.status for r in sent} == {"sent"}
    assert not pending_deadline_alerts(session, settings, to)
    assert session.scalar(select(EmailDelivery).where(EmailDelivery.status == "failed")) is None


# ---------------------------------------------------------------- wiring
def followup_rows(session) -> list[EmailDelivery]:
    return list(session.scalars(select(EmailDelivery).where(
        EmailDelivery.kind.in_(["corrigendum_alert", "deadline_alert"]))))


def test_a_run_sends_the_follow_up_alerts_only_when_they_are_switched_on(settings, session):
    assert settings.email.alert_on_corrigendum is False and settings.email.alert_on_deadline is False
    assert settings.email.deadline_alert_days == [7, 2]
    scored(settings, session, shortlist=(HELPLINE,))
    quiet = rescan(settings, session, {HELPLINE: {"emd_inr": 75000}}, email=True)
    assert quiet.corrigendum_alerts_sent == 0 and quiet.deadline_alerts_sent == 0
    assert followup_rows(session) == []

    settings.email.alert_on_corrigendum = True
    settings.email.alert_on_deadline = True
    # A dry run previews both and uses neither up.
    preview = rescan(settings, session, {HELPLINE: {"emd_inr": 75000}}, dry_run=True)
    assert preview.corrigendum_alerts_sent == 2 and preview.deadline_alerts_sent == 2
    assert {r.status for r in followup_rows(session)} == {"dry_run"}
    kinds = {read(f)["X-Chanakya-Kind"] for f in outbox(settings, "dry_run")}
    assert {"corrigendum_alert", "deadline_alert"} <= kinds

    loud = rescan(settings, session, {HELPLINE: {"emd_inr": 75000}}, email=True)
    assert loud.corrigendum_alerts_sent == 2 and loud.deadline_alerts_sent == 2 and loud.exit_code == 0
    again = rescan(settings, session, {HELPLINE: {"emd_inr": 75000}}, email=True)
    assert again.corrigendum_alerts_sent == 0 and again.deadline_alerts_sent == 0


def test_the_email_command_sends_each_follow_up_alert_without_a_scan(settings, session, monkeypatch):
    scored(settings, session, shortlist=(HELPLINE,))
    rescan(settings, session, {HELPLINE: {"emd_inr": 75000}})
    session.close()
    monkeypatch.setattr(cli, "settings", lambda: settings)
    runner = CliRunner()
    for kind, nothing in (("corrigendum", "no change to a tracked notice"), ("deadline", "no tracked notice is due")):
        first = runner.invoke(cli.app, ["email", "--kind", kind])
        assert first.exit_code == 0, first.output
        assert "bd@example.com: sent" in first.output and "cto@example.com: sent" in first.output
        second = runner.invoke(cli.app, ["email", "--kind", kind])
        assert second.exit_code == 0 and nothing in second.output
    assert runner.invoke(cli.app, ["email", "--kind", "nonsense"]).exit_code == 1
    assert {read(f)["X-Chanakya-Kind"] for f in outbox(settings)} == {"corrigendum_alert", "deadline_alert"}
