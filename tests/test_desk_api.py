"""The desk API narrates a real pass and records a BD decision.

Every test runs the desk on a temporary database with the simulated model and a
synthetic inbox, so nothing here touches ``data/`` or the network.
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from chanakya import server
from chanakya.server import create_app

from .conftest import sample_records, write_records


@pytest.fixture
def desk(settings, monkeypatch):
    """A desk on synthetic records. ``settings`` uses the fake model, so the desk is a demo."""
    write_records(settings.sources[0].path, sample_records())
    monkeypatch.setattr(server, "desk_settings", lambda: settings)
    monkeypatch.setattr(server, "_job", None)
    return settings


def _wait_for_job(client: TestClient, tries: int = 120, pause: float = 0.25) -> dict:
    job = None
    for _ in range(tries):
        job = client.get("/api/job").json()["job"]
        if job and job["status"] != "running":
            return job
        time.sleep(pause)
    raise AssertionError(f"job did not finish: {job}")


def test_a_pass_is_visible_and_reviewable(desk):
    with TestClient(create_app()) as client:
        assert client.get("/api/health").json()["status"] == "ok"

        started = client.post("/api/runs")
        assert started.status_code == 200, started.text
        job = _wait_for_job(client)
        assert job["status"] in ("ok", "partial"), job
        assert any(event["kind"] == "judge" for event in job["events"])
        bands = job["stats"]["priorities"]
        assert bands is not None and sum(bands.values()) == job["stats"]["assessed"] > 0

        brief = client.get("/api/brief").json()
        assert brief["demo"] is True
        assert brief["email"] == {"recipients": 2, "digest": "daily", "alert_on_new_p1": False, "provider": "file"}
        assert brief["counts"]["notices"] >= 1
        assert brief["vira"]["greeting"]
        assert brief["restricted_portals"] == []  # a demo desk has no real portals to list

        everything = client.get("/api/opportunities", params={"include_rejected": True}).json()["opportunities"]
        assert everything
        listed = client.get("/api/opportunities").json()
        assert listed["total"] >= len(listed["opportunities"])
        assert all(row["priority"] in (None, "P1", "P2", "P3") for row in listed["opportunities"])
        assert all(row["decision"] != "rejected" for row in listed["opportunities"])
        narrowed = client.get("/api/opportunities", params={"closing_within_days": 7, "capability": "CAP-AGENT"}).json()
        assert "total" in narrowed

        events = client.get("/api/events", params={"run_id": job["run_id"]}).json()
        assert any(event["kind"] == "judge" for event in events["events"])

        chosen = everything[0]
        reviewed = client.post(
            f"/api/opportunities/{chosen['id']}/review",
            json={"status": "Shortlisted", "label": "relevant", "note": "desk test"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["review_status"] == "Shortlisted"

        answer = client.post("/api/vira", json={"message": "What should I look at today?"})
        assert answer.status_code == 200
        assert answer.json()["reply"]


def test_a_second_pass_still_narrates_every_notice(desk):
    """The synthetic inbox does not change, but a demo pass must never come back empty."""
    with TestClient(create_app()) as client:
        for _ in range(2):
            assert client.post("/api/runs").status_code == 200
            job = _wait_for_job(client)
            assert sum(event["kind"] == "judge" for event in job["events"]) >= len(sample_records()) - 1


def test_desk_text_has_no_em_dashes(desk):
    with TestClient(create_app()) as client:
        client.post("/api/runs")
        job = _wait_for_job(client)
        brief = client.get("/api/brief").json()
        rows = client.get("/api/opportunities", params={"include_rejected": True}).json()["opportunities"]
        detail = client.get(f"/api/opportunities/{rows[0]['id']}").json()
        shown = [brief["vira"]["greeting"], *(event["message"] for event in job["events"])]
        for row in [*rows, detail]:
            shown += [row["eligibility"] or "", row["why"] or "", row["next_action"] or "", row["platform"], *row["flags"], *row["reasons"]]
        assert not [text for text in shown if "—" in text or "–" in text]


def test_the_greeting_counts_every_lead_not_just_the_first_three(desk):
    from chanakya.app import open_session
    from chanakya.db import Opportunity

    with open_session(desk) as session:
        for index in range(7):
            session.add(Opportunity(
                id=f"CHK-GREET{index}", dedup_key=f"tid:greet{index}", source_id="fixture", platform="Fixture portal", source_record_key=f"k{index}",
                title=f"Synthetic notice {index}", status="active", priority="P1" if index < 4 else "P2",
                relevance_score=80 - index, content_hash=f"h{index}",
            ))
        session.commit()
    with TestClient(create_app()) as client:
        greeting = client.get("/api/brief").json()["vira"]["greeting"]
    assert "7 are worth a decision now: 4 in P1 and 3 in P2." in greeting


def test_watch_refuses_a_second_writer(desk, tmp_path, monkeypatch):
    from chanakya.desklock import acquire, read_lock

    desk.llm.mode = "disabled"  # a real desk: the watch is allowed
    path = tmp_path / "watch.lock"
    monkeypatch.setattr(server, "lock_path", lambda settings: path)
    spawned: list[int] = []

    def fake_spawn(root, pause_s=600, retry_s=30, config=None):
        acquire(path, {
            "pid": os.getpid(), "desk": "live", "mode": "watch",
            "started_at": "2026-10-10T00:00:00Z", "pause_s": pause_s, "retry_s": retry_s,
        })
        spawned.append(pause_s)
        assert config == desk.config_path  # the watch is pinned to the desk's own config
        return os.getpid()

    monkeypatch.setattr(server, "spawn_watch", fake_spawn)
    with TestClient(create_app()) as client:
        assert client.get("/api/watch").json()["running"] is False
        started = client.post("/api/watch", json={"action": "start"})
        assert started.status_code == 200, started.text
        assert spawned == [600]
        status = client.get("/api/watch").json()
        assert status["running"] is True
        assert status["mode"] == "watch"
        assert client.post("/api/watch", json={"action": "start"}).status_code == 409
        assert client.post("/api/watch", json={"action": "pause"}).status_code == 400
        assert client.post("/api/analyze", json={"scope": "changed"}).status_code == 409
        assert client.post("/api/runs").status_code == 409
        stopped = client.post("/api/watch", json={"action": "stop"})
        assert stopped.status_code == 200
        assert read_lock(path)["stop"] is True


def test_a_demo_desk_has_nothing_to_watch(desk):
    with TestClient(create_app()) as client:
        refused = client.post("/api/watch", json={"action": "start"})
        assert refused.status_code == 400
        assert client.get("/api/watch").json()["running"] is False


def test_analyze_does_not_open_a_portal(desk, monkeypatch):
    from chanakya.app import RunResult
    from chanakya.db import ScanRun
    from chanakya.pipeline.runner import RunStats

    seen: dict = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        run = ScanRun(id="run_analyze_test", status="ok", trigger="analyze", stats={"assessed": 1})
        return RunResult(run, RunStats())

    monkeypatch.setattr(server, "run_pipeline", fake)
    with TestClient(create_app()) as client:
        started = client.post("/api/analyze", json={"scope": "changed"})
        assert started.status_code == 200, started.text
        job = _wait_for_job(client, pause=0.05)
        assert job["status"] == "ok", job
        assert seen["skip_scan"] is True
        assert seen["export"] is False
        assert seen["trigger"] == "analyze"
        assert client.post("/api/analyze", json={"scope": "everything"}).status_code == 400


def test_the_desk_reads_the_config_named_in_the_environment(monkeypatch):
    from chanakya.server import desk_settings, is_demo, project_root

    monkeypatch.setenv("CHANAKYA_CONFIG", str(project_root() / "config" / "chanakya.demo.yaml"))
    assert is_demo(desk_settings())
    monkeypatch.delenv("CHANAKYA_CONFIG")
    assert desk_settings().config_path == project_root() / "config" / "chanakya.yaml"


def test_stop_before_a_source_leaves_a_partial_run(settings):
    from chanakya.app import open_session, run_pipeline

    write_records(settings.sources[0].path, sample_records())
    with open_session(settings) as session:
        result = run_pipeline(
            settings, session, email=False, export=False, trigger="watch",
            should_stop=lambda: True,
        )
        assert result.run.status == "partial"
        assert result.run.error and "stopped before the list was finished" in result.run.error
        assert result.stats.assessed == 0


def test_a_run_left_running_by_a_dead_process_is_closed_and_shown_stopped(settings):
    from sqlalchemy import select

    from chanakya.app import open_session
    from chanakya.db import ScanRun, SourceScanRun
    from chanakya.pipeline.runner import ORPHAN_ERROR, close_orphaned_runs
    from chanakya.server import _run_row

    with open_session(settings) as session:
        session.add(ScanRun(id="run_orphan", status="running", trigger="watch", stats={}))
        session.add(SourceScanRun(run_id="run_orphan", source_id="fixture"))
        session.commit()
        run = session.get(ScanRun, "run_orphan")
        # Nobody holds the lock: the desk must not show it as still running.
        shown = _run_row(run, alive=False)
        assert shown["status"] == "stopped" and shown["error"] == ORPHAN_ERROR
        assert _run_row(run, alive=True)["status"] == "running"
        # The next writer closes it for good.
        assert close_orphaned_runs(session) == 1
        assert run.status == "partial" and run.error == ORPHAN_ERROR and run.finished_at is not None
        assert session.scalar(select(SourceScanRun.status)) == "partial"
        assert close_orphaned_runs(session) == 0


def test_run_row_reports_the_budget_cap_and_priority_bands():
    from chanakya.db import ScanRun
    from chanakya.server import _run_row

    row = _run_row(ScanRun(id="r", status="ok", trigger="analyze", stats={
        "p1": 12, "p2": 37, "p3": 78, "p4": 4777, "llm_skipped_budget": 136, "deep_deferred": 21,
        "budget_blocked_reasons": ["max_calls_per_run (60) reached"],
    }))
    assert row["priorities"] == {"p1": 12, "p2": 37, "p3": 78, "p4": 4777}
    assert row["llm_skipped_budget"] == 136 and row["deep_deferred"] == 21
    assert row["budget_blocked_reasons"] == ["max_calls_per_run (60) reached"]
    # Runs recorded before the bands were counted say so instead of showing zeros.
    assert _run_row(ScanRun(id="old", status="ok", trigger="watch", stats={}))["priorities"] is None
