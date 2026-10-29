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

from .dbsupport import isolated_database


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
    """SQLite by default; a real Postgres schema when CHANAKYA_TEST_DATABASE_URL is set
    (see tests/dbsupport.py) — same convention every other test file uses, so the job
    manager's own tests get Postgres parity coverage too, not just the connector tests."""
    with isolated_database(tmp_path) as url:
        engine = make_engine(url)
        init_db(engine)
        yield session_factory(engine)


def _manager_with_stub(sf, outcomes=None, *, monkeypatch=None) -> tuple[JobManager, _StubProvider]:
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    stub = _StubProvider(settings.scrapling, outcomes)
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}  # pre-seed instead of the real loader
    jm = JobManager(sf, settings, max_attempts=3, sleep=lambda s: None)
    import chanakya.acquisition.job_manager as mod

    assert monkeypatch is not None, "pass the test's monkeypatch fixture"
    monkeypatch.setattr(mod, "registry", registry)
    # These tests exercise job-lifecycle behavior with a stub provider returning
    # "https://x/" results — not real SSRF validation (covered by
    # test_acquisition_crawlee.py / test_acquisition_scrapy.py), so the redirect-allowlist
    # reclassification added for that is turned off here.
    monkeypatch.setattr(mod, "_reclassify_offallowlist_redirects", lambda result, request: result)
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
    # These tests exercise job-lifecycle behavior with a stub provider returning
    # "https://x/" results — not real SSRF validation (covered by
    # test_acquisition_crawlee.py / test_acquisition_scrapy.py), so the redirect-allowlist
    # reclassification added for that is turned off here.
    monkeypatch.setattr(mod, "_reclassify_offallowlist_redirects", lambda result, request: result)
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


def test_retry_backoff_is_jittered_and_bounded(db, monkeypatch):
    """Full-jitter backoff (uniform(0, 2**attempt capped at 10)) avoids retry storms across
    jobs when a provider is overloaded — assert the sleep durations are bounded and not a
    fixed constant, rather than re-asserting the exact formula."""
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    stub = _StubProvider(settings.scrapling, outcomes=[
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="failed", error="transient", retryable=True),
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="ok"),
    ])
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    # These tests exercise job-lifecycle behavior with a stub provider returning
    # "https://x/" results — not real SSRF validation (covered by
    # test_acquisition_crawlee.py / test_acquisition_scrapy.py), so the redirect-allowlist
    # reclassification added for that is turned off here.
    monkeypatch.setattr(mod, "_reclassify_offallowlist_redirects", lambda result, request: result)
    sleeps: list[float] = []
    jm = JobManager(db, settings, max_attempts=3, sleep=sleeps.append)

    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    _wait_terminal(db, job.id)

    assert sleeps and all(0 <= s <= 10 for s in sleeps)


def test_retry_after_from_a_retryable_result_is_honored_as_a_backoff_floor(db, monkeypatch):
    """A provider (e.g. Apify on a 429) can report diagnostics["retry_after_s"] — the
    server's own explicit guidance for how long to wait before retrying. job_manager
    currently ignores it entirely and always uses its own jittered backoff (bounded at 10s),
    which can retry well before the server asked it to."""
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    stub = _StubProvider(settings.scrapling, outcomes=[
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="failed", error="rate limited",
                           retryable=True, diagnostics={"retry_after_s": 37.0}),
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="ok"),
    ])
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    monkeypatch.setattr(mod, "_reclassify_offallowlist_redirects", lambda result, request: result)
    sleeps: list[float] = []
    jm = JobManager(db, settings, max_attempts=3, sleep=sleeps.append)

    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    _wait_terminal(db, job.id)

    assert sleeps and sleeps[0] >= 37.0, f"expected the backoff to honor retry_after_s=37, got {sleeps[0]}"


def test_retry_after_is_capped_against_a_bad_or_malicious_value(db, monkeypatch):
    """A provider-reported retry_after_s is still caller-influenced data (ultimately coming
    from a response header) — an absurd value must not stall this thread indefinitely."""
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    stub = _StubProvider(settings.scrapling, outcomes=[
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="failed", error="rate limited",
                           retryable=True, diagnostics={"retry_after_s": 1_000_000.0}),
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="ok"),
    ])
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    monkeypatch.setattr(mod, "_reclassify_offallowlist_redirects", lambda result, request: result)
    sleeps: list[float] = []
    jm = JobManager(db, settings, max_attempts=3, sleep=sleeps.append)

    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    _wait_terminal(db, job.id)

    assert sleeps and sleeps[0] <= 120, f"expected the backoff to be capped at 120s, got {sleeps[0]}"


