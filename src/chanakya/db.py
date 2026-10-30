"""Database schema (SQLAlchemy 2.0). SQLite for the MVP, PostgreSQL in production.

Conventions:
- All DateTime columns hold naive UTC (see ``timeutil``).
- Opportunity IDs are stable: ``CHK-`` + hash of the deduplication key, so the
  same tender keeps the same ID across runs, exports and emails.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint,
    create_engine, event, func, select,
)
from sqlalchemy import String as _SAString
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .timeutil import now_utc

SCHEMA_VERSION = 1


def String(length: int | None = None):  # noqa: N802 - stands in for sqlalchemy.String in the models below
    """``VARCHAR(length)`` on SQLite, unbounded ``TEXT`` on PostgreSQL.

    SQLite never enforces the length, so portal text longer than the declared
    size has always been stored whole. PostgreSQL would reject the row. The
    length stays as documentation of what is expected, not as a constraint.
    """
    return _SAString(length).with_variant(Text(), "postgresql")


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200), default="")
    base_url: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_status: Mapped[str] = mapped_column(String(40), default="candidate")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    high_water_mark: Mapped[datetime | None] = mapped_column(DateTime)  # newest published_at seen
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime)
    health: Mapped[str] = mapped_column(String(40), default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|partial|failed
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class SourceScanRun(Base):
    __tablename__ = "source_scan_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")  # ok|failed|skipped|blocked
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    listings_seen: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    malformed_count: Mapped[int] = mapped_column(Integer, default=0)
    documents_parsed: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    coverage_note: Mapped[str | None] = mapped_column(Text)


class AcquisitionJob(Base):
    """One acquisition-provider request (see ``chanakya.acquisition``). Distinct from
    ``ScanRun``/``SourceScanRun``, which track a connector-based multi-source scan: a job is
    the unit the provider dashboard and job manager operate on — one provider, one URL, one
    outcome — and may or may not be part of a larger ``ScanRun``."""

    __tablename__ = "acquisition_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str | None] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    capability: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued|running|succeeded|partial|failed|cancelled
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class AcquisitionJobEvent(Base):
    __tablename__ = "acquisition_job_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("acquisition_jobs.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    kind: Mapped[str] = mapped_column(String(30))  # created|started|retrying|succeeded|failed|blocked|cancelled
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Website(Base):
    """A user-added site for the Website Intelligence module. Distinct from ``Source``,
    which models config-file-driven government-portal sources — a ``Website`` is created,
    edited and removed by a desk user at runtime."""

    __tablename__ = "websites"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(2000))
    category: Mapped[str] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    crawl_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_crawl_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_crawl_status: Mapped[str | None] = mapped_column(String(20))


class WebsiteCrawlJob(Base):
    """One crawl run of a ``Website``. Tracks discovery/processing counters for the crawl
    monitor; the actual crawl execution (Phase 2) observes ``cancel_requested``/
    ``pause_requested`` and updates the counters as it runs."""

    __tablename__ = "website_crawl_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    website_id: Mapped[str] = mapped_column(ForeignKey("websites.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued|running|paused|succeeded|partial|failed|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    pages_discovered: Mapped[int] = mapped_column(Integer, default=0)
    pages_processed: Mapped[int] = mapped_column(Integer, default=0)
    pages_failed: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    tenders_extracted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class RawOpportunity(Base):
    __tablename__ = "raw_opportunities"
    __table_args__ = (UniqueConstraint("source_id", "source_record_key", "content_hash"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    source_record_key: Mapped[str] = mapped_column(String(300))
    run_id: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str] = mapped_column(String(100))
    source_record_key: Mapped[str] = mapped_column(String(300))
    source_tender_id: Mapped[str | None] = mapped_column(String(120), index=True)
    ref_no: Mapped[str | None] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(Text)
    organisation: Mapped[str | None] = mapped_column(String(400))
    department: Mapped[str | None] = mapped_column(String(600))
    state: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(300))
    opportunity_type: Mapped[str] = mapped_column(String(40), default="Unknown")
    category: Mapped[str | None] = mapped_column(String(200))
    tender_category: Mapped[str | None] = mapped_column(String(60))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    closing_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    opening_at: Mapped[datetime | None] = mapped_column(DateTime)
    estimated_value_inr: Mapped[float | None] = mapped_column(Float)
    emd_inr: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    official_url: Mapped[str | None] = mapped_column(String(1000))
    detail_url: Mapped[str | None] = mapped_column(String(2000))
    detail_url_expires: Mapped[bool] = mapped_column(Boolean, default=False)
    document_refs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    has_corrigendum: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    evidence: Mapped[str | None] = mapped_column(Text)
    also_seen_on: Mapped[list[Any]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_run: Mapped[str | None] = mapped_column(String(64))
    last_changed_run: Mapped[str | None] = mapped_column(String(64))

    # Deterministic + AI assessment (latest)
    filter_decision: Mapped[str | None] = mapped_column(String(20), index=True)
    filter_reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rule_score: Mapped[float | None] = mapped_column(Float)
    semantic_score: Mapped[float | None] = mapped_column(Float)
    top_capabilities: Mapped[list[Any]] = mapped_column(JSON, default=list)
    analysis_state: Mapped[str] = mapped_column(String(30), default="pending")
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("ai_analysis_results.id"))
    assessed_kb_version: Mapped[str | None] = mapped_column(String(32))
    assessed_hash: Mapped[str | None] = mapped_column(String(64))
    relevance_score: Mapped[float | None] = mapped_column(Float, index=True)
    priority: Mapped[str | None] = mapped_column(String(4), index=True)
    eligibility_status: Mapped[str | None] = mapped_column(String(60))
    data_completeness: Mapped[float | None] = mapped_column(Float)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    intelligence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    review_status: Mapped[str] = mapped_column(String(30), default="New")

    versions: Mapped[list[OpportunityVersion]] = relationship(back_populates="opportunity", cascade="all, delete-orphan")


class OpportunityVersion(Base):
    __tablename__ = "opportunity_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(64))
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    content_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    opportunity: Mapped[Opportunity] = relationship(back_populates="versions")


class ChangeEvent(Base):
    __tablename__ = "change_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    change_type: Mapped[str] = mapped_column(String(40))  # new|deadline_changed|value_changed|corrigendum_added|status_changed|retendered|document_changed|details_changed
    field: Mapped[str | None] = mapped_column(String(60))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    material: Mapped[bool] = mapped_column(Boolean, default=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class OpportunityDocument(Base):
    __tablename__ = "opportunity_documents"
    __table_args__ = (UniqueConstraint("opportunity_id", "sha256"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    url: Mapped[str | None] = mapped_column(String(2000))
    filename: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str | None] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    pages: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[str | None] = mapped_column(String(40))
    extraction_quality: Mapped[float | None] = mapped_column(Float)
    needs_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    sections: Mapped[list[Any]] = mapped_column(JSON, default=list)
    text_path: Mapped[str | None] = mapped_column(String(1000))
    version: Mapped[int] = mapped_column(Integer, default=1)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    status: Mapped[str] = mapped_column(String(30), default="parsed")
    error: Mapped[str | None] = mapped_column(Text)


class EligibilityFindingRow(Base):
    __tablename__ = "eligibility_findings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    criterion: Mapped[str] = mapped_column(String(40))
    requirement_text: Mapped[str] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    explicit: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(10), default="rule")
    assessment: Mapped[str] = mapped_column(String(20), default="unknown")
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class CapabilityMatch(Base):
    __tablename__ = "opportunity_capability_matches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    capability_id: Mapped[str] = mapped_column(String(40), index=True)
    rule_score: Mapped[float] = mapped_column(Float, default=0.0)
    semantic_score: Mapped[float] = mapped_column(Float, default=0.0)
    combined_score: Mapped[float] = mapped_column(Float, default=0.0)
    matched_terms: Mapped[list[Any]] = mapped_column(JSON, default=list)
    llm_confirmed: Mapped[bool | None] = mapped_column(Boolean)


class AIAnalysisResult(Base):
    __tablename__ = "ai_analysis_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(32), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # triage | deep
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(20))
    kb_version: Mapped[str] = mapped_column(String(32))
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AIUsageEvent(Base):
    __tablename__ = "ai_usage_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(30))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(String(30))  # ok|invalid_output|error|timeout|auth_error|rate_limited|budget_blocked|cache_hit
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"
    __table_args__ = (UniqueConstraint("text_hash", "model"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(100))
    vector: Mapped[list[Any]] = mapped_column(JSON)


class NotificationRule(Base):
    __tablename__ = "notification_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(30))  # daily_digest|weekly_digest|p1_alert|deadline_alert|corrigendum_alert
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_score: Mapped[float] = mapped_column(Float, default=50)
    recipients: Mapped[list[Any]] = mapped_column(JSON, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EmailDelivery(Base):
    __tablename__ = "email_delivery_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("exported_reports.id"))
    run_id: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30), default="daily_digest")
    recipient: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|dry_run
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class ExportedReport(Base):
    __tablename__ = "exported_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64))
    rows: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class UserReviewFeedback(Base):
    __tablename__ = "user_review_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    review_status: Mapped[str | None] = mapped_column(String(30))
    relevance_label: Mapped[str | None] = mapped_column(String(20))  # relevant|irrelevant
    note: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Award(Base):
    """Published award-of-contract lines (roadmap Phase 7). Not opportunities: no
    foreign key to ``opportunities``; matching is done at report time by Tender ID
    or reference so awards can be stored before or after the notice is seen."""

    __tablename__ = "awards"
    __table_args__ = (UniqueConstraint("source_id", "award_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    award_key: Mapped[str] = mapped_column(String(300))  # tid:<tender id>|<bidder> or h:<hash>
    tender_id: Mapped[str | None] = mapped_column(String(120), index=True)
    ref_no: Mapped[str | None] = mapped_column(String(300), index=True)
    title: Mapped[str] = mapped_column(Text)
    organisation: Mapped[str | None] = mapped_column(String(400))
    awarded_to: Mapped[str | None] = mapped_column(String(600))  # bidder name as published
    awarded_value_inr: Mapped[float | None] = mapped_column(Float)
    award_date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    evidence: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
    cur = dbapi_conn.cursor()
    import sqlite3
    import time

    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    # A brand-new file is switched to WAL by its first connection. A second connection
    # arriving in that instant gets "database is locked" at once, whatever the busy
    # timeout says, so wait for the switch (up to 5 s) instead of failing the open.
    for attempt in range(50):
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError:
            if attempt == 49:
                raise
            time.sleep(0.1)
    cur.close()


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and ":memory:" not in url:
        from pathlib import Path
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    if url.startswith(("postgresql", "postgres://")):
        # PostgreSQL only: driver spelling, UTC session, NUL stripping (see pgsupport).
        from . import pgsupport
        pgsupport.require_driver()
        url = pgsupport.normalize_url(url)
        engine = create_engine(url, future=True, **pgsupport.engine_kwargs(url))
        pgsupport.configure_engine(engine)
        return engine
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def init_db(engine: Engine) -> None:
    """Open a database for use: create it if empty, check its revision if it is under migration control.

    Schema changes to an existing database go through ``chanakya db upgrade`` (see ``migrate``).
    """
    from .migrate import prepare_database
    prepare_database(engine)
    with Session(engine) as s:
        row = s.get(SystemSetting, "schema_version")
        if row is None:
            s.add(SystemSetting(key="schema_version", value={"version": SCHEMA_VERSION}))
            try:
                s.commit()
            except IntegrityError:  # another opener of this new database wrote the row first
                s.rollback()


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, future=True)


def spend_since(session: Session, since: datetime) -> float:
    total = session.scalar(
        select(func.coalesce(func.sum(AIUsageEvent.est_cost_usd), 0.0)).where(AIUsageEvent.created_at >= since)
    )
    return float(total or 0.0)
