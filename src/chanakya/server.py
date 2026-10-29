"""Desk API: the web console reads the same database the pipeline writes.

One desk, one config: ``CHANAKYA_CONFIG`` if it is set, otherwise
``config/chanakya.yaml``. A pass runs on a background thread and appends a
narration the UI polls. Vira answers from stored evidence; she only calls the
model when the question is not already answered by that evidence.

To show the desk without the network or a key, start it on the synthetic
inbox: ``CHANAKYA_CONFIG=config/chanakya.demo.yaml chanakya serve``. The desk
then labels itself as demo data.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.orm import Session

from .app import open_session, run_pipeline
from .config import Settings, load_settings
from .db import (
    ChangeEvent, EligibilityFindingRow, Opportunity, ScanRun, Source, SourceScanRun, UserReviewFeedback,
)
from .desklock import (
    GEPNIC_CAPTION, RESTRICTED_PORTALS, LockHeld, acquire, held, lock_path, read_events, release,
    request_stop, spawn_watch, stop_requested,
)
from .llm.base import ChatMessage, LLMError
from .models import ReviewStatus
from .obs import configure_logging, metrics_text
from .pipeline.runner import ORPHAN_ERROR
from .reporting.text import plain
from .timeutil import format_ist, now_utc

_job_lock = threading.Lock()
_job: dict[str, Any] | None = None


def project_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "config").is_dir():
            return candidate
    return Path.cwd()


def desk_settings() -> Settings:
    """The config the desk reads and writes. ``CHANAKYA_CONFIG`` wins over the project default."""
    override = os.environ.get("CHANAKYA_CONFIG")
    return load_settings(override or project_root() / "config" / "chanakya.yaml")


# Paths that stay open even when app.api_token_env is set: a health check must work without a
# credential, or a load balancer / uptime monitor configured without one can never pass.
_AUTH_EXEMPT_PATHS = frozenset({"/api/health"})


def require_desk_auth(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    """App-wide bearer-token gate. A no-op (today's open-localhost behavior) when
    app.api_token_env is unset; once set, every route except the health check requires
    Authorization: Bearer <token>. This supersedes desk_acquisition.py's own narrower gate —
    both exist for defense in depth, but this one is what actually protects the rest of the
    desk API (opportunities, brief, documents, profile, insights, ...)."""
    if request.url.path in _AUTH_EXEMPT_PATHS:
        return
    settings = desk_settings()
    expected = settings.secret(settings.app.api_token_env) if settings.app.api_token_env else None
    if not expected:
        return
    provided = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None
    if provided != expected:
        raise HTTPException(401, "missing or invalid API token")


def is_demo(settings: Settings) -> bool:
    """A desk on the simulated model is showing synthetic notices and must say so."""
    return settings.llm.mode == "fake"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    text = value.isoformat()
    return text if text.endswith("Z") else text + "Z"


def _card(o: Opportunity) -> dict[str, Any]:
    return plain(_card_raw(o))


def _card_raw(o: Opportunity) -> dict[str, Any]:
    intel = o.intelligence or {}
    return {
        "id": o.id,
        "title": o.title,
        "organisation": o.organisation,
        "department": o.department,
        "state": o.state,
        "platform": o.platform,
        "tender_id": o.source_tender_id,
        "ref_no": o.ref_no,
        "category": o.category,
        "opportunity_type": o.opportunity_type,
        "status": o.status,
        "priority": o.priority,
        "score": o.relevance_score,
        "decision": o.filter_decision,
        "eligibility": o.eligibility_status,
        "review_status": o.review_status,
        "published_at": _iso(o.published_at),
        "closing_at": _iso(o.closing_at),
        "closing_label": format_ist(o.closing_at),
        "value_inr": o.estimated_value_inr,
        "emd_inr": o.emd_inr,
        "summary": intel.get("summary"),
        "why": intel.get("why_match"),
        "next_action": intel.get("recommended_next_action"),
        "solution": intel.get("relevant_solution"),
        "capabilities": o.top_capabilities or [],
        "reasons": o.filter_reasons or [],
        "flags": intel.get("flags") or [],
        "confidence": intel.get("confidence"),
        "confidence_basis": intel.get("confidence_basis"),
        "analysis_state": o.analysis_state,
        "official_url": o.official_url,
        "evidence_line": o.evidence,
    }


def _detail(session: Session, o: Opportunity) -> dict[str, Any]:
    card = _card_raw(o)
    intel = o.intelligence or {}
    findings = session.scalars(
        select(EligibilityFindingRow).where(EligibilityFindingRow.opportunity_id == o.id)
    ).all()
    changes = session.scalars(
        select(ChangeEvent).where(ChangeEvent.opportunity_id == o.id).order_by(ChangeEvent.detected_at.desc()).limit(12)
    ).all()
    card.update({
        "description": o.description,
        "score_breakdown": o.score_breakdown or {},
        "rule_score": o.rule_score,
        "semantic_score": o.semantic_score,
        "data_completeness": o.data_completeness,
        "explicit_requirements": intel.get("explicit_requirements") or [],
        "inferred_requirements": intel.get("inferred_requirements") or [],
        "eligibility_blockers": intel.get("eligibility_blockers") or [],
        "technical_risks": intel.get("technical_risks") or [],
        "missing_information": intel.get("missing_information") or [],
        "deliverables": intel.get("deliverables") or [],
        "technologies": intel.get("technologies") or [],
        "evidence": intel.get("evidence") or ([o.evidence] if o.evidence else []),
        "analysis_notes": intel.get("analysis_notes") or [],
        "findings": [
            {
                "criterion": f.criterion,
                "text": f.requirement_text,
                "assessment": f.assessment,
                "explicit": f.explicit,
                "source": f.source,
                "value": f.value,
                "unit": f.unit,
            }
            for f in findings
        ],
        "changes": [
            {
                "type": c.change_type,
                "field": c.field,
                "old": c.old_value,
                "new": c.new_value,
                "at": _iso(c.detected_at),
            }
            for c in changes
        ],
    })
    return plain(card)


def _writer_alive(settings: Settings) -> bool:
    """A running row is only believed while a process can still be writing it."""
    with _job_lock:
        if _job and _job.get("status") == "running":
            return True
    return held(lock_path(settings)) is not None


def _run_row(run: ScanRun, alive: bool = True) -> dict[str, Any]:
    stats = run.stats or {}
    stopped = run.status == "running" and not alive
    return {
        "id": run.id,
        "status": "stopped" if stopped else run.status,
        "trigger": run.trigger,
        "dry_run": run.dry_run,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "error": ORPHAN_ERROR if stopped and not run.error else run.error,
        "new": stats.get("new", 0),
        "updated": stats.get("updated", 0),
        "assessed": stats.get("assessed", 0),
        "rejected": stats.get("rejected", 0),
        "candidates": stats.get("candidates", 0),
        "strong": stats.get("strong", 0),
        "low": stats.get("low", 0),
        "priorities": {band: stats.get(band) for band in ("p1", "p2", "p3", "p4")} if "p1" in stats else None,
        "llm_skipped_budget": stats.get("llm_skipped_budget", 0),
        "deep_deferred": stats.get("deep_deferred", 0),
        "budget_blocked_reasons": stats.get("budget_blocked_reasons") or [],
        "triage_calls": stats.get("triage_calls", 0),
        "deep_calls": stats.get("deep_calls", 0),
        "llm_cost_usd": stats.get("llm_cost_usd", 0),
        "llm_mode": stats.get("llm_mode"),
        "coverage_notes": stats.get("coverage_notes") or [],
        "sources": stats.get("sources") or {},
    }


def _lead_clauses(*, priority: str | None, q: str | None, include_rejected: bool,
                  closing_within_days: int | None, capability: str | None, state: str | None,
                  review_status: str | None) -> list[Any]:
    clauses: list[Any] = []
    if priority:
        clauses.append(Opportunity.priority == priority)
        if not include_rejected:
            clauses.append(or_(Opportunity.filter_decision.is_(None), Opportunity.filter_decision != "rejected"))
    elif not include_rejected:
        clauses.append(or_(Opportunity.priority.in_(["P1", "P2", "P3"]), Opportunity.priority.is_(None)))
        clauses.append(or_(Opportunity.filter_decision.is_(None), Opportunity.filter_decision != "rejected"))
    if q and q.strip():
        like = f"%{q.strip()}%"
        clauses.append(or_(
            Opportunity.title.ilike(like), Opportunity.organisation.ilike(like),
            Opportunity.id.ilike(like), Opportunity.source_tender_id.ilike(like),
        ))
    if closing_within_days is not None:
        now = now_utc()
        clauses.append(Opportunity.status == "active")
        clauses.append(Opportunity.closing_at.is_not(None))
        clauses.append(Opportunity.closing_at >= now)
        clauses.append(Opportunity.closing_at <= now + timedelta(days=closing_within_days))
    if capability and capability.strip():
        clauses.append(cast(Opportunity.top_capabilities, String).icontains(f'"{capability.strip()}"'))
    if state and state.strip():
        clauses.append(func.lower(Opportunity.state) == state.strip().lower())
    if review_status and review_status.strip():
        clauses.append(Opportunity.review_status == review_status.strip())
    return clauses


def _watch_status() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "running": False, "mode": None, "run_id": None, "source_id": None,
        "source_name": None, "pages": None, "notices_stored": None, "started_at": None, "pause_s": None,
    }
    settings = desk_settings()
    info = held(lock_path(settings))
    if info is None:
        return payload
    payload.update({
        "running": True,
        "mode": info.get("mode") or "watch",
        "started_at": info.get("started_at"),
        "pause_s": info.get("pause_s"),
    })
    with open_session(settings) as session:
        payload["notices_stored"] = int(session.scalar(select(func.count()).select_from(Opportunity)) or 0)
        run = session.scalar(select(ScanRun).where(ScanRun.status == "running").order_by(ScanRun.started_at.desc()))
        if run is not None:
            payload["run_id"] = run.id
            payload["started_at"] = _iso(run.started_at)
            current = session.scalar(
                select(SourceScanRun)
                .where(SourceScanRun.run_id == run.id, SourceScanRun.status == "running")
                .order_by(SourceScanRun.started_at.desc())
            )
            if current is not None:
                payload["source_id"] = current.source_id
                payload["pages"] = current.pages_fetched
                source = session.get(Source, current.source_id)
                payload["source_name"] = source.name if source is not None else current.source_id
    return payload


def _priority_rank():
    return case(
        (Opportunity.priority == "P1", 0),
        (Opportunity.priority == "P2", 1),
        (Opportunity.priority == "P3", 2),
        (Opportunity.priority == "P4", 3),
        else_=4,
    )


def _narrate(session: Session) -> dict[str, Any]:
    total = session.scalar(select(func.count()).select_from(Opportunity)) or 0
    if total == 0:
        return {
            "mood": "curious",
            "greeting": (
                "I have not opened a source yet. Start a pass and I will tell you, notice by notice, "
                "what I kept, what I set aside, and why. Scores come with the sentence that produced them."
            ),
            "suggestions": ["What will you do on a pass?", "Which sources can you read?"],
        }
    def active(priority: str) -> int:
        return int(session.scalar(
            select(func.count()).select_from(Opportunity)
            .where(Opportunity.status == "active", Opportunity.priority == priority)
        ) or 0)

    p1, p2 = active("P1"), active("P2")
    first = session.scalar(
        select(Opportunity)
        .where(Opportunity.status == "active", Opportunity.priority.in_(["P1", "P2"]))
        .order_by(_priority_rank(), Opportunity.relevance_score.desc().nulls_last(), Opportunity.first_seen_at.asc(), Opportunity.id.asc())
    )
    leads = p1 + p2
    rejected = int(session.scalar(
        select(func.count()).select_from(Opportunity).where(Opportunity.filter_decision == "rejected")
    ) or 0)
    parts = [f"I have read {total:,} notices on this desk."]
    if leads:
        bands = " and ".join(f"{count} in {band}" for band, count in (("P1", p1), ("P2", p2)) if count)
        parts.append(f"{leads} {'is' if leads == 1 else 'are'} worth a decision now: {bands}.")
        if first is not None:
            title = (first.title or "").strip().rstrip(".")
            if len(title) > 90:
                title = title[:90].rsplit(" ", 1)[0] + "…"
            parts.append(f"Start with “{title}”.")
    else:
        parts.append("Nothing active is in the P1 or P2 band.")
    if rejected:
        parts.append(f"I set {rejected:,} aside for free, each with the rule that stopped it.")
    return {
        "mood": "focused" if leads else "neutral",
        "greeting": " ".join(parts),
        "suggestions": [
            "What should I look at today?",
            "Why did you set notices aside?",
            "Did this pass spend any model calls?",
        ],
    }


def _brief(session: Session, settings: Settings) -> dict[str, Any]:
    def count(*where) -> int:
        q = select(func.count()).select_from(Opportunity)
        for clause in where:
            q = q.where(clause)
        return int(session.scalar(q) or 0)

    leads = session.scalars(
        select(Opportunity)
        .where(Opportunity.status == "active", Opportunity.priority.in_(["P1", "P2", "P3"]))
        .order_by(_priority_rank(), Opportunity.relevance_score.desc().nulls_last(), Opportunity.first_seen_at.asc(), Opportunity.id.asc())
        .limit(5)
    ).all()
    last = session.scalar(select(ScanRun).order_by(ScanRun.started_at.desc()))
    sources = session.scalars(select(Source).order_by(Source.id)).all()
    pages = _source_pages(session)
    voice = _narrate(session)
    return plain({
        "demo": is_demo(settings),
        "email": {
            "recipients": len([r for r in settings.email.to + settings.email.cc if r]),
            "digest": settings.email.digest,
            "alert_on_new_p1": settings.email.alert_on_new_p1,
            "provider": settings.email.provider,
        },
        "llm_mode": settings.llm.mode,
        "company": settings.company_profile.name,
        "counts": {
            "notices": count(),
            "active": count(Opportunity.status == "active"),
            "p1": count(Opportunity.priority == "P1", Opportunity.status == "active"),
            "p2": count(Opportunity.priority == "P2", Opportunity.status == "active"),
            "p3": count(Opportunity.priority == "P3", Opportunity.status == "active"),
            "rejected": count(Opportunity.filter_decision == "rejected"),
            "shortlisted": count(Opportunity.review_status == "Shortlisted"),
        },
        "last_run": _run_row(last, _writer_alive(settings)) if last else None,
        "leads": [_card(o) for o in leads],
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "type": s.type,
                "health": s.health,
                "enabled": s.enabled,
                "verification_status": s.verification_status,
                "last_error": s.last_error,
                "consecutive_failures": s.consecutive_failures,
                "high_water_mark": _iso(s.high_water_mark),
                "pages": pages.get(s.id),
                "caption": GEPNIC_CAPTION if s.type == "gepnic" else None,
                "restricted": s.health == "restricted",
            }
            for s in sources
        ],
        "configured_sources": [
            {"id": s.id, "name": s.name or s.id, "type": s.type, "enabled": s.enabled,
             "verification_status": s.verification_status,
             "caption": GEPNIC_CAPTION if s.type == "gepnic" else None}
            for s in settings.sources
        ],
        "restricted_portals": [] if is_demo(settings) else list(RESTRICTED_PORTALS),
        "vira": voice,
    })


def _source_pages(session: Session) -> dict[str, int]:
    latest = (
        select(SourceScanRun.source_id, func.max(SourceScanRun.id).label("mid"))
        .group_by(SourceScanRun.source_id)
        .subquery()
    )
    rows = session.execute(
        select(SourceScanRun.source_id, SourceScanRun.pages_fetched).join(latest, SourceScanRun.id == latest.c.mid)
    ).all()
    return {source_id: int(fetched or 0) for source_id, fetched in rows}


class ReviewRequest(BaseModel):
    status: str | None = None
    label: str | None = None
    note: str | None = None
    reviewer: str = "BD"


class ViraRequest(BaseModel):
    message: str = Field(min_length=1, max_length=600)
    opportunity_id: str | None = None


class WatchRequest(BaseModel):
    action: str


class AnalyzeRequest(BaseModel):
    scope: str = "changed"


def _append_event(event: dict[str, Any]) -> None:
    with _job_lock:
        if _job is None:
            return
        event = plain({"at": datetime.utcnow().isoformat() + "Z", **event})
        _job["events"].append(event)
        _job["stage"] = event.get("kind") or _job.get("stage")
        if len(_job["events"]) > 400:
            del _job["events"][:-400]


def _run_job(*, skip_scan: bool = False, trigger: str = "desk", assess_scope: str = "changed") -> None:
    global _job
    owns_lock = False
    path: Path | None = None
    try:
        settings = desk_settings()
        demo = is_demo(settings)
        path = lock_path(settings)
        acquire(path, {
            "pid": os.getpid(),
            "desk": "live",
            "mode": "analyze" if skip_scan else "scan",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "pause_s": None,
        })
        owns_lock = True
        opening = "Scoring the notices already stored. I am not opening a portal." if skip_scan else (
            "Starting the pass. I will narrate each source and each decision."
        )
        _append_event({"kind": "judge" if skip_scan else "acquire", "message": opening})
        if demo and not skip_scan:
            # The synthetic inbox does not change between passes. A demo exists to be
            # watched, so judge every notice again and narrate it.
            assess_scope = "all"

        def on_event(event: dict[str, Any]) -> None:
            _append_event(event)

        with open_session(settings) as session:
            result = run_pipeline(
                settings, session, email=False,
                dry_run=(demo and not skip_scan),
                export=not skip_scan,
                trigger=trigger, assess_scope=assess_scope, skip_scan=skip_scan,
                on_event=on_event,
                should_stop=(lambda: stop_requested(path)) if owns_lock and path is not None else None,
            )
        with _job_lock:
            if _job is not None:
                _job["status"] = result.run.status
                _job["run_id"] = result.run.id
                _job["stats"] = _run_row(result.run)
                _job["error"] = result.run.error
    except LockHeld as exc:
        with _job_lock:
            if _job is not None:
                _job["status"] = "failed"
                _job["error"] = f"another writer already holds the database (pid {exc.pid})"
    except Exception as exc:  # noqa: BLE001 — surface the failure to the desk
        with _job_lock:
            if _job is not None:
                _job["status"] = "failed"
                _job["error"] = f"{type(exc).__name__}: {exc}"
                _job["events"].append({
                    "at": datetime.utcnow().isoformat() + "Z",
                    "kind": "error",
                    "message": _job["error"],
                    "trace": traceback.format_exc()[-2000:],
                })
    finally:
        if owns_lock and path is not None:
            release(path, os.getpid())


def _answer_from_records(session: Session, message: str, opportunity_id: str | None) -> dict[str, Any] | None:
    text = message.lower()
    if opportunity_id:
        o = session.get(Opportunity, opportunity_id)
        if o:
            return _explain(o)
    for token in message.replace("—", " ").split():
        cleaned = token.strip(".,").upper()
        if cleaned.startswith("CHK-"):
            o = session.get(Opportunity, cleaned)
            if o:
                return _explain(o)
    if any(phrase in text for phrase in ("today", "look at", "shortlist", "worth", "priority", "what should")):
        leads = session.scalars(
            select(Opportunity)
            .where(Opportunity.status == "active", Opportunity.priority.in_(["P1", "P2"]))
            .order_by(_priority_rank(), Opportunity.relevance_score.desc().nulls_last(), Opportunity.first_seen_at.asc(), Opportunity.id.asc())
            .limit(4)
        ).all()
        if not leads:
            return {
                "reply": "Nothing active is in P1 or P2. I can show you what I set aside, or you can start another pass.",
                "mood": "neutral",
                "suggestions": ["Why did you set notices aside?"],
                "leads": [],
            }
        lines = []
        for o in leads:
            action = (o.intelligence or {}).get("recommended_next_action") or "Review the notice."
            lines.append(f"{o.priority} {o.id}: {(o.title or '')[:90]}. Next: {action}")
        return {
            "reply": "These are the ones I would put in front of you.\n\n" + "\n\n".join(lines),
            "mood": "focused",
            "suggestions": ["Open the first one"],
            "leads": [_card(o) for o in leads],
        }
    if any(phrase in text for phrase in ("set aside", "reject", "noise", "why did")):
        rows = session.scalars(
            select(Opportunity).where(Opportunity.filter_decision == "rejected").limit(4)
        ).all()
        if not rows:
            return {"reply": "I have not set any notice aside on this desk yet.", "mood": "neutral", "suggestions": [], "leads": []}
        lines = [f"“{(o.title or '')[:80]}”: {(o.filter_reasons or ['no match'])[0]}" for o in rows]
        return {
            "reply": "I drop a notice when the rules find no Wayam capability, or when it is civil works, equipment supply, or another exclusion. No model call is spent on those.\n\n" + "\n".join(lines),
            "mood": "neutral",
            "suggestions": ["What should I look at today?"],
            "leads": [_card(o) for o in rows],
        }
    if any(phrase in text for phrase in ("model", "cost", "token", "ollama", "spend")):
        run = session.scalar(select(ScanRun).order_by(ScanRun.started_at.desc()))
        if run is None:
            return {"reply": "No pass has finished on this desk, so there is no spend to report.", "mood": "neutral", "suggestions": [], "leads": []}
        stats = run.stats or {}
        return {
            "reply": (
                f"The last pass ({run.id}, {run.status}) used mode {stats.get('llm_mode')}. "
                f"Triage calls {stats.get('triage_calls', 0)}, deep calls {stats.get('deep_calls', 0)}, "
                f"recorded cost ${float(stats.get('llm_cost_usd') or 0):.4f}. "
                "Rejected notices never reach the model."
            ),
            "mood": "neutral",
            "suggestions": ["What should I look at today?"],
            "leads": [],
        }
    if any(phrase in text for phrase in ("source", "portal", "what will you", "how do you")):
        return {
            "reply": (
                "A pass has five beats. I open each enabled source and keep the official listing. "
                "I match the text to the Wayam capability book with rules first, then lexical similarity. "
                "I set unrelated notices aside for free. Ambiguous or strong matches can go to the model, "
                "and only then do I score P1 to P4 with a written reason and a next action. "
                "I read the portals in the config and use the Ollama key only where the rules cannot decide."
            ),
            "mood": "curious",
            "suggestions": ["What should I look at today?"],
            "leads": [],
        }
    return None


def _explain(o: Opportunity) -> dict[str, Any]:
    intel = o.intelligence or {}
    parts = [
        f"{o.id} is {o.priority or 'unscored'} with a score of {o.relevance_score if o.relevance_score is not None else 'not set'}.",
        f"Decision: {o.filter_decision or 'not assessed'}. Eligibility: {o.eligibility_status or 'not assessed'}.",
    ]
    if intel.get("why_match"):
        parts.append(str(intel["why_match"]))
    if o.filter_reasons:
        parts.append("Rules: " + "; ".join(o.filter_reasons[:3]))
    if intel.get("recommended_next_action"):
        parts.append("Next: " + str(intel["recommended_next_action"]))
    breakdown = (o.score_breakdown or {}).get("components") or {}
    if breakdown:
        bits = [f"{name} {comp.get('score')} ({comp.get('reason')})" for name, comp in breakdown.items()]
        parts.append("Score: " + "; ".join(bits[:6]))
    return {
        "reply": "\n\n".join(parts),
        "mood": "focused",
        "suggestions": ["What should I look at today?"],
        "leads": [_card(o)],
    }


def _model_answer(settings: Settings, message: str, brief: dict[str, Any]) -> str | None:
    if settings.llm.mode != "cloud":
        return None
    from .llm import build_provider
    provider = build_provider(settings)
    if provider is None:
        return None
    payload = {
        "counts": brief["counts"],
        "last_run": brief["last_run"],
        "leads": [
            {"id": lead["id"], "title": lead["title"], "priority": lead["priority"], "why": lead["why"],
             "next_action": lead["next_action"], "decision": lead["decision"]}
            for lead in brief["leads"]
        ],
    }
    system = (
        "You are Vira, Wayam's procurement discovery agent. Answer only from DATA. "
        "If DATA does not contain the answer, say so. Do not invent tenders, values, deadlines, or eligibility. "
        "Write in plain sentences. Keep it under 140 words."
    )
    try:
        response = provider.chat(
            [ChatMessage("system", system), ChatMessage("user", message + "\n\nDATA:\n" + json.dumps(payload)[:7000])],
            model=settings.llm.fast_model, max_tokens=320, temperature=0, think=settings.llm.think,
        )
    except LLMError:
        return None
    finally:
        provider.close()
    text = (response.content or "").strip()
    return text or None


def create_app() -> FastAPI:
    settings = desk_settings()
    if settings.app.environment == "production":
        token = settings.secret(settings.app.api_token_env) if settings.app.api_token_env else None
        if not token:
            # Fail fast at startup, not on the first request: a production desk silently
            # running open (because api_token_env was never set) is exactly the "accidentally
            # public" outcome this check exists to rule out.
            raise RuntimeError(
                "app.environment is 'production' but app.api_token_env is unset or its "
                "environment variable has no value — refusing to start an unauthenticated "
                "desk API in production. Set app.api_token_env to the name of an env var "
                "holding a real token, and set that env var, before deploying."
            )

    app = FastAPI(title="Chanakya desk", version="0.1.0", dependencies=[Depends(require_desk_auth)])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Fixed-window counter per client (bearer token if auth is configured, else source IP),
    # scoped to this app instance so repeated create_app() calls (every test file makes many)
    # never share state. In-memory is correct for this project's single-process deployment
    # model (same assumption the job manager and LLM budget guard already make); a multi-worker
    # deployment would need a shared store instead.
    rate_limit_state: dict[str, tuple[int, float]] = {}
    rate_limit_lock = threading.Lock()
    rate_limit_window_s = 60.0

    def rate_limit_key(request: Request) -> str:
        auth = request.headers.get("authorization")
        if auth:
            return auth
        return request.client.host if request.client else "unknown"

    def over_rate_limit(key: str, limit: int) -> float | None:
        """None if the request is allowed; otherwise seconds until the window resets."""
        now = time.monotonic()
        with rate_limit_lock:
            count, window_start = rate_limit_state.get(key, (0, now))
            if now - window_start >= rate_limit_window_s:
                count, window_start = 0, now
            count += 1
            rate_limit_state[key] = (count, window_start)
            if count > limit:
                return rate_limit_window_s - (now - window_start)
        return None

    @app.middleware("http")
    async def rate_limit(request: Request, call_next: Callable) -> Any:
        limit = settings.app.rate_limit_per_minute
        if limit > 0 and request.method != "OPTIONS" and request.url.path != "/api/health":
            retry_after = over_rate_limit(rate_limit_key(request), limit)
            if retry_after is not None:
                return PlainTextResponse(
                    "Too Many Requests", status_code=429, headers={"Retry-After": str(int(retry_after) + 1)},
                )
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable) -> Any:
        """Baseline response headers every route gets, with no per-route opt-in needed.
        This API serves only JSON (no HTML it renders itself), so there is no CSP to tune —
        these three cover the browser-facing risk a JSON API still carries: a browser
        sniffing a response as something executable, or this API being framed/leaked as a
        referrer by a page that embeds it."""
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/metrics")
    def metrics(format: str = "prometheus") -> PlainTextResponse:
        """Run, source-health and 24-hour gauges for a monitor: the same text as `chanakya metrics`."""
        if format not in ("prometheus", "json"):
            raise HTTPException(400, "format must be prometheus or json")
        with open_session(desk_settings()) as session:
            text = metrics_text(session, format)
        media = "application/json" if format == "json" else "text/plain; version=0.0.4; charset=utf-8"
        return PlainTextResponse(text, media_type=media)

    from .desk_insights import router as insights_router  # /api/costs, /api/products, /api/capabilities
    app.include_router(insights_router)
    from .desk_documents import router as documents_router  # a notice's documents, and analyse-one
    app.include_router(documents_router)
    from .desk_profile import router as profile_router  # /api/profile
    app.include_router(profile_router)
    from .desk_acquisition import router as acquisition_router  # /api/acquisition/*
    app.include_router(acquisition_router)

    @app.get("/api/brief")
    def brief() -> dict[str, Any]:
        settings = desk_settings()
        with open_session(settings) as session:
            return _brief(session, settings)

    @app.get("/api/opportunities")
    def opportunities(priority: str | None = None, q: str | None = None,
                      include_rejected: bool = False, closing_within_days: int | None = None,
                      capability: str | None = None, state: str | None = None,
                      review_status: str | None = None, limit: int = 200, offset: int = 0) -> dict[str, Any]:
        if closing_within_days is not None and closing_within_days <= 0:
            raise HTTPException(400, "closing_within_days must be positive")
        limit = min(max(limit, 1), 500)
        offset = max(offset, 0)
        settings = desk_settings()
        clauses = _lead_clauses(
            priority=priority, q=q, include_rejected=include_rejected,
            closing_within_days=closing_within_days, capability=capability,
            state=state, review_status=review_status,
        )
        with open_session(settings) as session:
            total = int(session.scalar(select(func.count()).select_from(Opportunity).where(*clauses)) or 0)
            stmt = select(Opportunity).where(*clauses).order_by(
                _priority_rank(), Opportunity.relevance_score.desc().nulls_last(), Opportunity.closing_at.asc().nulls_first(),
                Opportunity.first_seen_at.asc(), Opportunity.id.asc(),  # ties in a fixed order, so pages do not overlap
            ).limit(limit).offset(offset)
            rows = session.scalars(stmt).all()
            return {"total": total, "opportunities": [_card(o) for o in rows]}

    @app.get("/api/opportunities/{opportunity_id}")
    def opportunity(opportunity_id: str) -> dict[str, Any]:
        settings = desk_settings()
        with open_session(settings) as session:
            row = session.get(Opportunity, opportunity_id)
            if row is None:
                raise HTTPException(404, "unknown opportunity")
            return _detail(session, row)

    @app.post("/api/opportunities/{opportunity_id}/review")
    def review(opportunity_id: str, body: ReviewRequest) -> dict[str, Any]:
        if body.label and body.label not in ("relevant", "irrelevant"):
            raise HTTPException(400, "label must be relevant or irrelevant")
        if body.status:
            try:
                body.status = ReviewStatus(body.status).value
            except ValueError as exc:
                raise HTTPException(400, "unknown review status") from exc
        settings = desk_settings()
        with open_session(settings) as session:
            row = session.get(Opportunity, opportunity_id)
            if row is None:
                raise HTTPException(404, "unknown opportunity")
            if body.status:
                row.review_status = body.status
            session.add(UserReviewFeedback(
                opportunity_id=row.id, review_status=body.status, relevance_label=body.label,
                note=body.note, reviewer=body.reviewer,
            ))
            session.commit()
            session.refresh(row)
            return _card(row)

    @app.get("/api/runs")
    def runs() -> dict[str, Any]:
        settings = desk_settings()
        with open_session(settings) as session:
            rows = session.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(30)).all()
            alive = _writer_alive(settings)
            return {"runs": [_run_row(r, alive) for r in rows]}

    @app.get("/api/job")
    def job() -> dict[str, Any]:
        with _job_lock:
            return {"job": None if _job is None else {k: v for k, v in _job.items() if k != "trace"}}

    @app.post("/api/runs")
    def start_run() -> dict[str, Any]:
        global _job
        if held(lock_path(desk_settings())):
            raise HTTPException(409, "a watch already holds the database")
        with _job_lock:
            if _job and _job.get("status") == "running":
                raise HTTPException(409, "a pass is already running")
            _job = {
                "id": datetime.utcnow().strftime("job_%Y%m%dT%H%M%S"),
                "kind": "scan",
                "status": "running",
                "stage": "acquire",
                "events": [],
                "error": None,
                "run_id": None,
                "stats": None,
            }
            snapshot = {k: v for k, v in _job.items()}
        thread = threading.Thread(target=_run_job, name="chanakya-pass", daemon=True)
        thread.start()
        return {"job": snapshot}

    @app.get("/api/watch")
    def watch() -> dict[str, Any]:
        return _watch_status()

    @app.post("/api/watch")
    def watch_action(body: WatchRequest) -> dict[str, Any]:
        if body.action not in ("start", "stop"):
            raise HTTPException(400, "action must be start or stop")
        settings = desk_settings()
        path = lock_path(settings)
        if body.action == "stop":
            request_stop(path)
            return _watch_status()
        if is_demo(settings):
            raise HTTPException(400, "the demo inbox does not change, so there is nothing to watch")
        if held(path):
            raise HTTPException(409, "a watch already holds the database")
        with _job_lock:
            if _job and _job.get("status") == "running":
                raise HTTPException(409, "a pass is already running")
        spawn_watch(project_root(), config=settings.config_path)
        return {"accepted": True, "running": True, "mode": "watch"}

    @app.post("/api/analyze")
    def analyze(body: AnalyzeRequest) -> dict[str, Any]:
        global _job
        if body.scope not in ("changed", "all"):
            raise HTTPException(400, "scope must be changed or all")
        if held(lock_path(desk_settings())):
            raise HTTPException(409, "a watch already holds the database")
        with _job_lock:
            if _job and _job.get("status") == "running":
                raise HTTPException(409, "a pass is already running")
            _job = {
                "id": datetime.utcnow().strftime("job_%Y%m%dT%H%M%S"),
                "kind": "analyze",
                "status": "running",
                "stage": "judge",
                "events": [],
                "error": None,
                "run_id": None,
                "stats": None,
            }
            snapshot = {k: v for k, v in _job.items()}
        thread = threading.Thread(
            target=_run_job, kwargs={"skip_scan": True, "trigger": "analyze", "assess_scope": body.scope},
            name="chanakya-analyze", daemon=True,
        )
        thread.start()
        return {"job": snapshot}

    @app.get("/api/events")
    def events(after: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        settings = desk_settings()
        with open_session(settings) as session:
            if run_id is None:
                run = session.scalar(
                    select(ScanRun).where(ScanRun.status == "running").order_by(ScanRun.started_at.desc())
                )
                if run is None:
                    run = session.scalar(select(ScanRun).order_by(ScanRun.started_at.desc()))
                run_id = run.id if run else None
        if not run_id:
            return {"run_id": None, "events": []}
        rows = read_events(lock_path(settings).parent, run_id, after=after)
        return {"run_id": run_id, "events": [plain(row) for row in rows]}

    @app.post("/api/vira")
    def vira(body: ViraRequest) -> dict[str, Any]:
        settings = desk_settings()
        with open_session(settings) as session:
            known = _answer_from_records(session, body.message, body.opportunity_id)
            if known:
                return known
            brief = _brief(session, settings)
        try:
            text = _model_answer(settings, body.message, brief)
        except Exception:  # noqa: BLE001 — the desk must still answer
            text = None
        if text:
            return {"reply": text, "mood": "curious", "suggestions": brief["vira"]["suggestions"], "leads": []}
        return {
            "reply": brief["vira"]["greeting"],
            "mood": brief["vira"]["mood"],
            "suggestions": brief["vira"]["suggestions"],
            "leads": [],
        }

    return app


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    import uvicorn
    try:
        configure_logging(desk_settings())  # the desk's own config decides app.log_format
    except Exception:  # noqa: BLE001 - a broken config is reported by the first request, as before
        pass
    uvicorn.run(create_app(), host=host, port=port)


# Imported by tests that want the job hook type without starting a server.
EventHook = Callable[[dict[str, Any]], None]