def test_a_200_ok_captcha_page_is_reclassified_as_blocked(db, monkeypatch):
    """A provider can get a clean 200 whose body is a CAPTCHA challenge, not real content —
    the job manager must not report this as a successful acquisition."""
    jm, stub = _manager_with_stub(db, monkeypatch=monkeypatch, outcomes=[
        AcquisitionResult(
            provider_id="scrapling", url="https://x/", status="ok",
            raw_content='<html><body><img alt="captcha" src="/captcha.png"></body></html>',
            content_type="text/html",
        ),
    ])
    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    row = _wait_terminal(db, job.id)
    assert row.status == "failed" and row.retryable is False
    assert row.diagnostics["blocked_reason"] == "captcha_in_body"


def test_a_result_from_an_off_allowlist_redirect_is_reclassified_as_blocked(db, monkeypatch):
    """A provider's own library can follow a redirect to a host this project never approved
    for the request (only PoliteClient re-validates every hop itself) — the job manager must
    not trust or store content that ended up coming from an off-allowlist host, even though
    it cannot un-send the outbound request that already happened."""
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    stub = _StubProvider(settings.scrapling, [
        AcquisitionResult(provider_id="scrapling", url="https://169.254.169.254/latest/meta-data/", status="ok",
                          raw_content="internal metadata", content_type="text/plain"),
    ])
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)  # intentionally NOT bypassing _reclassify_offallowlist_redirects
    jm = JobManager(db, settings, sleep=lambda s: None)

    job = jm.create_job(provider_id="scrapling", url="https://eprocure.gov.in/x")
    jm.submit(job.id)
    row = _wait_terminal(db, job.id)
    assert row.status == "failed" and row.retryable is False
    assert row.diagnostics["blocked_reason"] == "redirect_off_allowlist"
    assert row.record_count in (0, None)


def test_a_result_from_an_allowed_host_is_not_blocked(db, monkeypatch):
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    stub = _StubProvider(settings.scrapling, [
        AcquisitionResult(provider_id="scrapling", url="https://eprocure.gov.in/tender/1", status="ok"),
    ])
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    jm = JobManager(db, settings, sleep=lambda s: None)

    job = jm.create_job(provider_id="scrapling", url="https://eprocure.gov.in/x")
    jm.submit(job.id)
    row = _wait_terminal(db, job.id)
    assert row.status == "succeeded"


def test_blocked_result_is_never_retried(db, monkeypatch):
    jm, stub = _manager_with_stub(db, monkeypatch=monkeypatch, outcomes=[
        AcquisitionResult(provider_id="scrapling", url="https://x/", status="blocked", error="captcha"),
    ])
    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    row = _wait_terminal(db, job.id)
    assert row.status == "failed" and row.retryable is False and stub.calls == 1


def test_a_run_id_stashed_on_request_extra_survives_the_managers_own_retry_loop(db, monkeypatch):
    """Mirrors Apify's run-resumption pattern at the job_manager level (not just directly on
    the provider): a stub that behaves like Apify — stores an id on request.extra on its
    first call, and asserts on later calls that the id is still there — proves job_manager's
    retry loop really does reuse the SAME request object across attempts, not a fresh one."""
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))

    class _StashesRunIdProvider(AcquisitionProvider):
        provider_id = "scrapling"
        capabilities = frozenset({ProviderCapability.HTTP_FETCH})
        requires_credentials = False

        def __init__(self, settings) -> None:
            super().__init__(settings)
            self.calls = 0

        def is_configured(self) -> bool:
            return True

        def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
            self.calls += 1
            if self.calls == 1:
                assert "run_id" not in request.extra
                request.extra["run_id"] = "run-abc"
                return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="failed",
                                          error="still running", retryable=True)
            assert request.extra.get("run_id") == "run-abc", "the retry must see the run id the first attempt stashed"
            return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="ok")

    stub = _StashesRunIdProvider(settings.scrapling)
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    monkeypatch.setattr(mod, "_reclassify_offallowlist_redirects", lambda result, request: result)
    jm = JobManager(db, settings, max_attempts=3, sleep=lambda s: None)

    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    row = _wait_terminal(db, job.id)
    assert row.status == "succeeded" and stub.calls == 2


