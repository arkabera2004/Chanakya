from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from chanakya.config import EmailSettings, LLMSettings, ModelPrice, Settings, SourceConfig, load_settings
from chanakya.db import init_db, make_engine, session_factory
from chanakya.pipeline.knowledge import load_knowledge_base
from chanakya.timeutil import now_ist

from .dbsupport import isolated_database

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


_frozen_now: datetime | None = None  # set per-test by _freeze_relative_dates below


def ist(days: float = 0, hours: float = 0) -> str:
    """Portal-style IST timestamp relative to now (keeps tests date-independent).

    Uses the per-test frozen instant (see ``_freeze_relative_dates``) when one is active, so
    repeated calls within the same test — e.g. test_followup_alerts.py calling
    ``sample_records()`` on every ``rescan()`` — always produce the identical string. Before
    this fix, two calls to ``ist()`` straddling a real wall-clock minute boundary (this format
    has no seconds) could disagree by a minute, which ``diff_changes`` correctly treats as a
    genuine ``deadline_changed`` event — a spurious change from the test's own clock drift, not
    a production defect. Reproduced directly: forcing a one-minute jump between two rescan()
    calls reproduced the exact "4 alerts instead of 0" failure this was causing intermittently.
    """
    base = _frozen_now if _frozen_now is not None else now_ist()
    return (base + timedelta(days=days, hours=hours)).strftime("%d-%b-%Y %I:%M %p")


@pytest.fixture(autouse=True)
def _freeze_relative_dates():
    """Every test gets one stable reference instant for ``ist()``, so fixture dates never
    drift mid-test regardless of how long the test takes to run or how many times it rebuilds
    the sample records."""
    global _frozen_now
    _frozen_now = now_ist()
    try:
        yield
    finally:
        _frozen_now = None


@pytest.fixture
def kb():
    return load_knowledge_base(ROOT / "config" / "wayam_capabilities.yaml")


@pytest.fixture
def example_settings() -> Settings:
    return load_settings(ROOT / "config" / "chanakya.example.yaml")


@pytest.fixture
def database_url(tmp_path):
    """An empty database for one test: SQLite by default, PostgreSQL when CHANAKYA_TEST_DATABASE_URL is set."""
    with isolated_database(tmp_path) as url:
        yield url


@pytest.fixture
def settings(tmp_path, example_settings, database_url) -> Settings:
    s = example_settings.model_copy(deep=True)
    s.app.data_dir = tmp_path / "data"
    s.app.database_url = database_url
    s.app.reports_dir = tmp_path / "reports"
    s.app.inbox_dir = tmp_path / "inbox"
    s.app.capabilities_file = ROOT / "config" / "wayam_capabilities.yaml"
    s.email = EmailSettings(enabled=True, provider="file", to=["bd@example.com"], cc=["cto@example.com"],
                            outbox_dir=tmp_path / "outbox", min_score=40)
    s.llm = LLMSettings(mode="fake")
    s.pricing = {"gpt-oss:20b": ModelPrice(input=0.07, cached_input=0.035, output=0.30),
                 "gpt-oss:120b": ModelPrice(input=0.15, cached_input=0.014, output=0.60)}
    s.http.min_interval_s = 0
    s.sources = [SourceConfig(id="fixture", type="fixture", path=tmp_path / "src", platform_label="Fixture portal",
                              verification_status="verified_working")]
    (tmp_path / "src").mkdir()
    return s


@pytest.fixture
def session(settings):
    engine = make_engine(settings.app.database_url)
    init_db(engine)
    s = session_factory(engine)()
    yield s
    s.close()
    engine.dispose()


def write_records(directory: Path, records: list[dict], name: str = "records.yaml") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text(yaml.safe_dump({"records": records}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def sample_records() -> list[dict]:
    """SYNTHETIC records with dates relative to now."""
    return [
        {"tender_id": "2026_SYNSC_000101_1", "ref_no": "SYN/SC/VA/01", "title": "Implementation of AI-based video analytics on existing CCTV cameras for Smart City ICCC",
         "organisation": "Synthetic Smart City Ltd", "category": "Information Technology (IT)", "published_at": ist(-1),
         "closing_at": ist(25), "estimated_value_inr": 45000000,
         "description": "Video analytics software for 450 existing RTSP cameras: crowd density, intrusion detection, abandoned object "
                        "alerts and vehicle classification integrated with the ICCC. Average annual turnover of Rs. 10 crore in the last "
                        "three financial years. Bidder must be ISO 27001 certified. Consortium is not allowed."},
        {"tender_id": "2026_SYNPWD_000102_1", "ref_no": "SYN/PWD/ROAD/02", "title": "Road resurfacing work with traffic monitoring signboards",
         "organisation": "Synthetic PWD", "published_at": ist(-1), "closing_at": ist(15)},
        {"tender_id": "2026_SYNHLT_000103_1", "ref_no": "SYN/HLT/CALL/03", "title": "Speech analytics and quality monitoring for health helpline call centre",
         "organisation": "Synthetic Health Department", "published_at": ist(-2), "closing_at": ist(4)},
        {"tender_id": "2026_SYNREV_000104_1", "ref_no": "SYN/REV/DOC/04", "title": "Selection of agency for AI/ML based automated scrutiny of building plan applications",
         "organisation": "Synthetic Urban Development Authority", "published_at": ist(-2), "closing_at": ist(20)},
        {"tender_id": "2026_SYNOLD_000105_1", "ref_no": "SYN/OLD/05", "title": "Comprehensive VAPT of departmental web applications",
         "organisation": "Synthetic Finance Department", "published_at": ist(-20), "closing_at": ist(-3)},
        {"tender_id": "2026_SYNCAT_000106_1", "ref_no": "SYN/CAT/06", "title": "Catering services for hostel mess",
         "organisation": "Synthetic University", "published_at": ist(-1), "closing_at": ist(10)},
    ]
