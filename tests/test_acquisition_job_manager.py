"""Job manager: creation, execution, retry, cancellation, and persisted event history.

Uses the FakeProvider-style pattern (a minimal stub provider registered directly, not
through the real registry) so these tests are deterministic and don't depend on any real
provider's network behavior. The Scrapling/Firecrawl adapters already have their own tests;
this file tests the job manager's own state machine.
"""

from __future__ import annotations

import time

import pytest

from chanakya.acquisition.contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability
from chanakya.acquisition.job_manager import JobManager
from chanakya.acquisition.registry import ProviderRegistry
from chanakya.config import AcquisitionProvidersSettings, ScraplingSettings
from chanakya.db import AcquisitionJob, AcquisitionJobEvent, init_db, make_engine, session_factory


class _StubProvider(AcquisitionProvider):
    provider_id = "scrapling"  # reuse a real settings slot so AcquisitionProvidersSettings validates
    capabilities = frozenset({ProviderCapability.HTTP_FETCH})
    requires_credentials = False

    def __init__(self, settings, outcomes: list[AcquisitionResult] | None = None) -> None:
        super().__init__(settings)
        self._outcomes = list(outcomes or [])
        self.calls = 0

    def is_configured(self) -> bool:
        return self.settings.enabled

    def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
        self.calls += 1
        if self._outcomes:
            return self._outcomes.pop(0)
        return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="ok")


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db(engine)
    return session_factory(engine)


def _manager_with_stub(sf, outcomes=None, *, monkeypatch=None) -> tuple[JobManager, _StubProvider]:
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    stub = _StubProvider(settings.scrapling, outcomes)
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}  # pre-seed instead of the real loader
    jm = JobManager(sf, settings, max_attempts=3, sleep=lambda s: None)
    import chanakya.acquisition.job_manager as mod

    assert monkeypatch is not None, "pass the test's monkeypatch fixture"
    monkeypatch.setattr(mod, "registry", registry)
    return jm, stub


def _wait_terminal(sf, job_id: str, tries: int = 100):
    for _ in range(tries):
        with sf() as s:
            row = s.get(AcquisitionJob, job_id)
            if row.status in ("succeeded", "partial", "failed", "cancelled"):
                return row
        time.sleep(0.02)
    raise AssertionError("job did not reach a terminal status")


def test_create_job_for_unknown_provider_fails_immediately(db, monkeypatch):
    jm, _ = _manager_with_stub(db, monkeypatch=monkeypatch)
    job = jm.create_job(provider_id="not-a-real-provider", url="https://x/")
    assert job.status == "failed"
    assert "unknown acquisition provider" in job.error


def test_create_job_for_disabled_provider_fails_without_running(db, monkeypatch):
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=False))
    stub = _StubProvider(settings.scrapling)
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    jm = JobManager(db, settings)
    job = jm.create_job(provider_id="scrapling", url="https://x/")
    assert job.status == "failed"
    assert stub.calls == 0


def test_successful_job_persists_events_and_status(db, monkeypatch):
    jm, stub = _manager_with_stub(db, monkeypatch=monkeypatch)
    job = jm.create_job(provider_id="scrapling", url="https://x/")
    assert jm.submit(job.id) is True
    row = _wait_terminal(db, job.id)
    assert row.status == "succeeded"
    with db() as s:
        kinds = [e.kind for e in s.query(AcquisitionJobEvent).filter_by(job_id=job.id).order_by(AcquisitionJobEvent.id)]
    assert kinds == ["created", "started", "succeeded"]


def test_duplicate_pending_job_is_reused_not_duplicated(db, monkeypatch):
    jm, stub = _manager_with_stub(db, monkeypatch=monkeypatch, outcomes=[AcquisitionResult(provider_id="scrapling", url="https://x/", status="ok")])
    job1 = jm.create_job(provider_id="scrapling", url="https://x/")
    job2 = jm.create_job(provider_id="scrapling", url="https://x/")
    assert job1.id == job2.id


