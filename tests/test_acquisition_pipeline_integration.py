"""Integration: an acquisition job's TenderRecords flow through the existing pipeline —
the same dedup_key/upsert_record path CPPP/GePNIC connectors already use, not a second
normalization system (see docs/CRAWLING_TARGET_ARCHITECTURE.md).
"""

from __future__ import annotations

import pytest

from chanakya.acquisition.contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability
from chanakya.acquisition.job_manager import JobManager
from chanakya.acquisition.registry import ProviderRegistry
from chanakya.config import AcquisitionProvidersSettings, ScraplingSettings
from chanakya.db import AcquisitionJob, Opportunity, init_db, make_engine, session_factory
from chanakya.models import TenderRecord

from .dbsupport import isolated_database


def _record(**overrides) -> TenderRecord:
    base = dict(
        source_id="scrapling", platform="scrapling", source_record_key="TID-001",
        source_tender_id="TID-001", title="Supply of Widgets", organisation="Synthetic Dept",
    )
    base.update(overrides)
    return TenderRecord(**base)


class _RecordingProvider(AcquisitionProvider):
    provider_id = "scrapling"
    capabilities = frozenset({ProviderCapability.STRUCTURED_EXTRACTION})
    requires_credentials = False

    def __init__(self, settings, records: list[TenderRecord]) -> None:
        super().__init__(settings)
        self._records = records

    def is_configured(self) -> bool:
        return True

    def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
        return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="ok", records=self._records)


@pytest.fixture
def db(tmp_path):
    """SQLite by default; a real Postgres schema when CHANAKYA_TEST_DATABASE_URL is set."""
    with isolated_database(tmp_path) as url:
        engine = make_engine(url)
        init_db(engine)
        yield session_factory(engine)


def _manager(sf, records: list[TenderRecord], monkeypatch) -> JobManager:
    settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    registry = ProviderRegistry()
    registry._cache[id(settings)] = {"scrapling": _RecordingProvider(settings.scrapling, records)}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)
    return JobManager(sf, settings, sleep=lambda s: None)


def test_a_successful_job_stores_a_real_opportunity_with_dedup_key(db, monkeypatch):
    jm = _manager(db, [_record()], monkeypatch)
    job = jm.create_job(provider_id="scrapling", url="https://x/", source_id="scrapling")
    jm._run(job.id)

    with db() as s:
        row = s.get(AcquisitionJob, job.id)
        assert row.status == "succeeded" and row.record_count == 1
        opps = s.query(Opportunity).all()
        assert len(opps) == 1
        assert opps[0].title == "Supply of Widgets"
        assert opps[0].dedup_key  # the same key CPPP/GePNIC connectors produce


def test_resubmitting_the_same_tender_does_not_create_a_duplicate_opportunity(db, monkeypatch):
    jm = _manager(db, [_record()], monkeypatch)
    job1 = jm.create_job(provider_id="scrapling", url="https://x/", source_id="scrapling", dedupe_pending=False)
    jm._run(job1.id)
    job2 = jm.create_job(provider_id="scrapling", url="https://x/", source_id="scrapling", dedupe_pending=False)
    jm._run(job2.id)

    with db() as s:
        opps = s.query(Opportunity).all()
        assert len(opps) == 1, "the pipeline's own dedup_key must prevent a duplicate row, not just the job-level dedup"


def test_a_partial_job_is_distinguished_from_a_fully_succeeded_one(db, monkeypatch):
    good = _record()
    # A record whose required 'title' is present but whose storage will be monkeypatched to fail,
    # proving a per-record storage error surfaces as 'partial', not a silent 'succeeded'.
    jm = _manager(db, [good, good], monkeypatch)

    import chanakya.pipeline.store as store_mod

    calls = {"n": 0}
    real_upsert = store_mod.upsert_record

    def flaky_upsert(session, rec, run_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated storage failure")
        return real_upsert(session, rec, run_id)

    monkeypatch.setattr(store_mod, "upsert_record", flaky_upsert)

    job = jm.create_job(provider_id="scrapling", url="https://x/", source_id="scrapling")
    jm._run(job.id)

    with db() as s:
        row = s.get(AcquisitionJob, job.id)
        assert row.status == "partial"
        assert row.diagnostics["stored"]["errors"] == 1
        assert row.diagnostics["stored"]["count"] == 1