def test_retry_creates_a_job_with_a_fresh_extra_not_the_failed_jobs_run_id(db, monkeypatch):
    """job_manager.retry() creates an entirely new AcquisitionJob row (a deliberate restart,
    not an in-flight retry) — its extra must start empty, never inheriting a run id a prior,
    now-failed job had stashed, which would be a different job's remote state."""
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))

    class _RecordsExtraSeenProvider(AcquisitionProvider):
        provider_id = "scrapling"
        capabilities = frozenset({ProviderCapability.HTTP_FETCH})
        requires_credentials = False

        def __init__(self, settings) -> None:
            super().__init__(settings)
            self.seen_extras: list[dict] = []

        def is_configured(self) -> bool:
            return True

        def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
            self.seen_extras.append(dict(request.extra))
            if len(self.seen_extras) == 1:
                request.extra["run_id"] = "run-from-first-job"
                return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="failed",
                                          error="permanent", retryable=True)
            return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="ok")

    stub = _RecordsExtraSeenProvider(settings.scrapling)
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    monkeypatch.setattr(mod, "_reclassify_offallowlist_redirects", lambda result, request: result)
    jm = JobManager(db, settings, max_attempts=1, sleep=lambda s: None)  # exhaust after one attempt

    job = jm.create_job(provider_id="scrapling", url="https://x/")
    jm.submit(job.id)
    _wait_terminal(db, job.id)

    retried = jm.retry(job.id)
    assert retried is not None
    jm.submit(retried.id)
    _wait_terminal(db, retried.id)

    assert stub.seen_extras[1] == {}, "the retried job's request must not inherit the failed job's run id"


def test_cancel_requested_mid_run_is_not_reported_as_succeeded(db, monkeypatch):
    """job_manager only checks cancel_requested before the first attempt and after the
    provider's blocking fetch() call returns — never during it (confirmed by inspection:
    there is no mechanism to interrupt a provider mid-call). This test exercises the
    after-fetch-returns check: a cancellation that arrives while the provider call is in
    flight (simulated by setting cancel_requested from inside fetch() itself, the only
    deterministic way to land in that exact window) must still end the job 'cancelled', even
    though the provider's own result was a genuine success — a cancelled job must never be
    reported as succeeded, and a would-be-successful result must not be silently stored."""
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))

    class _CancelsMidFlightProvider(AcquisitionProvider):
        provider_id = "scrapling"
        capabilities = frozenset({ProviderCapability.HTTP_FETCH})
        requires_credentials = False

        def __init__(self, settings, job_id_holder: dict, sf) -> None:
            super().__init__(settings)
            self._job_id_holder = job_id_holder
            self._sf = sf

        def is_configured(self) -> bool:
            return True

        def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
            with self._sf() as s:
                row = s.get(AcquisitionJob, self._job_id_holder["id"])
                row.cancel_requested = True
                s.commit()
            return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="ok")

    job_id_holder: dict = {}
    stub = _CancelsMidFlightProvider(settings.scrapling, job_id_holder, db)
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    jm = JobManager(db, settings, sleep=lambda s: None)

    job = jm.create_job(provider_id="scrapling", url="https://x/")
    job_id_holder["id"] = job.id
    jm._run(job.id)

    with db() as s:
        row = s.get(AcquisitionJob, job.id)
        assert row.status == "cancelled", "a mid-run cancellation must never be reported as succeeded"
        assert row.record_count in (0, None), "a cancelled job's would-be result must not be stored"


def test_mid_run_cancellation_reaches_the_providers_remote_cancel_hook(db, monkeypatch):
    """A provider that owns remote state (e.g. an Apify Actor run) needs a chance to stop it
    when a job is cancelled mid-run. job_manager currently only ever flips a local DB flag —
    it never calls AcquisitionProvider.cancel_remote_run(). This proves cancel_remote_run is
    invoked with the same request the job ran, once cancellation is detected after fetch()
    returns."""
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))

    class _CancelsMidFlightProvider(AcquisitionProvider):
        provider_id = "scrapling"
        capabilities = frozenset({ProviderCapability.HTTP_FETCH})
        requires_credentials = False

        def __init__(self, settings, job_id_holder: dict, sf) -> None:
            super().__init__(settings)
            self._job_id_holder = job_id_holder
            self._sf = sf
            self.cancel_remote_run_calls = 0

        def is_configured(self) -> bool:
            return True

        def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
            with self._sf() as s:
                row = s.get(AcquisitionJob, self._job_id_holder["id"])
                row.cancel_requested = True
                s.commit()
            return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="ok")

        def cancel_remote_run(self, request: AcquisitionRequest) -> bool | None:
            self.cancel_remote_run_calls += 1
            return True

    job_id_holder: dict = {}
    stub = _CancelsMidFlightProvider(settings.scrapling, job_id_holder, db)
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": stub}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    jm = JobManager(db, settings, sleep=lambda s: None)

    job = jm.create_job(provider_id="scrapling", url="https://x/")
    job_id_holder["id"] = job.id
    jm._run(job.id)

    assert stub.cancel_remote_run_calls == 1, "a mid-run cancellation must call the provider's cancel_remote_run()"


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
    # These tests exercise job-lifecycle behavior with a stub provider returning
    # "https://x/" results — not real SSRF validation (covered by
    # test_acquisition_crawlee.py / test_acquisition_scrapy.py), so the redirect-allowlist
    # reclassification added for that is turned off here.
    monkeypatch.setattr(mod, "_reclassify_offallowlist_redirects", lambda result, request: result)

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