def test_retryable_failure_retries_then_succeeds(db, monkeypatch):
    jm, stub = _manager_with_stub(db, monkeypatch=monkeypatch, outcomes=[
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="failed", error="transient", retryable=True),
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="ok"),
    ])
    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    row = _wait_terminal(db, job.id)
    assert row.status == "succeeded" and row.attempt == 2 and stub.calls == 2


def test_non_retryable_failure_stops_after_one_attempt(db, monkeypatch):
    jm, stub = _manager_with_stub(db, monkeypatch=monkeypatch, outcomes=[
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="failed", error="permanent", retryable=False),
    ])
    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    row = _wait_terminal(db, job.id)
    assert row.status == "failed" and stub.calls == 1


def test_blocked_result_is_never_retried(db, monkeypatch):
    jm, stub = _manager_with_stub(db, monkeypatch=monkeypatch, outcomes=[
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="blocked", error="captcha"),
    ])
    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    row = _wait_terminal(db, job.id)
    assert row.status == "failed" and row.retryable is False and stub.calls == 1


def test_cancel_before_run_prevents_execution(db, monkeypatch):
    jm, stub = _manager_with_stub(db, monkeypatch=monkeypatch)
    job = jm.create_job(provider_id="scrapling", url="https://x/")
    assert jm.cancel(job.id) is True
    jm._run(job.id)  # run synchronously for a deterministic assertion
    with db() as s:
        row = s.get(AcquisitionJob, job.id)
        assert row.status == "cancelled"
    assert stub.calls == 0


def test_retry_of_a_failed_retryable_job_creates_a_new_job(db, monkeypatch):
    jm, stub = _manager_with_stub(db, monkeypatch=monkeypatch, outcomes=[
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="failed", error="transient", retryable=True),
    ])
    job = jm.create_job(provider_id="scrapling", url="https://x/", dedupe_pending=False)
    jm._run(job.id)  # exhausts nothing special here; just drive to a failed state directly
    with db() as s:
        s.get(AcquisitionJob, job.id).max_attempts = 1
        s.commit()
    stub._outcomes = [AcquisitionResult(provider_id="scrapling", url="https://x/", status="failed", error="transient", retryable=True)]
    jm._run(job.id)
    with db() as s:
        row = s.get(AcquisitionJob, job.id)
        assert row.status == "failed" and row.retryable is True

    new_job = jm.retry(job.id)
    assert new_job is not None and new_job.id != job.id


def test_orphaned_running_job_is_recovered_on_manager_startup(db, monkeypatch):
    """Simulates a process crash: a job stuck at 'running' with no live thread behind it,
    then a fresh JobManager constructed against the same database (as a restarted process
    would do) must mark it failed+retryable instead of leaving it stuck forever."""
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    stub = _StubProvider(settings.scrapling)
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)

    jm1 = JobManager(db, settings)
    job = jm1.create_job(provider_id="scrapling", url="https://x/")
    with db() as s:
        row = s.get(AcquisitionJob, job.id)
        row.status, row.started_at = "running", row.created_at
        s.commit()  # simulate: the process died mid-run, this row never reached a terminal state

    JobManager(db, settings)  # a fresh manager, as a restarted process would create
    with db() as s:
        row = s.get(AcquisitionJob, job.id)
        assert row.status == "failed" and row.retryable is True
        assert "restarted" in row.error
        kinds = [e.kind for e in s.query(AcquisitionJobEvent).filter_by(job_id=job.id).order_by(AcquisitionJobEvent.id)]
    assert kinds[-1] == "failed"
    assert stub.calls == 0  # jm2 never actually ran the job — it only marked the orphan


def test_recover_orphaned_jobs_is_a_no_op_with_nothing_stuck(db, monkeypatch):
    jm, _ = _manager_with_stub(db, monkeypatch=monkeypatch)
    assert jm.recover_orphaned_jobs() == 0
