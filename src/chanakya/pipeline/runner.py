"""Pipeline orchestration: scan → assess → (export, email are in reporting).

Stages are independent and idempotent:
* ``scan`` acquires and stores records (no inference). A failing source never
  corrupts others — records are committed in small batches and the failure is
  logged with reduced-coverage notes.
* ``assess`` re-evaluates only what changed: new records, materially modified
  records, records with new documents, records whose previous AI analysis was
  skipped/failed, and everything when the capability knowledge base changes.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..config import Settings, SourceConfig
from ..connectors import NETWORK_TYPES, DiscoveryContext, StructureChangedError, build_connector
from ..db import (
    CapabilityMatch, EligibilityFindingRow, Opportunity, OpportunityDocument, ScanRun, Source, SourceScanRun, spend_since,
)
from ..documents.extract import build_context, extract_document, identify_sections
from ..http import BlockedError, FetchError, PoliteClient
from ..llm.base import LLMProvider
from ..llm.budget import BudgetGuard
from ..models import (
    AnalysisState, EligibilityFinding, EligibilityStatus, EvidenceRef, FilterDecision, OpportunityType, TenderRecord,
    TenderStatus,
)
from ..security import SecurityError, safe_filename
from ..timeutil import IST, format_ist, from_storage, now_utc, start_of_ist_day_utc, to_storage
from .analyze import Analyzer, DeepOutput, TriageOutput
from .classify import RuleClassifier
from .eligibility import assess as assess_eligibility
from .eligibility import extract_requirements, overall_status
from .knowledge import KnowledgeBase, load_knowledge_base
from .scoring import ScoreInput, Scorer, completeness
from .semantic import EmbeddingSemanticScorer, LexicalSemanticScorer
from .winloss import buyer_win_count
from .store import mark_expired, upsert_record

log = logging.getLogger(__name__)

# A configured max_pages is one chunk. The scan keeps following the listing in the
# same HTTP session until the date window, the last page, or this safety ceiling.
# Hitting the ceiling does not move the high-water mark, so the next pass retries
# the unfinished window instead of skipping everything older than the first page.
_PAGE_SAFETY = 1200

SEM_NORM = 0.30  # lexical cosine at/above this counts as a full semantic match
RICH_CONTEXT_CHARS = 200
PROMPT_OVERHEAD_TOKENS = 2000  # system prompt, schema, metadata, description and capability cards


def excerpt_chars(max_input_tokens_per_call: int) -> int:
    """Characters of document excerpt that fit in one deep-analysis request."""
    return max(2000, int((max_input_tokens_per_call - PROMPT_OVERHEAD_TOKENS) * 3.2))


def new_run_id() -> str:
    return f"run_{datetime.now(IST):%Y%m%dT%H%M%S}_{secrets.token_hex(2)}"


@dataclass
class RunStats:
    sources: dict[str, dict] = field(default_factory=dict)
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates: int = 0
    malformed: int = 0
    expired_marked: int = 0
    assessed: int = 0
    rejected: int = 0
    low: int = 0
    candidates: int = 0
    strong: int = 0
    p1: int = 0
    p2: int = 0
    p3: int = 0
    p4: int = 0
    triage_calls: int = 0
    deep_calls: int = 0
    llm_cached: int = 0
    llm_skipped_budget: int = 0
    llm_failed: int = 0
    deep_deferred: int = 0
    documents_parsed: int = 0
    coverage_notes: list[str] = field(default_factory=list)
    llm_cost_usd: float = 0.0
    llm_mode: str = "disabled"
    budget_blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def record_from_opportunity(o: Opportunity) -> TenderRecord:
    try:
        otype = OpportunityType(o.opportunity_type)
    except ValueError:
        otype = OpportunityType.UNKNOWN
    return TenderRecord(source_id=o.source_id, platform=o.platform, source_record_key=o.source_record_key,
                        source_tender_id=o.source_tender_id, ref_no=o.ref_no, title=o.title,
                        organisation=o.organisation, department=o.department, category=o.category,
                        tender_category=o.tender_category, opportunity_type=otype, description=o.description)


def document_folders(settings: Settings, o: Opportunity) -> list[Path]:
    """Inbox folders that hold this notice's documents: by Tender ID, by id, by reference.

    The Tender ID and the reference come from a portal, so they are untrusted. A key is
    used as written only when it is one plain folder name, and always in the sanitised
    form ``chanakya import --tender-id`` writes. Neither can point outside the inbox.
    """
    inbox = settings.app.inbox_dir / "documents"
    keys: set[str] = set()
    for raw in (o.source_tender_id, o.id, (o.ref_no or "").replace("/", "_")):
        if not raw:
            continue
        keys.add(safe_filename(raw))
        if raw not in (".", "..") and not any(ch in raw for ch in '/\\:'):
            keys.add(raw)
    return [inbox / key for key in sorted(keys)]


def documents_for(settings: Settings, o: Opportunity) -> list[Path]:
    """Every document on disk for this notice, in the order a pass reads them.

    A pass reads the first ``budget.max_documents_per_tender`` of these. The desk lists
    the same paths, so what a person sees attached is what the pass will open.
    """
    paths: list[Path] = []
    for d in o.document_refs or []:
        if d.get("local_path"):
            p = Path(d["local_path"])
            if p.is_file():
                paths.append(p)
    for folder in document_folders(settings, o):
        if folder.is_dir():
            paths.extend(sorted(p for p in folder.iterdir() if p.is_file()))
    uniq, seen = [], set()
    for p in paths:
        if p.resolve() not in seen:
            seen.add(p.resolve())
            uniq.append(p)
    return uniq


ORPHAN_ERROR = "stopped: the process ended before this pass was closed"


def close_orphaned_runs(session: Session) -> int:
    """A run still marked running when a new writer starts was left by a process that died.

    The caller is the only writer (it holds the lock), so nothing else can own these rows.
    Stored notices stay; the row is closed as partial so the desk stops waiting on it.
    """
    now = now_utc()
    runs = session.scalars(select(ScanRun).where(ScanRun.status == "running")).all()
    for run in runs:
        run.status = "partial"
        run.finished_at = now
        run.error = run.error or ORPHAN_ERROR
    for ssr in session.scalars(select(SourceScanRun).where(SourceScanRun.status == "running")).all():
        ssr.status = "partial"
        ssr.finished_at = now
        ssr.error = ssr.error or "stopped with the process"
    if runs:
        session.commit()
    return len(runs)


class StoppedScan(Exception):
    """The watch was asked to stop. Stored notices stay; scoring waits for analyse."""


class Pipeline:
    def __init__(self, settings: Settings, session: Session, *, provider: LLMProvider | None = None,
                 http_client: PoliteClient | None = None, kb: KnowledgeBase | None = None) -> None:
        self.settings = settings
        self.session = session
        self.provider = provider
        self.client = http_client
        self.kb = kb or load_knowledge_base(settings.app.capabilities_file)
        self.classifier = RuleClassifier(self.kb, settings.filtering)
        self.semantic_note: str | None = None
        self.semantic = self._build_semantic() if settings.semantic.enabled else None
        self.scorer = Scorer(settings.scoring, self.kb)
        self.on_event = None  # optional callable(dict) — the desk narrates from these
        self.should_stop = None  # optional callable() -> bool, checked between sources and page chunks
        self.checkpoint = None  # optional callable(), run after each source and each page chunk of a scan
        self._budget: tuple[str, BudgetGuard, Analyzer | None] | None = None  # one guard per run

    def emit(self, kind: str, message: str, **data) -> None:
        """Tell a listener what just happened. Listeners must not break the run."""
        if self.on_event is None:
            return
        try:
            self.on_event({"kind": kind, "message": message, **data})
        except Exception:  # noqa: BLE001 — narration is optional
            log.exception("pipeline event listener failed")

    def _build_semantic(self):
        sem = self.settings.semantic
        if sem.backend == "ollama":
            if self.provider is not None and self.provider.mode == "local":
                from ..db import EmbeddingCache

                def cache_get(h: str, model: str):
                    row = self.session.scalar(select(EmbeddingCache).where(EmbeddingCache.text_hash == h,
                                                                           EmbeddingCache.model == model))
                    return row.vector if row else None

                def cache_put(h: str, model: str, vec: list[float]) -> None:
                    self.session.add(EmbeddingCache(text_hash=h, model=model, vector=vec))
                    self.session.flush()

                try:
                    return EmbeddingSemanticScorer(self.kb, lambda texts: self.provider.embed(texts, model=sem.embed_model),
                                                   sem.embed_model, cache_get, cache_put)
                except Exception as exc:  # noqa: BLE001 - fall back to the free lexical scorer
                    self.semantic_note = f"embedding backend unavailable ({type(exc).__name__}); using lexical semantics"
            else:
                self.semantic_note = ("semantic.backend=ollama needs llm.mode=local (no paid cloud embeddings); "
                                      "using lexical semantics")
        return LexicalSemanticScorer(self.kb)

    # ------------------------------------------------------------------ runs
    def start_run(self, trigger: str = "manual", dry_run: bool = False) -> ScanRun:
        close_orphaned_runs(self.session)
        run = ScanRun(id=new_run_id(), trigger=trigger, dry_run=dry_run, status="running", stats={})
        self.session.add(run)
        self.session.commit()
        return run

    def finish_run(self, run: ScanRun, stats: RunStats, error: str | None = None, status: str | None = None) -> None:
        failed = [s for s, v in stats.sources.items() if v.get("status") in ("failed", "blocked")]
        ok = [s for s, v in stats.sources.items() if v.get("status") == "ok"]
        if status:
            run.status = status
        elif error:
            run.status = "failed"
        elif failed and not ok and stats.sources:
            run.status = "failed"
        elif failed:
            run.status = "partial"
        else:
            run.status = "ok"
        run.finished_at = now_utc()
        run.stats = stats.to_dict()
        run.error = error
        self.session.commit()

    # ------------------------------------------------------------------ sources
    def _sync_source(self, cfg: SourceConfig) -> Source:
        src = self.session.get(Source, cfg.id)
        if src is None:
            src = Source(id=cfg.id, type=cfg.type)
            self.session.add(src)
        src.type, src.name, src.enabled = cfg.type, cfg.name or cfg.id, cfg.enabled
        src.base_url = cfg.base_url or (str(cfg.path) if cfg.path else None)
        src.verification_status = cfg.verification_status
        return src

    def scan(self, run: ScanRun, stats: RunStats, source_ids: list[str] | None = None,
             since: datetime | None = None, max_pages: int | None = None) -> bool:
        selected = [s for s in self.settings.sources if s.enabled and (not source_ids or s.id in source_ids)]
        if source_ids:
            unknown = set(source_ids) - {s.id for s in self.settings.sources}
            if unknown:
                raise ValueError(f"unknown source(s): {sorted(unknown)}")
        stopped = False
        try:
            for cfg in selected:
                if self.should_stop and self.should_stop():
                    stopped = True
                    break
                self._scan_source(run, stats, cfg, since, max_pages)
                self._run_checkpoint(run, stats)
        except StoppedScan:
            stopped = True
        if stopped:
            note = "stopped before the list was finished, so stored notices could be assessed"
            stats.coverage_notes.append(note)
            self.emit("acquire", "Stopping the watch. Stored notices stay on the desk.", status="partial")
            self.session.commit()
            return True
        stats.expired_marked += mark_expired(self.session, run.id)
        self.session.commit()
        return False

    def _scan_source(self, run: ScanRun, stats: RunStats, cfg: SourceConfig, since: datetime | None,
                     max_pages: int | None) -> None:
        src = self._sync_source(cfg)
        ssr = SourceScanRun(run_id=run.id, source_id=cfg.id)
        self.session.add(ssr)
        self.session.commit()
        t0 = time.monotonic()
        now = now_utc()
        info: dict = {"status": "running"}
        stats.sources[cfg.id] = info
        if src.cooldown_until and src.cooldown_until > now:
            ssr.status = info["status"] = "skipped"
            note = f"{cfg.id}: in cooldown until {format_ist(src.cooldown_until)} after repeated failures — coverage reduced"
            ssr.coverage_note = note
            stats.coverage_notes.append(note)
            ssr.finished_at = now_utc()
            self.session.commit()
            self.emit("acquire", f"{cfg.name or cfg.id} is cooling down after earlier failures, so I am leaving it for this pass.",
                      source=cfg.id, status="skipped")
            return
        if cfg.type in NETWORK_TYPES and self.client is None:
            ssr.status = info["status"] = "skipped"
            ssr.coverage_note = f"{cfg.id}: no HTTP client (offline mode)"
            stats.coverage_notes.append(ssr.coverage_note)
            self.session.commit()
            self.emit("acquire", f"{cfg.name or cfg.id} needs the network, and this pass is offline.",
                      source=cfg.id, status="skipped")
            return
        self.emit("acquire", f"Opening {cfg.name or cfg.id}.", source=cfg.id, status="running")
        connector = build_connector(cfg, self.client if cfg.type in NETWORK_TYPES else None)
        if since is not None:
            start = since if since.tzinfo else since.replace(tzinfo=IST)
        elif src.high_water_mark is not None:
            start = from_storage(src.high_water_mark - timedelta(hours=cfg.overlap_hours))
        else:
            start = from_storage(now - timedelta(days=self.settings.filtering.backfill_days))
        chunk = max_pages or cfg.max_pages
        ctx = DiscoveryContext(since=start, max_pages=chunk, run_id=run.id)
        newest = src.high_water_mark
        counts = {"new": 0, "updated": 0, "unchanged": 0, "duplicate": 0}
        seen = 0
        pages_done = 0
        malformed = 0
        note_bits: list[str] = []
        chunk_accounted = True
        try:
            pending: str | None = None
            seen_resume: set[str] = set()
            while True:
                if self.should_stop and self.should_stop():
                    ssr.status = info["status"] = "partial"
                    ssr.error = "stopped with the watch"
                    ssr.finished_at = now_utc()
                    ssr.pages_fetched = pages_done
                    self.session.commit()
                    raise StoppedScan()
                chunk_accounted = False
                ctx = DiscoveryContext(since=start, max_pages=chunk, run_id=run.id, start_url=pending)
                for rec in connector.discover(ctx):
                    seen += 1
                    if cfg.fetch_detail and connector.supports_detail:
                        try:
                            rec = connector.fetch_detail(rec)
                        except (FetchError, BlockedError) as exc:
                            ctx.notes.append(f"detail unavailable for {rec.source_record_key}: {exc}")
                    res = upsert_record(self.session, rec, run.id)
                    counts[res.outcome] += 1
                    if rec.published_at:
                        p = to_storage(rec.published_at)
                        newest = p if newest is None or p > newest else newest
                    if seen % 25 == 0:
                        self.session.commit()
                pages_done += ctx.pages_fetched
                malformed += ctx.malformed
                note_bits.extend(ctx.notes)
                chunk_accounted = True
                partial = bool(ctx.stopped_reason and "coverage may be partial" in ctx.stopped_reason)
                nxt = ctx.resume_url
                if partial and nxt and nxt not in seen_resume and pages_done < _PAGE_SAFETY:
                    seen_resume.add(nxt)
                    pending = nxt
                    self.session.commit()
                    self.emit(
                        "acquire",
                        f"{cfg.name or cfg.id}: still inside the date window after {pages_done} pages. Continuing.",
                        source=cfg.id, status="running", pages=pages_done,
                    )
                    self._run_checkpoint(run, stats)
                    continue
                break
            self.session.commit()
            ssr.status = info["status"] = "ok"
            src.consecutive_failures = 0
            src.cooldown_until = None
            src.last_success_at = now_utc()
            src.health = "ok"
            src.last_error = None
            if partial:
                # Newest-page watermark would skip every older notice on the next pass.
                note = f"{cfg.id}: {ctx.stopped_reason} — high-water mark left in place so the next pass continues this window"
                stats.coverage_notes.append(note)
                note_bits.append(note)
            else:
                src.high_water_mark = newest
        except BlockedError as exc:
            if not chunk_accounted:
                pages_done += ctx.pages_fetched
                malformed += ctx.malformed
                note_bits.extend(ctx.notes)
                chunk_accounted = True
            self.session.commit()  # keep what was stored before the block
            ssr.status = info["status"] = "blocked"
            ssr.error = str(exc)
            src.health = "restricted"
            src.last_error = str(exc)
            stats.coverage_notes.append(f"{cfg.id}: access restricted ({exc}) — not bypassed; use manual import")
        except (FetchError, StructureChangedError, SecurityError, OSError, ValueError) as exc:
            self.session.rollback()
            ssr = self.session.merge(ssr)
            src = self.session.get(Source, cfg.id) or src
            ssr.status = info["status"] = "failed"
            ssr.error = f"{type(exc).__name__}: {exc}"
            src.consecutive_failures = (src.consecutive_failures or 0) + 1
            src.health = "failing"
            src.last_error = ssr.error
            if src.consecutive_failures >= self.settings.http.circuit_breaker_failures:
                src.cooldown_until = now_utc() + timedelta(seconds=self.settings.http.circuit_breaker_cooldown_s)
            stats.coverage_notes.append(f"{cfg.id}: scan failed ({ssr.error}) — coverage reduced")
            if not chunk_accounted:
                pages_done += ctx.pages_fetched
                malformed += ctx.malformed
                note_bits.extend(ctx.notes)
        ssr.pages_fetched = pages_done
        ssr.listings_seen = seen
        ssr.new_count, ssr.updated_count = counts["new"], counts["updated"]
        ssr.unchanged_count, ssr.duplicate_count = counts["unchanged"], counts["duplicate"]
        ssr.malformed_count = malformed
        ssr.finished_at = now_utc()
        ssr.duration_s = round(time.monotonic() - t0, 2)
        if note_bits:
            ssr.coverage_note = "; ".join(note_bits[:20])
        info.update({"pages": pages_done, "seen": seen, **counts, "malformed": malformed,
                     "duration_s": ssr.duration_s, "error": ssr.error})
        stats.new += counts["new"]
        stats.updated += counts["updated"]
        stats.unchanged += counts["unchanged"]
        stats.duplicates += counts["duplicate"]
        stats.malformed += malformed
        self.session.commit()
        self.emit(
            "acquire",
            f"{cfg.name or cfg.id}: {seen} notices, {counts['new']} new, {counts['updated']} changed"
            + (f". {ssr.error}" if ssr.error else "."),
            source=cfg.id, status=info.get("status"), seen=seen, new=counts["new"], updated=counts["updated"],
        )

    def _run_checkpoint(self, run: ScanRun, stats: RunStats) -> None:
        """Let the caller work on what is stored so far. A long listing must not hold it back.

        Everything scanned is committed before this runs, so a failure here costs only the
        checkpoint: the scan goes on and the closing assessment picks the notices up.
        """
        if self.checkpoint is None:
            return
        try:
            # Closed notices are marked first, as at the end of a scan, so none is scored as open.
            stats.expired_marked += mark_expired(self.session, run.id)
            self.session.commit()
            self.checkpoint()
        except Exception:  # noqa: BLE001 - never blame a source for an assessment error
            self.session.rollback()
            log.exception("scan checkpoint failed")

    # ------------------------------------------------------------------ assess
    def _needs_assessment(self, scope: str, run_id: str | None,
                          only_ids: list[str] | None = None) -> list[Opportunity]:
        if only_ids is not None:
            # A person asked for these notices by id (the desk's "Analyse this notice").
            return list(self.session.scalars(select(Opportunity).where(Opportunity.id.in_(only_ids))).all())
        q = select(Opportunity)
        if scope == "new" and run_id:
            q = q.where(or_(Opportunity.first_seen_run == run_id, Opportunity.last_changed_run == run_id))
        elif scope == "changed":
            retry_states = [AnalysisState.PENDING.value, AnalysisState.SKIPPED_BUDGET.value, AnalysisState.FAILED.value]
            if self.provider is not None:
                retry_states.append(AnalysisState.SKIPPED_DISABLED.value)
            q = q.where(or_(Opportunity.assessed_hash.is_(None),
                            Opportunity.assessed_hash != Opportunity.content_hash,
                            Opportunity.assessed_kb_version.is_(None),
                            Opportunity.assessed_kb_version != self.kb.version,
                            Opportunity.analysis_state.in_(retry_states)))
        selected = list(self.session.scalars(q.order_by(Opportunity.published_at.desc().nulls_last(), Opportunity.first_seen_at.desc(), Opportunity.id.desc())).all())
        if scope == "changed":
            # Deferred analyses are revisited only when new documents have arrived for them.
            have = {o.id for o in selected}
            deferred = self.session.scalars(select(Opportunity).where(
                Opportunity.analysis_state == AnalysisState.DEFERRED.value,
                Opportunity.status == TenderStatus.ACTIVE.value)).all()
            for o in deferred:
                if o.id not in have and self._doc_fingerprint(o) != (o.intelligence or {}).get("doc_fingerprint"):
                    selected.append(o)
        return selected

    def _doc_fingerprint(self, o: Opportunity) -> str:
        parts = []
        for p in self._documents_for(o):
            try:
                st = p.stat()
                parts.append(f"{p.name}:{st.st_size}:{int(st.st_mtime)}")
            except OSError:
                continue
        return "|".join(sorted(parts))

    def _budget_for(self, run_id: str | None, stats: RunStats) -> tuple[BudgetGuard, Analyzer | None]:
        """One guard and analyser per run, so per-run limits hold across every batch of that run."""
        if self._budget is None or run_id is None or self._budget[0] != run_id:
            guard = BudgetGuard(self.settings.budget, self.settings.pricing,
                                self.provider.mode if self.provider else "disabled",
                                spent_today_before_run=spend_since(self.session, start_of_ist_day_utc()))
            analyzer = Analyzer(self.provider, self.settings, self.kb, self.session, guard, run_id) if self.provider else None
            self._budget = (run_id or "", guard, analyzer)
        stats.llm_mode = self.provider.mode if self.provider else "disabled"
        if self.semantic_note and self.semantic_note not in stats.coverage_notes:
            stats.coverage_notes.append(self.semantic_note)
        return self._budget[1], self._budget[2]

    def _assess_batch(self, opps: list[Opportunity], guard: BudgetGuard, analyzer: Analyzer | None,
                      stats: RunStats, *, stoppable: bool = False) -> None:
        # Assess the most promising first so a limited budget is spent where it matters.
        prepared = []
        for o in opps:
            rr = self.classifier.classify(record_from_opportunity(o))
            prepared.append((o, rr))
        prepared.sort(key=lambda t: t[1].best_score, reverse=True)
        for o, rr in prepared:
            if stoppable and self.should_stop and self.should_stop():
                break
            self._assess_one(o, rr, analyzer, stats)
            stats.assessed += 1
            band = (o.priority or "").lower()
            if band in ("p1", "p2", "p3", "p4"):
                setattr(stats, band, getattr(stats, band) + 1)
            self.session.commit()
            self.emit("judge", self._judge_line(o), opportunity_id=o.id, title=o.title,
                      decision=o.filter_decision, priority=o.priority,
                      why=(o.intelligence or {}).get("why_match"),
                      score=o.relevance_score)
        stats.llm_cost_usd = round(guard.run_cost, 6)
        stats.budget_blocked_reasons = guard.blocked_reasons

    def assess_stored(self, run: ScanRun | None, stats: RunStats) -> int:
        """Assess notices that are stored but not yet assessed, while a scan is still reading.

        Only never-assessed and changed notices are taken. Retries (budget, failures) and
        knowledge-base changes wait for the closing ``assess`` so they are not repeated per chunk.
        """
        opps = list(self.session.scalars(select(Opportunity).where(or_(
            Opportunity.assessed_hash.is_(None), Opportunity.assessed_hash != Opportunity.content_hash))).all())
        if not opps:
            return 0
        guard, analyzer = self._budget_for(run.id if run else None, stats)
        self.emit("match", f"Matching {len(opps)} stored notices while the list is still being read.")
        self._assess_batch(opps, guard, analyzer, stats, stoppable=True)
        return len(opps)

    def assess(self, run: ScanRun | None, stats: RunStats, scope: str = "changed",
               only_ids: list[str] | None = None) -> None:
        run_id = run.id if run else None
        guard, analyzer = self._budget_for(run_id, stats)
        opps = self._needs_assessment(scope, run_id, only_ids)
        self.emit("match", f"Matching {len(opps)} notices to Wayam's capabilities. Unrelated ones stop before any model call.")
        self._assess_batch(opps, guard, analyzer, stats)
        self.emit(
            "recommend",
            f"Pass complete. {stats.strong} strong, {stats.candidates} to review, {stats.low} weak, "
            f"{stats.rejected} set aside. Model calls: {stats.triage_calls} triage, {stats.deep_calls} deep.",
            strong=stats.strong, candidates=stats.candidates, rejected=stats.rejected,
            triage=stats.triage_calls, deep=stats.deep_calls,
        )
        if guard.unpriced_models:
            stats.coverage_notes.append(f"no price configured for models {sorted(guard.unpriced_models)} — cost recorded as 0")

    def _documents_for(self, o: Opportunity) -> list[Path]:
        return documents_for(self.settings, o)[: self.settings.budget.max_documents_per_tender]

    def _process_documents(self, o: Opportunity, stats: RunStats):
        docs = self._documents_for(o)
        sections, pages = [], []
        for path in docs:
            try:
                dt = extract_document(path, self.settings.documents, max_pages=self.settings.budget.max_pages_per_document)
            except OSError as exc:
                log.warning("cannot read %s: %s", path, exc)
                continue
            existing = self.session.scalar(select(OpportunityDocument).where(
                OpportunityDocument.opportunity_id == o.id, OpportunityDocument.sha256 == dt.sha256))
            secs = identify_sections(dt.all_pages())
            if existing is None:
                prior = self.session.scalars(select(OpportunityDocument).where(
                    OpportunityDocument.opportunity_id == o.id, OpportunityDocument.filename == dt.filename)).all()
                self.session.add(OpportunityDocument(
                    opportunity_id=o.id, filename=dt.filename, stored_path=str(path), sha256=dt.sha256, mime=dt.mime,
                    size_bytes=path.stat().st_size, pages=len(dt.all_pages()), extraction_method=dt.method,
                    extraction_quality=dt.quality, needs_manual_review=dt.needs_manual_review,
                    sections=[{"name": s.name, "heading": s.heading, "pages": [s.page_start, s.page_end],
                               "document": s.document} for s in secs],
                    version=len(prior) + 1, status="parsed" if not dt.errors else "parsed_with_errors",
                    error="; ".join(dt.errors)[:2000] or None))
                stats.documents_parsed += 1
            sections.extend(secs)
            pages.extend(dt.all_pages())
        return docs, sections, pages

    def _assess_one(self, o: Opportunity, rr, analyzer: Analyzer | None, stats: RunStats) -> None:
        intel: dict = {}
        decision = rr.decision
        rule_by_cap = {h.capability_id: h for h in rr.hits}
        sem_scores: dict[str, float] = {}
        if self.semantic and decision != FilterDecision.REJECTED:
            sem_scores = self.semantic.score(record_from_opportunity(o).text_for_matching())
        combined: dict[str, float] = {}
        for cap in self.kb.capabilities:
            r = rule_by_cap[cap.id].score if cap.id in rule_by_cap else 0.0
            sn = min(1.0, sem_scores.get(cap.id, 0.0) / SEM_NORM)
            combined[cap.id] = round(max(r, 0.65 * r + 0.35 * sn), 4)
        ranked = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)
        best_cap, best_score = ranked[0] if ranked else (None, 0.0)
        best_sem = max(sem_scores.values()) if sem_scores else 0.0
        # Similarity alone promotes a notice only when the rules saw a technology signal or a technology
        # category. Weak context words share vocabulary with almost anything, so they need a higher bar.
        sem = self.settings.semantic
        sem_bar = sem.candidate_threshold if (rr.tech_signals or rr.high_recall) \
            else max(sem.candidate_threshold, sem.weak_only_threshold)
        if decision == FilterDecision.LOW and best_sem >= sem_bar and not rr.exclusions:
            decision = FilterDecision.CANDIDATE
            rr.reasons.append(f"promoted by semantic similarity {best_sem:.2f} to {max(sem_scores, key=sem_scores.get)}")
        if decision == FilterDecision.REJECTED:
            stats.rejected += 1
        elif decision == FilterDecision.LOW:
            stats.low += 1
        elif decision == FilterDecision.CANDIDATE:
            stats.candidates += 1
        else:
            stats.strong += 1
        candidate_caps = [cid for cid, sc in ranked[:4] if sc > 0.05][:3] or ([best_cap] if best_cap else [])

        active = o.status == TenderStatus.ACTIVE.value
        docs, sections, pages = ([], [], [])
        if decision in (FilterDecision.CANDIDATE, FilterDecision.STRONG):
            docs, sections, pages = self._process_documents(o, stats)
        intel["documents_parsed"] = len(docs)
        intel["doc_fingerprint"] = self._doc_fingerprint(o)

        # ---------------- selective LLM analysis
        triage: TriageOutput | None = None
        deep: DeepOutput | None = None
        state = AnalysisState.NOT_NEEDED.value
        notes: list[str] = []
        lo, hi = self.settings.llm.triage_band
        # Every candidate is triaged: similarity can lift its combined score past the band, but without
        # a strong phrase the rules have not shown that software or analytics is what is being bought.
        wants_triage = active and (decision == FilterDecision.CANDIDATE
                                   or (decision == FilterDecision.STRONG and lo <= best_score < hi))
        if analyzer is None and active and decision in (FilterDecision.CANDIDATE, FilterDecision.STRONG):
            state = AnalysisState.SKIPPED_DISABLED.value
        if analyzer is not None and wants_triage:
            out = analyzer.triage(o, candidate_caps, best_score)
            state = self._merge_state(state, out, stats, "triage")
            notes += out.notes
            intel["injection_flags"] = out.injection_flags
            if isinstance(out.output, TriageOutput):
                triage = out.output
        excerpt, cites = build_context(sections, max_chars=excerpt_chars(self.settings.budget.max_input_tokens_per_call),
                                       fallback_pages=pages)
        rich = bool(excerpt) or len(o.description or "") >= RICH_CONTEXT_CHARS
        wants_deep = active and decision in (FilterDecision.CANDIDATE, FilterDecision.STRONG) and (
            best_score >= self.settings.llm.deep_min_score or (triage is not None and triage.relevant == "yes"
                                                               and triage.confidence >= 0.6))
        if triage is not None and triage.relevant == "no":
            wants_deep = False
        if analyzer is not None and wants_deep:
            if rich:
                out = analyzer.deep(o, candidate_caps, best_score, excerpt or None, cites)
                state = self._merge_state(state, out, stats, "deep")
                notes += out.notes
                intel["injection_flags"] = sorted(set(intel.get("injection_flags", []) + out.injection_flags))
                if isinstance(out.output, DeepOutput):
                    deep = out.output
                    o.analysis_id = out.analysis_id
            else:
                stats.deep_deferred += 1
                notes.append("deep analysis deferred: only listing-level data available (documents are behind the "
                             "portal CAPTCHA) — import documents with `chanakya import --tender-id` to enable it")
                if state in (AnalysisState.NOT_NEEDED.value, AnalysisState.DONE.value, AnalysisState.CACHED.value):
                    state = AnalysisState.DEFERRED.value

        # ---------------- eligibility
        chunks: list[tuple[str, EvidenceRef]] = []
        if o.description:
            chunks.append((o.description, EvidenceRef(source="listing", url=o.official_url, section="description")))
        for s in sections:
            if s.name in ("eligibility", "experience", "financial_qualification", "emd", "technical_requirements",
                          "contract_conditions", "evaluation"):
                chunks.append((s.text, EvidenceRef(source="document", document=s.document, page=s.page_start, section=s.heading[:80])))
        findings = extract_requirements(chunks)
        if deep:
            for r in deep.requirements:
                findings.append(EligibilityFinding(
                    criterion=r.criterion if r.criterion != "other" else "other", requirement_text=r.text,
                    explicit=r.explicit, source="llm",
                    assessment="partner" if r.criterion == "oem_authorization" else "unknown",
                    evidence=EvidenceRef(source="document" if excerpt else "listing", quote=r.evidence) if r.evidence else None))
        findings = assess_eligibility(findings, self.settings.company_profile)
        hardware = bool(rr.hardware_supply or (deep and deep.hardware_involved and deep.fit in ("partner_needed",)))
        elig = overall_status(findings, hardware_heavy=hardware) if decision != FilterDecision.REJECTED else EligibilityStatus.UNKNOWN
        self.session.execute(delete(EligibilityFindingRow).where(EligibilityFindingRow.opportunity_id == o.id))
        for f in findings:
            self.session.add(EligibilityFindingRow(opportunity_id=o.id, criterion=f.criterion,
                                                   requirement_text=f.requirement_text, value=f.value, unit=f.unit,
                                                   explicit=f.explicit, source=f.source, assessment=f.assessment,
                                                   evidence=f.evidence.model_dump() if f.evidence else None))

        # ---------------- scoring
        o.intelligence = {**(o.intelligence or {}), **intel}
        comp = completeness(o)
        sres = self.scorer.score(ScoreInput(
            filter_decision=decision, best_capability=best_cap if best_score > 0 else None,
            capability_score=best_score, eligibility=elig, status=o.status, closing_at_utc=o.closing_at,
            estimated_value_inr=o.estimated_value_inr or (deep.estimated_value_inr if deep else None),
            organisation=o.organisation, opportunity_type=o.opportunity_type,
            triage_relevant=triage.relevant if triage else None, deep_fit=deep.fit if deep else None,
            deep_hardware=deep.hardware_involved if deep else None, analysis_state=state, completeness=comp,
            hardware_supply=rr.hardware_supply,
            buyer_wins=buyer_win_count(self.session, o.organisation, exclude_id=o.id)
            if self.settings.scoring.win_history_enabled else 0))
        if intel.get("injection_flags"):
            sres.flags.append("INSTRUCTION-LIKE TEXT IN SOURCE (screened)")
        if self.provider is not None and self.provider.mode == "fake" and (triage is not None or deep is not None):
            sres.flags.append("SIMULATED AI OUTPUT (fake provider)")

        # ---------------- persist
        self.session.execute(delete(CapabilityMatch).where(CapabilityMatch.opportunity_id == o.id))
        for cid, sc in ranked[:5]:
            if sc <= 0:
                continue
            h = rule_by_cap.get(cid)
            self.session.add(CapabilityMatch(
                opportunity_id=o.id, capability_id=cid, rule_score=h.score if h else 0.0,
                semantic_score=sem_scores.get(cid, 0.0), combined_score=sc, matched_terms=h.terms if h else [],
                llm_confirmed=(cid in (deep.capability_ids if deep else triage.capability_ids if triage else []))
                if (deep or triage) else None))
        o.filter_decision = decision.value
        o.filter_reasons = rr.reasons
        o.rule_score = rr.best_score
        o.semantic_score = round(best_sem, 4)
        o.top_capabilities = [{"id": cid, "score": sc, "offering": self.kb.get(cid).offering if self.kb.get(cid) else None}
                              for cid, sc in ranked[:3] if sc > 0]
        o.relevance_score = sres.total
        o.priority = sres.priority.value
        o.eligibility_status = elig.value
        o.data_completeness = comp
        o.score_breakdown = sres.breakdown()
        o.analysis_state = state
        o.assessed_hash = o.content_hash
        o.assessed_kb_version = self.kb.version
        o.intelligence = self._intelligence(o, rr, best_cap, best_score, triage, deep, findings, elig, sres, cites,
                                            notes, intel)

    @staticmethod
    def _judge_line(o: Opportunity) -> str:
        title = (o.title or "").strip()
        title = title[:72] + ("…" if len(title) > 72 else "")
        if o.filter_decision == "rejected":
            reason = (o.filter_reasons or ["no capability match"])[0]
            return f"Set aside “{title}”. {reason}"
        why = ((o.intelligence or {}).get("why_match") or "").strip()
        return f"{o.priority or 'Unscored'} · {title}. {why[:180]}"

    @staticmethod
    def _merge_state(state: str, out, stats: RunStats, purpose: str) -> str:
        if out.state == "done":
            if purpose == "triage":
                stats.triage_calls += 1
            else:
                stats.deep_calls += 1
            return AnalysisState.DONE.value
        if out.state == "cached":
            stats.llm_cached += 1
            return AnalysisState.CACHED.value if state != AnalysisState.DONE.value else state
        if out.state == "skipped_budget":
            stats.llm_skipped_budget += 1
            return AnalysisState.SKIPPED_BUDGET.value
        if out.state == "skipped_disabled":
            return AnalysisState.SKIPPED_DISABLED.value
        stats.llm_failed += 1
        return AnalysisState.FAILED.value

    def _intelligence(self, o, rr, best_cap, best_score, triage, deep, findings, elig, sres, cites, notes, intel) -> dict:
        cap = self.kb.get(best_cap) if best_cap else None
        hit = next((h for h in rr.hits if h.capability_id == best_cap), None)
        explicit = [f.requirement_text for f in findings if f.explicit and f.criterion not in ("emd", "mse_exemption")]
        inferred = [f.requirement_text for f in findings if not f.explicit]
        blockers = [f"{f.criterion}: {f.requirement_text[:160]}" for f in findings
                    if f.assessment in ("does_not_meet", "partner", "unknown") and f.criterion not in ("emd", "consortium", "mse_exemption")]
        missing = list(deep.missing_information) if deep else []
        if not deep:
            missing.append("scope, eligibility and deliverables (tender documents not analysed)")
        if o.estimated_value_inr is None:
            missing.append("estimated value")
        prio = sres.priority.value
        portal = o.platform
        tid = o.source_tender_id or o.ref_no or o.id
        if o.status != TenderStatus.ACTIVE.value:
            action = "No action — notice is not active."
        elif prio == "P1":
            action = (f"Download the tender documents from {portal} (search Tender ID {tid}); confirm turnover, "
                      f"experience and certification criteria; decide go/no-go well before {format_ist(o.closing_at) or 'the deadline'}.")
        elif prio == "P2":
            action = f"Review the tender documents from {portal} (Tender ID {tid}) to confirm scope fit and eligibility."
        elif prio == "P3":
            action = "Monitor for corrigenda; revisit if scope details show an AI/software component."
        else:
            action = "No action."
        if elig == EligibilityStatus.PARTNER_REQUIRED and o.status == TenderStatus.ACTIVE.value and prio in ("P1", "P2"):
            action += " Identify an OEM/SI partner."
        why = []
        if cap and best_score > 0:
            terms = ", ".join((hit.strong + hit.medium)[:4]) if hit else ""
            why.append(f"{cap.name} ({cap.offering}; {cap.offering_status.replace('_', ' ')})"
                       + (f" — matched: {terms}" if terms else " — semantic similarity"))
        if triage:
            why.append(f"AI triage ({triage.relevant}, conf {triage.confidence:.2f}): {triage.reason}")
        evidence = [o.evidence] if o.evidence else []
        evidence += cites[:6]
        return {
            **intel,
            "summary": deep.scope_summary if deep and deep.scope_summary else f"Listing-level notice: {o.title}",
            "why_match": " | ".join(why) or "No Wayam capability match",
            "relevant_solution": f"{cap.offering} [{cap.id}]" if cap and best_score > 0 else None,
            "offering_status": cap.offering_status if cap and best_score > 0 else None,
            "deliverables": deep.deliverables if deep else [],
            "technologies": deep.explicit_technologies if deep else [],
            "hardware_involved": deep.hardware_involved if deep else None,
            "fit": deep.fit if deep else None,
            "explicit_requirements": explicit[:12],
            "inferred_requirements": inferred[:8],
            "eligibility_blockers": blockers[:8],
            "technical_risks": (deep.risks if deep else []) + ([] if deep else ["Scope not yet verified from documents"]),
            "missing_information": sorted(set(missing)),
            "recommended_next_action": deep.recommended_next_action if deep and deep.recommended_next_action and prio in ("P1", "P2") and o.status == "active" else action,
            "evidence": evidence,
            "confidence": round(deep.confidence if deep else (triage.confidence if triage else min(0.6, 0.2 + best_score)), 2),
            "confidence_basis": "AI deep analysis" if deep else ("AI triage" if triage else "deterministic rules only"),
            "analysis_notes": notes,
            "flags": sres.flags,
        }
