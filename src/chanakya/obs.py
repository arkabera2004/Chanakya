"""Observability: structured logs, the ``run_finished`` summary event and metrics export.

Three things live here, all on the standard library:

* ``configure_logging`` installs one stderr handler with a redaction filter and
  either the plain text format or one JSON object per line (``app.log_format``,
  overridable with ``CHANAKYA_LOG_FORMAT``).
* ``emits_run_finished`` decorates ``app.run_pipeline``, the one function every
  run goes through, so each run ends with exactly one ``run_finished`` record.
* ``collect_metrics`` and ``render_prometheus`` read the database and produce
  the numbers a platform monitor scrapes (``chanakya metrics``, ``GET /api/metrics``).

Log safety (doc 13): the filter replaces every known secret value with
``***REDACTED***`` (reusing ``security.redact``), drops fields that carry
prompts or document text, and caps the length of every string, so a log line
cannot hold a credential, a full prompt or a full document.
"""

from __future__ import annotations

import copy
import functools
import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .security import redact

log = logging.getLogger("chanakya.obs")

LOG_FORMATS = ("text", "json")
LOG_FORMAT_ENV = "CHANAKYA_LOG_FORMAT"
TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Length caps. A deep-analysis prompt is several thousand characters and a tender
# document far more, so neither fits under these.
MAX_MESSAGE_CHARS = 1000
MAX_FIELD_CHARS = 500
MAX_EXCEPTION_CHARS = 4000

# Structured fields with these names carry model input or source text. They are
# never written; only their size is.
_PAYLOAD_KEYS = frozenset({
    "prompt", "prompts", "system_prompt", "messages", "document", "documents", "document_text",
    "doc_text", "text", "content", "excerpt", "body", "html", "raw", "payload",
})
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|password|passwd|secret|authorization|bearer|credentials?)(?:$|_)|^(?:access_|auth_)?token$",
    re.I,
)
REDACTED = "***REDACTED***"

# Environment variables that hold credentials when no settings object is at hand.
_DEFAULT_SECRET_ENVS = ("OLLAMA_API_KEY", "CHANAKYA_SMTP_USERNAME", "CHANAKYA_SMTP_PASSWORD")
_DATABASE_URL_ENV = "CHANAKYA_DATABASE_URL"

_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message", "asctime", "taskName", "_chanakya_clean",
}

_registered_secrets: set[str] = set()


# ---------------------------------------------------------------- redaction

def register_secret(value: str | None) -> None:
    """Remember a secret value so it is removed from every later log line.

    ``Settings.secret`` calls this with each value it hands out."""
    if value and len(value.strip()) >= 4:
        _registered_secrets.add(value.strip())


def _url_password(url: str | None) -> str | None:
    if not url or "@" not in url:
        return None
    try:
        return urlsplit(url).password
    except ValueError:
        return None


def known_secrets(settings: Any = None) -> list[str]:
    """Every secret value this process can see, read fresh so a late ``.env`` load is covered."""
    names = set(_DEFAULT_SECRET_ENVS)
    urls = [os.environ.get(_DATABASE_URL_ENV)]
    if settings is not None:
        try:
            names.update({settings.llm.api_key_env, settings.email.username_env, settings.email.password_env})
            urls.append(settings.app.database_url)
        except AttributeError:
            pass
    values = set(_registered_secrets)
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            values.add(value)
    for url in urls:
        password = _url_password(url)
        if password:
            values.add(password)
    # Longest first, so a secret that contains another is replaced whole.
    return sorted((v for v in values if len(v) >= 4), key=len, reverse=True)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated {len(text) - limit} chars]"


def _clip_tail(text: str, limit: int) -> str:
    """Keep the end of a traceback: the last frames and the error line matter most."""
    if len(text) <= limit:
        return text
    return f"[truncated {len(text) - limit} chars]...{text[-limit:]}"


