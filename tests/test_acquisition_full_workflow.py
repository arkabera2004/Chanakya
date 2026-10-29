"""Phase 6: prove an acquisition-sourced TenderRecord flows through the FULL procurement
workflow, not just storage. test_acquisition_pipeline_integration.py already proves
dedup/provenance/partial-status at the storage layer; this file proves the next stage the
brief calls out as unverified — that a job-manager-stored Opportunity (no SourceConfig entry
behind it, unlike CPPP/GePNIC scans) still reaches eligibility assessment, capability
scoring, and priority assignment through the same `Pipeline.assess()` every connector-sourced
record goes through. Uses the real knowledge base and scorer, a FakeProvider (no network/cost),
and a real isolated test database — no mocking of the assessment pipeline itself.
"""

from __future__ import annotations

from sqlalchemy import select

from chanakya.acquisition.contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability
from chanakya.acquisition.job_manager import JobManager
from chanakya.acquisition.registry import ProviderRegistry
from chanakya.config import AcquisitionProvidersSettings, ScraplingSettings
from chanakya.db import Opportunity, make_engine, session_factory
from chanakya.llm import FakeProvider
from chanakya.models import TenderRecord
from chanakya.pipeline.runner import Pipeline, RunStats

# Reuses conftest.sample_records()'s video-analytics tender verbatim — already proven elsewhere
# (test_reporting_e2e.py) to score as a strong CAP-CV-VA match, so a non-trivial scoring result
# here is evidence the real knowledge base/scorer ran, not a title crafted to game this test.
_VIDEO_ANALYTICS_RECORD = TenderRecord(
    source_id="scrapling", platform="scrapling", source_record_key="2026_SYNSC_000101_1",
    source_tender_id="2026_SYNSC_000101_1", ref_no="SYN/SC/VA/01",
    title="Implementation of AI-based video analytics on existing CCTV cameras for Smart City ICCC",
    organisation="Synthetic Smart City Ltd", category="Information Technology (IT)",
    description=(
        "Video analytics software for 450 existing RTSP cameras: crowd density, intrusion "
        "detection, abandoned object alerts and vehicle classification integrated with the ICCC."
    ),
)


class _RecordingProvider(AcquisitionProvider):
    provider_id = "scrapling"
    capabilities = frozenset({ProviderCapability.STRUCTURED_EXTRACTION})
    requires_credentials = False

    def __init__(self, provider_settings, records: list[TenderRecord]) -> None:
        super().__init__(provider_settings)
        self._records = records

    def is_configured(self) -> bool:
        return True

    def fetch(self, request: AcquisitionRequest) -> AcquisitionResult:
        return AcquisitionResult(provider_id=self.provider_id, url=request.url, status="ok", records=self._records)


def test_an_acquisition_sourced_opportunity_is_assessed_scored_and_prioritized(settings, session, monkeypatch):
    acq_settings = AcquisitionProvidersSettings(scrapling=ScraplingSettings(enabled=True))
    registry = ProviderRegistry()
    registry._cache[id(acq_settings)] = {"scrapling": _RecordingProvider(acq_settings.scrapling, [_VIDEO_ANALYTICS_RECORD])}
    import chanakya.acquisition.job_manager as mod

    monkeypatch.setattr(mod, "registry", registry)

    engine = make_engine(settings.app.database_url)
    jm = JobManager(session_factory(engine), acq_settings, sleep=lambda s: None)
    job = jm.create_job(provider_id="scrapling", url="https://x/", source_id="scrapling")
    jm._run(job.id)  # synchronous, same pattern as test_acquisition_pipeline_integration.py

    # No SourceConfig("scrapling", ...) exists in `settings.sources` — proving assess() is
    # source-agnostic, not scoped to the connector-based sources a scan() run would use.
    assert not any(s.id == "scrapling" for s in settings.sources)

    opp = session.scalar(select(Opportunity).where(Opportunity.source_tender_id == "2026_SYNSC_000101_1"))
    assert opp is not None, "the job must have stored the opportunity before assessment"
    assert opp.analysis_state == "pending"  # not yet assessed — assess() hasn't run yet

    pipe = Pipeline(settings, session, provider=FakeProvider())
    pipe.assess(run=None, stats=RunStats())
    session.commit()

    session.refresh(opp)
    assert opp.analysis_state not in ("pending", None)
    assert opp.assessed_hash == opp.content_hash
    assert opp.top_capabilities, "scoring must have run and ranked at least one capability"
    assert opp.top_capabilities[0]["id"] == "CAP-CV-VA"
    assert opp.priority in ("P1", "P2", "P3", "P4")