def _size(value: Any) -> int:
    try:
        return len(value) if isinstance(value, str) else len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def scrub(value: Any, secrets: list[str], *, key: str | None = None) -> Any:
    """Make one structured value safe to log: redact, drop payload fields, cap lengths."""
    if key is not None:
        if key.lower() in _PAYLOAD_KEYS:
            return f"[omitted {_size(value)} chars]"
        if _SENSITIVE_KEY.search(key) and value not in (None, "", False):
            return REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clip(redact(value, secrets), MAX_FIELD_CHARS)
    if isinstance(value, dict):
        return {str(k): scrub(v, secrets, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [scrub(v, secrets) for v in value]
    return _clip(redact(str(value), secrets), MAX_FIELD_CHARS)


def record_extras(record: logging.LogRecord) -> dict[str, Any]:
    """The structured fields a caller passed with ``extra=``."""
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}


def sanitize_record(record: logging.LogRecord, settings: Any = None) -> logging.LogRecord:
    """Return a safe copy of ``record``. The original is left alone for other handlers."""
    if getattr(record, "_chanakya_clean", False):
        return record
    secrets = known_secrets(settings)
    clean = copy.copy(record)
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a bad format string must not lose the line
        message = str(record.msg)
    clean.msg = _clip(redact(message, secrets), MAX_MESSAGE_CHARS)
    clean.args = None
    exc_text = record.exc_text
    if not exc_text and record.exc_info:
        exc_text = logging.Formatter().formatException(record.exc_info)
    clean.exc_info = None
    clean.exc_text = _clip_tail(redact(exc_text, secrets), MAX_EXCEPTION_CHARS) if exc_text else None
    if record.stack_info:
        clean.stack_info = _clip_tail(redact(record.stack_info, secrets), MAX_EXCEPTION_CHARS)
    for key, value in record_extras(record).items():
        setattr(clean, key, scrub(value, secrets, key=key))
    clean._chanakya_clean = True
    return clean


class RedactionFilter(logging.Filter):
    """Handler filter: nothing reaches the formatter before it has been sanitised."""

    def __init__(self, settings: Any = None) -> None:
        super().__init__()
        self.settings = settings

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:  # type: ignore[override]
        return sanitize_record(record, self.settings)


# ---------------------------------------------------------------- formatters

class TextFormatter(logging.Formatter):
    """The plain format, sanitised even when used without the filter."""

    def __init__(self, settings: Any = None) -> None:
        super().__init__(TEXT_FORMAT)
        self.settings = settings

    def format(self, record: logging.LogRecord) -> str:
        return super().format(sanitize_record(record, self.settings))


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp (UTC ISO), level, logger, message, then the extras."""

    def __init__(self, settings: Any = None) -> None:
        super().__init__()
        self.settings = settings

    def format(self, record: logging.LogRecord) -> str:
        record = sanitize_record(record, self.settings)
        stamp = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        payload: dict[str, Any] = {
            "timestamp": stamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record_extras(record).items():
            payload[key if key not in payload else f"extra_{key}"] = value
        if record.exc_text:
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = record.stack_info
        # ASCII output: a Windows console code page cannot break a log line.
        return json.dumps(payload, default=str, separators=(",", ":"))


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever ``sys.stderr`` is now, so a replaced stream is never a closed one."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr


def resolve_log_format(settings: Any = None) -> str:
    """``CHANAKYA_LOG_FORMAT`` wins, then ``app.log_format``, then text."""
    env = (os.environ.get(LOG_FORMAT_ENV) or "").strip().lower()
    if env in LOG_FORMATS:
        return env
    configured = getattr(getattr(settings, "app", None), "log_format", None)
    return configured if configured in LOG_FORMATS else "text"


_verbose = False


def configure_logging(settings: Any = None, *, verbose: bool | None = None, stream: Any = None) -> str:
    """Install (or replace) Chanakya's log handler. Safe to call more than once.

    Text mode keeps the earlier behaviour: warnings and above, everything with
    ``--verbose``. JSON mode also lets ``chanakya.*`` loggers through at
    ``app.log_level`` so the ``run_finished`` event reaches the monitor.
    ``verbose=None`` keeps the verbosity of the previous call.
    Returns the format in use.
    """
    global _verbose
    if verbose is None:
        verbose = _verbose
    _verbose = verbose
    fmt = resolve_log_format(settings)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_chanakya", False):
            root.removeHandler(handler)
    handler: logging.Handler = _StderrHandler() if stream is None else logging.StreamHandler(stream)
    handler._chanakya = True  # type: ignore[attr-defined]
    handler.addFilter(RedactionFilter(settings))
    handler.setFormatter(JsonFormatter(settings) if fmt == "json" else TextFormatter(settings))
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.WARNING)
    app_logger = logging.getLogger("chanakya")
    if fmt == "json" and not verbose:
        name = str(getattr(getattr(settings, "app", None), "log_level", "INFO") or "INFO").upper()
        level = logging.getLevelName(name)
        app_logger.setLevel(level if isinstance(level, int) else logging.INFO)
    else:
        app_logger.setLevel(logging.NOTSET)
    return fmt


# ---------------------------------------------------------------- run summary

RUN_FINISHED = "run_finished"


def run_summary(result: Any, *, duration_s: float, email_requested: bool = False) -> dict[str, Any]:
    """The fields of the ``run_finished`` event, from a finished ``RunResult``."""
    run, stats = result.run, result.stats
    sources = {
        source_id: {
            "status": info.get("status"),
            "rows": int(info.get("seen") or 0),
            "new": int(info.get("new") or 0),
            "updated": int(info.get("updated") or 0),
            "pages": int(info.get("pages") or 0),
            "duration_s": info.get("duration_s"),
            "error": info.get("error"),
        }
        for source_id, info in (stats.sources or {}).items()
    }
    deliveries: dict[str, int] = {}
    for _recipient, status in result.email_status or []:
        deliveries[status] = deliveries.get(status, 0) + 1
    alerts = sum(int(getattr(result, name, 0) or 0)
                 for name in ("alerts_sent", "corrigendum_alerts_sent", "deadline_alerts_sent"))
    return {
        "event": RUN_FINISHED,
        "run_id": run.id,
        "trigger": run.trigger,
        "status": run.status,
        "exit_code": result.exit_code,
        "dry_run": bool(run.dry_run),
        "duration_s": round(duration_s, 3),
        "sources": sources,
        "rows_total": sum(s["rows"] for s in sources.values()),
        "new": stats.new,
        "updated": stats.updated,
        "assessed": stats.assessed,
        "priorities": {"P1": stats.p1, "P2": stats.p2, "P3": stats.p3, "P4": stats.p4},
        "model_mode": stats.llm_mode,
        "model_calls": stats.triage_calls + stats.deep_calls,
        "triage_calls": stats.triage_calls,
        "deep_calls": stats.deep_calls,
        "model_cached": stats.llm_cached,
        "model_failed": stats.llm_failed,
        "model_skipped_budget": stats.llm_skipped_budget,
        "model_cost_usd": stats.llm_cost_usd,
        "budget_blocked_reasons": list(stats.budget_blocked_reasons or []),
        "email_sent": deliveries.get("sent", 0) > 0 or (email_requested and alerts > 0),
        "email_deliveries": deliveries,
        "email_error": result.email_error,
        "alerts_sent": alerts,
        "report_written": result.report_path is not None,
        "error": run.error,
    }


def _level_for(exit_code: int) -> int:
    if exit_code == 0:
        return logging.INFO
    return logging.WARNING if exit_code == 3 else logging.ERROR


def log_run_finished(summary: dict[str, Any]) -> None:
    log.log(
        _level_for(int(summary.get("exit_code") or 0)),
        "run_finished run_id=%s trigger=%s status=%s exit_code=%s duration_s=%s new=%s updated=%s assessed=%s "
        "model_calls=%s model_cost_usd=%s email_sent=%s",
        summary.get("run_id"), summary.get("trigger"), summary.get("status"), summary.get("exit_code"),
        summary.get("duration_s"), summary.get("new"), summary.get("updated"), summary.get("assessed"),
        summary.get("model_calls"), summary.get("model_cost_usd"), summary.get("email_sent"),
        extra=summary,
    )


def emits_run_finished(fn):
    """Decorator for ``app.run_pipeline``: one ``run_finished`` record per call, whatever the outcome.

    A run that returns reports its own status and exit code. A call that raises
    (before a run row exists, or after it, for example while exporting) reports
    ``status="crashed"`` with exit code 1 and no run id, then the error continues.
    Emitting the record can never fail the run.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            try:
                log_run_finished({
                    "event": RUN_FINISHED, "run_id": None, "trigger": kwargs.get("trigger", "manual"),
                    "status": "crashed", "exit_code": 130 if isinstance(exc, KeyboardInterrupt) else 1,
                    "duration_s": round(time.monotonic() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                })
            except Exception:  # noqa: BLE001 - reporting must not hide the real error
                pass
            raise
        try:
            requested = bool(kwargs.get("email")) and not kwargs.get("dry_run")
            log_run_finished(run_summary(result, duration_s=time.monotonic() - started, email_requested=requested))
        except Exception:  # noqa: BLE001 - reporting must not fail a finished run
            log.exception("could not emit run_finished")
        return result

    return wrapper


# ---------------------------------------------------------------- metrics

SUCCESS_STATUSES = ("ok", "partial")  # the same definition `chanakya healthcheck` uses
RUN_STATUSES = ("running", "ok", "partial", "failed")


def _epoch(value: datetime | None) -> float | None:
    """Stored timestamps are naive UTC."""
    return None if value is None else value.replace(tzinfo=UTC).timestamp()


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def derive_exit_code(status: str, error: str | None, email_failed: bool = False) -> int | None:
    """The exit code a stored run maps to (same rules as ``RunResult.exit_code``). None while running."""
    if status == "running":
        return None
    if status == "failed":
        return 1 if error else 2
    if status == "partial" or email_failed:
        return 3
    return 0


def collect_metrics(session: Any, now: datetime | None = None) -> dict[str, Any]:
    """Latest run, source health and last-24-hours totals, read from the database."""
    from sqlalchemy import func, select

    from .db import AIUsageEvent, EmailDelivery, ScanRun, Source, SourceScanRun
    from .timeutil import now_utc

    now = now or now_utc()
    day_ago = now - timedelta(hours=24)

    last_run: dict[str, Any] | None = None
    run = session.scalar(select(ScanRun).order_by(ScanRun.started_at.desc()))
    if run is not None:
        stats = run.stats or {}
        email_failed = bool(session.scalar(
            select(func.count()).select_from(EmailDelivery)
            .where(EmailDelivery.run_id == run.id, EmailDelivery.status == "failed")
        ))
        per_source = session.scalars(
            select(SourceScanRun).where(SourceScanRun.run_id == run.id).order_by(SourceScanRun.id)
        ).all()
        duration = (run.finished_at - run.started_at).total_seconds() if run.finished_at else None
        last_run = {
            "run_id": run.id,
            "trigger": run.trigger,
            "status": run.status,
            "exit_code": derive_exit_code(run.status, run.error, email_failed),
            "dry_run": bool(run.dry_run),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "started_ts": _epoch(run.started_at),
            "finished_ts": _epoch(run.finished_at),
            "duration_s": duration,
            "new": int(stats.get("new") or 0),
            "updated": int(stats.get("updated") or 0),
            "assessed": int(stats.get("assessed") or 0),
            "priorities": {band.upper(): int(stats.get(band) or 0) for band in ("p1", "p2", "p3", "p4")},
            "triage_calls": int(stats.get("triage_calls") or 0),
            "deep_calls": int(stats.get("deep_calls") or 0),
            "model_cost_usd": float(stats.get("llm_cost_usd") or 0.0),
            "budget_blocked": len(stats.get("budget_blocked_reasons") or []),
            "email_failed": email_failed,
            "sources": [
                {"source_id": s.source_id, "status": s.status, "rows": int(s.listings_seen or 0),
                 "new": int(s.new_count or 0), "updated": int(s.updated_count or 0),
                 "pages": int(s.pages_fetched or 0), "duration_s": s.duration_s}
                for s in per_source
            ],
        }

    last_success: dict[str, Any] | None = None
    good = session.scalar(
        select(ScanRun).where(ScanRun.status.in_(SUCCESS_STATUSES)).order_by(ScanRun.started_at.desc())
    )
    if good is not None:
        last_success = {
            "run_id": good.id, "status": good.status, "started_at": _iso(good.started_at),
            "started_ts": _epoch(good.started_at),
            "seconds_since": max(0.0, (now - good.started_at).total_seconds()),
        }

    sources = []
    for src in session.scalars(select(Source).order_by(Source.id)).all():
        since = max(0.0, (now - src.last_success_at).total_seconds()) if src.last_success_at else None
        sources.append({
            "source_id": src.id, "type": src.type, "enabled": bool(src.enabled), "health": src.health or "unknown",
            "consecutive_failures": int(src.consecutive_failures or 0),
            "in_cooldown": bool(src.cooldown_until and src.cooldown_until > now),
            "last_success_at": _iso(src.last_success_at),
            "seconds_since_last_success": since,
        })

    runs_24h = {status: 0 for status in RUN_STATUSES}
    totals = {"new": 0, "updated": 0, "assessed": 0}
    for row in session.scalars(select(ScanRun).where(ScanRun.started_at >= day_ago)).all():
        runs_24h[row.status] = runs_24h.get(row.status, 0) + 1
        for key in totals:
            totals[key] += int((row.stats or {}).get(key) or 0)
    rows_24h = {
        source_id: int(seen or 0)
        for source_id, seen in session.execute(
            select(SourceScanRun.source_id, func.sum(SourceScanRun.listings_seen))
            .where(SourceScanRun.started_at >= day_ago).group_by(SourceScanRun.source_id)
            .order_by(SourceScanRun.source_id)
        ).all()
    }
    calls, cost, tokens_in, tokens_out = session.execute(
        select(func.count(), func.coalesce(func.sum(AIUsageEvent.est_cost_usd), 0.0),
               func.coalesce(func.sum(AIUsageEvent.input_tokens), 0),
               func.coalesce(func.sum(AIUsageEvent.output_tokens), 0))
        .where(AIUsageEvent.created_at >= day_ago, AIUsageEvent.cache_hit.is_(False),
               AIUsageEvent.result != "budget_blocked")
    ).one()
    emails_24h = {
        status: int(count)
        for status, count in session.execute(
            select(EmailDelivery.status, func.count()).where(EmailDelivery.created_at >= day_ago)
            .group_by(EmailDelivery.status).order_by(EmailDelivery.status)
        ).all()
    }
    return {
        "generated_at": _iso(now),
        "generated_ts": _epoch(now),
        "last_run": last_run,
        "last_success": last_success,
        "sources": sources,
        "last_24h": {
            "runs": runs_24h, **totals, "rows_by_source": rows_24h,
            "model_calls": int(calls or 0), "model_cost_usd": round(float(cost or 0.0), 6),
            "input_tokens": int(tokens_in or 0), "output_tokens": int(tokens_out or 0),
            "emails": emails_24h,
        },
    }


def _label_value(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return repr(round(float(value), 6))


class _Exposition:
    """Collects samples per metric so each family is written together with its HELP and TYPE."""

    def __init__(self) -> None:
        self.families: dict[str, tuple[str, list[str]]] = {}

    def add(self, name: str, help_text: str, value: Any, **labels: Any) -> None:
        if value is None:
            return
        family = self.families.setdefault(name, (help_text, []))
        rendered = ",".join(f'{k}="{_label_value(v)}"' for k, v in labels.items())
        family[1].append(f"{name}{{{rendered}}} {_number(value)}" if rendered else f"{name} {_number(value)}")

    def render(self) -> str:
        lines: list[str] = []
        for name, (help_text, samples) in self.families.items():
            lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", *samples]
        return "\n".join(lines) + "\n"


def render_prometheus(metrics: dict[str, Any]) -> str:
    """Prometheus text exposition format (0.0.4). Every metric is a gauge read from the database."""
    out = _Exposition()
    out.add("chanakya_metrics_generated_timestamp_seconds", "Unix time these metrics were read from the database.",
            metrics.get("generated_ts"))

    success = metrics.get("last_success")
    out.add("chanakya_successful_run_recorded", "1 when at least one run has finished ok or partial, else 0.",
            success is not None)
    if success:
        out.add("chanakya_last_success_timestamp_seconds", "Unix start time of the last ok or partial run.",
                success["started_ts"])
        out.add("chanakya_seconds_since_last_success",
                "Seconds since the last ok or partial run started. Absent until a run has succeeded.",
                success["seconds_since"])

    run = metrics.get("last_run")
    if run:
        out.add("chanakya_last_run_info", "Identity of the latest run. Always 1.", 1,
                run_id=run["run_id"], trigger=run["trigger"], status=run["status"])
        for status in RUN_STATUSES:
            out.add("chanakya_last_run_status", "1 for the status of the latest run, 0 for the others.",
                    run["status"] == status, status=status)
        out.add("chanakya_last_run_exit_code",
                "Exit code the latest run maps to: 0 ok, 1 run error, 2 all sources failed, 3 partial. Absent while running.",
                run["exit_code"])
        out.add("chanakya_last_run_started_timestamp_seconds", "Unix start time of the latest run.", run["started_ts"])
        out.add("chanakya_last_run_duration_seconds", "Duration of the latest run. Absent while running.",
                run["duration_s"])
        for kind in ("new", "updated", "assessed"):
            out.add("chanakya_last_run_notices", "Notices stored as new, updated, or assessed in the latest run.",
                    run[kind], kind=kind)
        for band, count in run["priorities"].items():
            out.add("chanakya_last_run_priority_notices", "Notices assessed into each priority band in the latest run.",
                    count, priority=band)
        out.add("chanakya_last_run_model_calls", "Model calls made in the latest run.", run["triage_calls"], purpose="triage")
        out.add("chanakya_last_run_model_calls", "Model calls made in the latest run.", run["deep_calls"], purpose="deep")
        out.add("chanakya_last_run_model_cost_usd", "Estimated model cost of the latest run in USD.", run["model_cost_usd"])
        out.add("chanakya_last_run_budget_blocked", "Distinct budget limits that blocked model calls in the latest run.",
                run["budget_blocked"])
        for src in run["sources"]:
            out.add("chanakya_last_run_source_rows", "Listing rows seen per source in the latest run.",
                    src["rows"], source_id=src["source_id"])
        for src in run["sources"]:
            out.add("chanakya_last_run_source_ok", "1 when the source finished ok in the latest run, else 0.",
                    src["status"] == "ok", source_id=src["source_id"], status=src["status"])
        for src in run["sources"]:
            out.add("chanakya_last_run_source_duration_seconds", "Scan time per source in the latest run.",
                    src["duration_s"], source_id=src["source_id"])

    sources = metrics.get("sources") or []
    for src in sources:
        out.add("chanakya_source_consecutive_failures", "Scans of this source that have failed in a row.",
                src["consecutive_failures"], source_id=src["source_id"])
    for src in sources:
        out.add("chanakya_source_healthy", "1 when the stored health of the source is ok, else 0.",
                src["health"] == "ok", source_id=src["source_id"], health=src["health"])
    for src in sources:
        out.add("chanakya_source_enabled", "1 when the source is enabled in the config.", src["enabled"],
                source_id=src["source_id"])
    for src in sources:
        out.add("chanakya_source_in_cooldown", "1 while the circuit breaker is holding the source back.",
                src["in_cooldown"], source_id=src["source_id"])
    for src in sources:
        out.add("chanakya_source_seconds_since_last_success",
                "Seconds since the source last scanned ok. Absent until it has.",
                src["seconds_since_last_success"], source_id=src["source_id"])

    day = metrics.get("last_24h") or {}
    for status, count in (day.get("runs") or {}).items():
        out.add("chanakya_runs_24h", "Runs started in the last 24 hours, by status.", count, status=status)
    for kind in ("new", "updated", "assessed"):
        out.add("chanakya_notices_24h", "Notices stored as new, updated, or assessed in the last 24 hours.",
                day.get(kind, 0), kind=kind)
    for source_id, rows in (day.get("rows_by_source") or {}).items():
        out.add("chanakya_source_rows_24h", "Listing rows seen per source in the last 24 hours.", rows, source_id=source_id)
    out.add("chanakya_model_calls_24h", "Model calls made in the last 24 hours (cache hits excluded).",
            day.get("model_calls", 0))
    out.add("chanakya_model_cost_usd_24h", "Estimated model cost in the last 24 hours in USD.", day.get("model_cost_usd", 0.0))
    out.add("chanakya_model_tokens_24h", "Model tokens in the last 24 hours.", day.get("input_tokens", 0), direction="input")
    out.add("chanakya_model_tokens_24h", "Model tokens in the last 24 hours.", day.get("output_tokens", 0), direction="output")
    for status, count in (day.get("emails") or {}).items():
        out.add("chanakya_email_deliveries_24h", "Email delivery rows created in the last 24 hours, by status.",
                count, status=status)
    return out.render()


def render_json(metrics: dict[str, Any]) -> str:
    return json.dumps(metrics, indent=2, default=str)


def metrics_text(session: Any, fmt: str = "prometheus", now: datetime | None = None) -> str:
    """What ``chanakya metrics`` prints and ``GET /api/metrics`` returns."""
    metrics = collect_metrics(session, now)
    return render_json(metrics) if fmt == "json" else render_prometheus(metrics)


def write_metrics_file(path: Path, text: str) -> None:
    """Write atomically: a textfile collector must never read a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
