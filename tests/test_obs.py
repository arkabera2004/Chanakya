"""Structured logs, the run_finished event and metrics export (obs.py).

Everything runs on the temporary database, the synthetic fixture source and the
simulated model. No network, no real key.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import datetime, timedelta

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from typer.testing import CliRunner

from chanakya import app as app_module
from chanakya import obs, server
from chanakya.app import run_pipeline
from chanakya.cli import app as cli_app
from chanakya.config import Settings
from chanakya.db import ScanRun, Source
from chanakya.llm import FakeProvider
from chanakya.pipeline.runner import Pipeline
from chanakya.timeutil import now_utc

from .conftest import ROOT, sample_records, write_records

FAKE_KEY = "sk-synthetic-ollama-key-0123456789"      # synthetic values, never real credentials
FAKE_SMTP = "synthetic-smtp-pass-9876"
FAKE_CUSTOM = "synthetic-custom-secret-4455"


@pytest.fixture(autouse=True)
def clean_logging(monkeypatch):
    """Each test starts with no remembered secrets and leaves no handler behind."""
    monkeypatch.setattr(obs, "_registered_secrets", set())
    monkeypatch.delenv(obs.LOG_FORMAT_ENV, raising=False)
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_chanakya", False):
            root.removeHandler(handler)
    logging.getLogger("chanakya").setLevel(logging.NOTSET)


def capture(settings=None, fmt: str = "json") -> tuple[logging.Logger, io.StringIO]:
    """A private logger wired the way configure_logging wires the root one."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(obs.RedactionFilter(settings))
    handler.setFormatter(obs.JsonFormatter(settings) if fmt == "json" else obs.TextFormatter(settings))
    logger = logging.getLogger(f"chanakya.test_obs.{fmt}.{id(stream)}")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def finished(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == obs.RUN_FINISHED]


def parse_prometheus(text: str) -> dict[str, float]:
    """Strict enough to catch a malformed line: every sample must match the exposition grammar."""
    samples: dict[str, float] = {}
    helped: set[str] = set()
    for line in text.splitlines():
        if line.startswith("# HELP "):
            helped.add(line.split()[2])
            continue
        if line.startswith("# TYPE "):
            assert line.split()[3] == "gauge"
            continue
        match = re.fullmatch(r'([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[a-zA-Z_][a-zA-Z0-9_]*="[^"\n]*"'
                             r'(?:,[a-zA-Z_][a-zA-Z0-9_]*="[^"\n]*")*\})? (-?[0-9.e+-]+)', line)
        assert match, f"not a valid sample line: {line!r}"
        assert match.group(1) in helped, f"sample before its HELP: {line!r}"
        key = match.group(1) + (match.group(2) or "")
        assert key not in samples, f"duplicate sample: {key}"
        samples[key] = float(match.group(3))
    return samples


# ---------------------------------------------------------------- formatter
def test_json_formatter_writes_one_parseable_object_with_extras():
    logger, stream = capture()
    logger.info("scan %s finished", "cppp_central",
                extra={"event": "source_finished", "run_id": "run_x", "source_id": "cppp_central", "rows": 42,
                       "nested": {"pages": 3}})
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["level"] == "INFO" and row["message"] == "scan cppp_central finished"
    assert row["logger"].startswith("chanakya.test_obs")
    assert row["event"] == "source_finished" and row["run_id"] == "run_x" and row["source_id"] == "cppp_central"
    assert row["rows"] == 42 and row["nested"] == {"pages": 3}
    assert row["timestamp"].endswith("Z")
    stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
    assert stamp.utcoffset() == timedelta(0)
    assert abs((stamp.replace(tzinfo=None) - now_utc()).total_seconds()) < 60


def test_json_formatter_keeps_exceptions_on_the_same_line():
    logger, stream = capture()
    try:
        raise ValueError("portal layout changed")
    except ValueError:
        logger.exception("run failed", extra={"run_id": "run_y"})
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["level"] == "ERROR" and row["run_id"] == "run_y"
    assert "ValueError: portal layout changed" in row["exception"]


def test_text_format_is_unchanged_for_plain_messages():
    logger, stream = capture(fmt="text")
    logger.warning("cannot read %s", "notice.pdf")
    line = stream.getvalue().strip()
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} WARNING chanakya\.test_obs\S* cannot read notice\.pdf", line)


# ---------------------------------------------------------------- redaction
@pytest.mark.parametrize("fmt", ["json", "text"])
def test_no_secret_from_settings_reaches_a_log_line(settings, monkeypatch, fmt):
    monkeypatch.setenv(settings.llm.api_key_env, FAKE_KEY)
    monkeypatch.setenv(settings.email.password_env, FAKE_SMTP)
    monkeypatch.setenv("SOME_OTHER_SECRET_ENV", FAKE_CUSTOM)
    assert settings.secret("SOME_OTHER_SECRET_ENV") == FAKE_CUSTOM     # handing it out registers it
    logger, stream = capture(settings, fmt)

    logger.error("auth failed for key %s and smtp %s", FAKE_KEY, FAKE_SMTP)
    logger.warning("custom secret in the text: %s", FAKE_CUSTOM,
                   extra={"detail": f"header Authorization: Bearer {FAKE_KEY}", "nested": {"values": [FAKE_SMTP]},
                          "api_key": "short", "smtp_password": FAKE_SMTP})
    try:
        raise RuntimeError(f"401 from ollama with {FAKE_KEY} and {FAKE_CUSTOM}")
    except RuntimeError:
        logger.exception("call failed")

    out = stream.getvalue()
    if fmt == "json":
        assert len(out.splitlines()) == 3              # the traceback stays inside its own line
    for secret in (FAKE_KEY, FAKE_SMTP, FAKE_CUSTOM):
        assert secret not in out
    assert obs.REDACTED in out
    if fmt == "json":
        rows = [json.loads(line) for line in out.splitlines()]
        assert rows[1]["api_key"] == obs.REDACTED and rows[1]["smtp_password"] == obs.REDACTED
        assert "RuntimeError" in rows[2]["exception"]


def test_a_database_password_is_redacted(settings):
    settings.app.database_url = "postgresql+psycopg://chanakya:synthetic-db-pass-11@db.internal/chanakya"
    logger, stream = capture(settings)
    logger.error("cannot connect to %s", settings.app.database_url)
    assert "synthetic-db-pass-11" not in stream.getvalue()


def test_prompts_and_documents_cannot_be_logged_in_full(settings):
    prompt = "SYSTEM: you are an analyst. " + "Tender clause text. " * 400       # about 8,000 characters
    document = "Section 4 eligibility. " * 2000
    logger, stream = capture(settings)
    logger.debug("sending prompt: %s", prompt,
                 extra={"prompt": prompt, "messages": [{"role": "user", "content": prompt}],
                        "document_text": document, "note": document, "input_tokens": 1800})
    row = json.loads(stream.getvalue())
    assert prompt not in stream.getvalue() and document not in stream.getvalue()
    assert len(row["message"]) < obs.MAX_MESSAGE_CHARS + 60 and "truncated" in row["message"]
    assert row["prompt"].startswith("[omitted ") and row["messages"].startswith("[omitted ")
    assert row["document_text"].startswith("[omitted ")
    assert len(row["note"]) < obs.MAX_FIELD_CHARS + 60 and "truncated" in row["note"]
    assert row["input_tokens"] == 1800                                             # a count is not a secret
    assert len(stream.getvalue()) < 4000


def test_the_filter_leaves_the_original_record_for_other_handlers():
    record = logging.LogRecord("chanakya.x", logging.INFO, __file__, 1, "key %s", ("Bearer abcdefgh12345678",), None)
    clean = obs.RedactionFilter().filter(record)
    assert clean is not record and record.args == ("Bearer abcdefgh12345678",)
    assert "abcdefgh12345678" not in clean.getMessage()


# ---------------------------------------------------------------- configuration
def test_log_format_is_strict_config_with_an_env_override(settings, monkeypatch):
    assert Settings().app.log_format == "text"
    assert Settings.model_validate({"app": {"log_format": "json"}}).app.log_format == "json"
    with pytest.raises(ValidationError):
        Settings.model_validate({"app": {"log_format": "xml"}})
    assert obs.resolve_log_format(settings) == "text"
    settings.app.log_format = "json"
    assert obs.resolve_log_format(settings) == "json"
    monkeypatch.setenv(obs.LOG_FORMAT_ENV, "text")
    assert obs.resolve_log_format(settings) == "text"
    monkeypatch.setenv(obs.LOG_FORMAT_ENV, "JSON")
    assert obs.resolve_log_format(None) == "json"
    monkeypatch.setenv(obs.LOG_FORMAT_ENV, "nonsense")                    # ignored, the config decides
    assert obs.resolve_log_format(settings) == "json"


@pytest.mark.parametrize("name", ["chanakya.yaml", "chanakya.example.yaml"])
def test_both_config_files_declare_log_format(name):
    path = ROOT / "config" / name
    if not path.exists():      # chanakya.yaml is a local file and may be absent on CI
        pytest.skip(f"{name} not present")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["app"]["log_format"] in obs.LOG_FORMATS
    assert Settings.model_validate(data).app.log_format == "text"


def test_configure_logging_installs_one_handler_and_switches_format(settings):
    root = logging.getLogger()

    def mine() -> list[logging.Handler]:
        return [h for h in root.handlers if getattr(h, "_chanakya", False)]

    stream = io.StringIO()
    assert obs.configure_logging(settings, verbose=False, stream=stream) == "text"
    settings.app.log_format = "json"
    assert obs.configure_logging(settings, verbose=False, stream=stream) == "json"
    assert len(mine()) == 1 and isinstance(mine()[0].formatter, obs.JsonFormatter)
    logging.getLogger("chanakya.obs").info("hello", extra={"event": "probe"})
    logging.getLogger("some.library").info("not ours, stays below the root level")
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [r["event"] for r in rows] == ["probe"]


# ---------------------------------------------------------------- run_finished
def test_run_finished_is_emitted_once_with_the_run_numbers(settings, session, caplog):
    write_records(settings.sources[0].path, sample_records())
    with caplog.at_level(logging.INFO, logger="chanakya.obs"):
        result = run_pipeline(settings, session, email=True, provider=FakeProvider(), trigger="schedule")
    events = finished(caplog)
    assert len(events) == 1
    e = events[0]
    assert e.levelno == logging.INFO and e.name == "chanakya.obs"
    assert e.run_id == result.run.id and e.trigger == "schedule"
    assert e.status == "ok" and e.exit_code == result.exit_code == 0
    assert isinstance(e.duration_s, float) and e.duration_s >= 0
    assert e.sources == {"fixture": {"status": "ok", "rows": 6, "new": 6, "updated": 0, "pages": e.sources["fixture"]["pages"],
                                     "duration_s": e.sources["fixture"]["duration_s"], "error": None}}
    assert e.rows_total == 6 and e.new == 6 and e.updated == 0 and e.assessed == 6
    s = result.stats
    assert e.priorities == {"P1": s.p1, "P2": s.p2, "P3": s.p3, "P4": s.p4} and sum(e.priorities.values()) == 6
    assert e.model_calls == s.triage_calls + s.deep_calls > 0
    assert e.model_cost_usd == s.llm_cost_usd and e.model_mode == "fake"
    assert e.budget_blocked_reasons == []
    assert e.email_sent is True and e.email_deliveries == {"sent": 2}
    assert e.report_written is True and e.error is None

    # The record survives the JSON formatter as one parseable line with the same fields.
    row = json.loads(obs.JsonFormatter(settings).format(e))
    assert row["event"] == "run_finished" and row["run_id"] == result.run.id and row["exit_code"] == 0
    assert row["sources"]["fixture"]["rows"] == 6 and row["priorities"] == e.priorities
    assert row["message"].startswith(f"run_finished run_id={result.run.id} trigger=schedule status=ok exit_code=0")


def test_run_finished_reports_budget_blocks_and_no_email(settings, session, caplog):
    write_records(settings.sources[0].path, sample_records())
    settings.budget.max_calls_per_run = 0
    with caplog.at_level(logging.INFO, logger="chanakya.obs"):
        result = run_pipeline(settings, session, provider=FakeProvider())
    (e,) = finished(caplog)
    assert e.budget_blocked_reasons == result.stats.budget_blocked_reasons != []
    assert e.model_calls == 0 and e.model_skipped_budget == result.stats.llm_skipped_budget > 0
    assert e.email_sent is False and e.email_deliveries == {}


def test_a_dry_run_preview_is_not_counted_as_email_sent(settings, session, caplog):
    write_records(settings.sources[0].path, sample_records())
    with caplog.at_level(logging.INFO, logger="chanakya.obs"):
        run_pipeline(settings, session, dry_run=True, provider=FakeProvider())
    (e,) = finished(caplog)
    assert e.dry_run is True and e.email_sent is False and e.email_deliveries == {"dry_run": 2}


def test_a_scan_only_run_and_an_analyze_run_each_emit_one_event(settings, session, caplog):
    write_records(settings.sources[0].path, sample_records())
    with caplog.at_level(logging.INFO, logger="chanakya.obs"):
        scanned = run_pipeline(settings, session, skip_assess=True, export=False, trigger="scan")
        analysed = run_pipeline(settings, session, skip_scan=True, export=False, trigger="analyze", provider=FakeProvider())
    scan_event, analyze_event = finished(caplog)
    assert scan_event.trigger == "scan" and scan_event.run_id == scanned.run.id
    assert scan_event.new == 6 and scan_event.assessed == 0 and scan_event.model_calls == 0
    assert scan_event.report_written is False
    assert analyze_event.trigger == "analyze" and analyze_event.run_id == analysed.run.id
    assert analyze_event.sources == {} and analyze_event.assessed == 6


def test_a_failed_run_emits_one_error_event_with_exit_code_1(settings, session, caplog, monkeypatch):
    write_records(settings.sources[0].path, sample_records())

    def boom(self, *args, **kwargs):
        raise RuntimeError("assessment broke")

    monkeypatch.setattr(Pipeline, "assess", boom)
    with caplog.at_level(logging.INFO, logger="chanakya.obs"):
        result = run_pipeline(settings, session, provider=FakeProvider())
    (e,) = finished(caplog)
    assert result.exit_code == 1
    assert e.levelno == logging.ERROR and e.status == "failed" and e.exit_code == 1
    assert e.error == "RuntimeError: assessment broke" and e.run_id == result.run.id


def test_an_all_sources_failed_run_is_exit_code_2(settings, session, caplog, monkeypatch):
    from chanakya.http import FetchError

    class DownConnector:
        supports_detail = False

        def discover(self, ctx):
            raise FetchError(f"portal down, sent Authorization: Bearer {FAKE_KEY}")

    monkeypatch.setattr("chanakya.pipeline.runner.build_connector", lambda cfg, client: DownConnector())
    with caplog.at_level(logging.INFO, logger="chanakya.obs"):
        result = run_pipeline(settings, session, provider=FakeProvider(), export=False)
    (e,) = finished(caplog)
    assert result.exit_code == 2
    assert e.exit_code == 2 and e.status == "failed" and e.levelno == logging.ERROR
    assert e.sources["fixture"]["status"] == "failed" and e.sources["fixture"]["rows"] == 0
    line = obs.JsonFormatter(settings).format(e)          # a source error is redacted on its way out
    assert FAKE_KEY not in line and "portal down" in json.loads(line)["sources"]["fixture"]["error"]


def test_a_crash_outside_the_run_still_emits_exactly_one_event(settings, session, caplog, monkeypatch):
    write_records(settings.sources[0].path, sample_records())

    def boom(*args, **kwargs):
        raise OSError("disk full while writing the workbook")

    monkeypatch.setattr(app_module, "export_workbook", boom)
    with caplog.at_level(logging.INFO, logger="chanakya.obs"), pytest.raises(OSError):
        run_pipeline(settings, session, provider=FakeProvider(), trigger="watch")
    (e,) = finished(caplog)
    assert e.status == "crashed" and e.exit_code == 1 and e.trigger == "watch" and e.levelno == logging.ERROR
    assert e.error == "OSError: disk full while writing the workbook"


# ---------------------------------------------------------------- metrics
def test_metrics_before_any_run_have_no_staleness_gauge(session):
    samples = parse_prometheus(obs.metrics_text(session))
    assert samples["chanakya_successful_run_recorded"] == 0
    assert "chanakya_seconds_since_last_success" not in samples
    assert not any(k.startswith("chanakya_last_run_") for k in samples)
    assert samples["chanakya_model_calls_24h"] == 0
    data = json.loads(obs.metrics_text(session, "json"))
    assert data["last_run"] is None and data["last_success"] is None and data["sources"] == []


def test_metrics_text_parses_and_matches_the_run(settings, session):
    write_records(settings.sources[0].path, sample_records())
    result = run_pipeline(settings, session, email=True, provider=FakeProvider())
    text = obs.metrics_text(session)
    assert text.endswith("\n")
    m = parse_prometheus(text)
    rid = result.run.id
    assert m[f'chanakya_last_run_info{{run_id="{rid}",trigger="manual",status="ok"}}'] == 1
    assert m['chanakya_last_run_status{status="ok"}'] == 1 and m['chanakya_last_run_status{status="failed"}'] == 0
    assert m["chanakya_last_run_exit_code"] == 0
    assert m["chanakya_last_run_duration_seconds"] >= 0
    assert m['chanakya_last_run_notices{kind="new"}'] == 6 and m['chanakya_last_run_notices{kind="assessed"}'] == 6
    s = result.stats
    assert [m[f'chanakya_last_run_priority_notices{{priority="P{i}"}}'] for i in (1, 2, 3, 4)] == [s.p1, s.p2, s.p3, s.p4]
    assert m['chanakya_last_run_model_calls{purpose="triage"}'] == s.triage_calls
    assert m['chanakya_last_run_model_calls{purpose="deep"}'] == s.deep_calls
    assert m["chanakya_last_run_model_cost_usd"] == pytest.approx(s.llm_cost_usd)
    assert m['chanakya_last_run_source_rows{source_id="fixture"}'] == 6
    assert m['chanakya_last_run_source_ok{source_id="fixture",status="ok"}'] == 1
    assert m['chanakya_source_consecutive_failures{source_id="fixture"}'] == 0
    assert m['chanakya_source_healthy{source_id="fixture",health="ok"}'] == 1
    assert m['chanakya_runs_24h{status="ok"}'] == 1
    assert m['chanakya_notices_24h{kind="new"}'] == 6
    assert m['chanakya_source_rows_24h{source_id="fixture"}'] == 6
    assert m["chanakya_model_calls_24h"] == s.triage_calls + s.deep_calls
    assert m["chanakya_model_cost_usd_24h"] == pytest.approx(s.llm_cost_usd, abs=1e-6)
    assert m['chanakya_email_deliveries_24h{status="sent"}'] == 2
    assert m["chanakya_successful_run_recorded"] == 1
    assert 0 <= m["chanakya_seconds_since_last_success"] < 120

    data = json.loads(obs.metrics_text(session, "json"))
    assert data["last_run"]["run_id"] == rid and data["last_run"]["exit_code"] == 0
    assert data["last_24h"]["runs"]["ok"] == 1 and data["sources"][0]["source_id"] == "fixture"


def test_the_staleness_gauge_grows_and_ignores_failed_runs(settings, session):
    write_records(settings.sources[0].path, sample_records())
    good = run_pipeline(settings, session, provider=FakeProvider(), export=False)
    started = session.get(ScanRun, good.run.id).started_at

    later = started + timedelta(hours=40)
    stale = obs.collect_metrics(session, now=later)
    assert stale["last_success"]["seconds_since"] == pytest.approx(40 * 3600)
    assert parse_prometheus(obs.render_prometheus(stale))["chanakya_seconds_since_last_success"] == pytest.approx(144000)
    assert stale["last_24h"]["runs"]["ok"] == 0 and stale["last_24h"]["new"] == 0      # outside the window now

    # A newer run that failed is the latest run, but it does not reset the gauge.
    session.add(ScanRun(id="run_failed_later", trigger="schedule", status="failed", stats={},
                        started_at=started + timedelta(hours=39), finished_at=started + timedelta(hours=39, minutes=1),
                        error="RuntimeError: synthetic failure"))
    src = session.get(Source, "fixture")
    src.consecutive_failures, src.health = 4, "failing"
    session.commit()
    m = parse_prometheus(obs.metrics_text(session, now=later))
    assert m["chanakya_seconds_since_last_success"] == pytest.approx(144000)
    assert m['chanakya_last_run_status{status="failed"}'] == 1 and m["chanakya_last_run_exit_code"] == 1
    assert m["chanakya_last_run_duration_seconds"] == 60
    assert m['chanakya_runs_24h{status="failed"}'] == 1
    assert m['chanakya_source_consecutive_failures{source_id="fixture"}'] == 4
    assert m['chanakya_source_healthy{source_id="fixture",health="failing"}'] == 0
    assert m['chanakya_source_seconds_since_last_success{source_id="fixture"}'] > 39 * 3600

    # A fresh success brings it back down.
    session.add(ScanRun(id="run_ok_latest", trigger="schedule", status="partial", stats={},
                        started_at=later - timedelta(minutes=5), finished_at=later))
    session.commit()
    fresh = parse_prometheus(obs.metrics_text(session, now=later))
    assert fresh["chanakya_seconds_since_last_success"] == pytest.approx(300)
    assert fresh["chanakya_last_run_exit_code"] == 3


def test_exit_code_derivation_matches_the_cli_contract():
    assert obs.derive_exit_code("ok", None) == 0
    assert obs.derive_exit_code("failed", "RuntimeError: x") == 1
    assert obs.derive_exit_code("failed", None) == 2
    assert obs.derive_exit_code("partial", None) == 3
    assert obs.derive_exit_code("ok", None, email_failed=True) == 3
    assert obs.derive_exit_code("running", None) is None


def test_label_values_are_escaped():
    text = obs.render_prometheus({"sources": [{
        "source_id": 'odd"id\\with\nnewline', "health": "ok", "consecutive_failures": 1, "enabled": True,
        "in_cooldown": False, "seconds_since_last_success": None}]})
    assert 'chanakya_source_consecutive_failures{source_id="odd\\"id\\\\with\\nnewline"} 1' in text.splitlines()


# ---------------------------------------------------------------- CLI and desk API
def _config_file(settings, tmp_path):
    cfg = tmp_path / "config" / "chanakya.yaml"
    cfg.parent.mkdir()
    data = settings.model_dump(mode="json", exclude={"config_path"})
    data["app"]["capabilities_file"] = str(settings.app.capabilities_file)
    cfg.write_text(yaml.safe_dump(data), encoding="utf-8")
    return cfg


def _json_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def test_cli_scan_logs_run_finished_as_json_and_metrics_reads_it_back(settings, tmp_path, monkeypatch):
    write_records(settings.sources[0].path, sample_records())
    monkeypatch.setattr("chanakya.desklock.lock_path", lambda st: tmp_path / "writer.lock")
    cfg = _config_file(settings, tmp_path)
    runner = CliRunner()

    res = runner.invoke(cli_app, ["--config", str(cfg), "scan"], env={obs.LOG_FORMAT_ENV: "json"})
    assert res.exit_code == 0, res.output
    logged = res.output + (getattr(res, "stderr", "") or "")
    events = {json.dumps(row, sort_keys=True): row for row in _json_lines(logged) if row.get("event") == "run_finished"}
    assert len(events) == 1, logged
    (event,) = events.values()
    assert event["trigger"] == "scan" and event["status"] == "ok" and event["exit_code"] == 0
    assert event["new"] == 6 and event["assessed"] == 0 and event["sources"]["fixture"]["rows"] == 6

    prom = runner.invoke(cli_app, ["--config", str(cfg), "metrics"])
    assert prom.exit_code == 0, prom.output
    samples = parse_prometheus(prom.stdout)
    assert samples['chanakya_last_run_notices{kind="new"}'] == 6
    assert "chanakya_seconds_since_last_success" in samples

    as_json = runner.invoke(cli_app, ["--config", str(cfg), "metrics", "--format", "json"])
    assert as_json.exit_code == 0 and json.loads(as_json.stdout)["last_run"]["trigger"] == "scan"

    target = tmp_path / "textfile" / "chanakya.prom"
    written = runner.invoke(cli_app, ["--config", str(cfg), "metrics", "--out", str(target)])
    assert written.exit_code == 0 and written.stdout == ""
    assert parse_prometheus(target.read_text(encoding="utf-8"))["chanakya_successful_run_recorded"] == 1
    assert not list(target.parent.glob("*.tmp"))

    bad = runner.invoke(cli_app, ["--config", str(cfg), "metrics", "--format", "xml"])
    assert bad.exit_code == 1


def test_api_metrics_returns_the_same_exposition(settings, session, monkeypatch):
    write_records(settings.sources[0].path, sample_records())
    monkeypatch.setattr(server, "desk_settings", lambda: settings)
    monkeypatch.setattr(server, "_job", None)
    with TestClient(server.create_app()) as client:
        empty = client.get("/api/metrics")
        assert empty.status_code == 200
        assert empty.headers["content-type"].startswith("text/plain")
        assert parse_prometheus(empty.text)["chanakya_successful_run_recorded"] == 0

        result = run_pipeline(settings, session, provider=FakeProvider(), export=False)
        response = client.get("/api/metrics")
        assert response.status_code == 200
        samples = parse_prometheus(response.text)
        assert samples["chanakya_successful_run_recorded"] == 1
        assert samples['chanakya_last_run_source_rows{source_id="fixture"}'] == 6
        assert f'run_id="{result.run.id}"' in response.text
        # Same function as the CLI: only the two wall-clock gauges may differ between two reads.
        clock = ("chanakya_metrics_generated_timestamp_seconds", "chanakya_seconds_since_last_success",
                 "chanakya_source_seconds_since_last_success")
        direct = parse_prometheus(obs.metrics_text(session))
        assert {k: v for k, v in samples.items() if not k.startswith(clock)} == \
               {k: v for k, v in direct.items() if not k.startswith(clock)}

        as_json = client.get("/api/metrics", params={"format": "json"})
        assert as_json.status_code == 200 and as_json.json()["last_run"]["run_id"] == result.run.id
        assert client.get("/api/metrics", params={"format": "xml"}).status_code == 400


def test_run_rows_survive_in_the_database_for_metrics(settings, session):
    """The numbers come from scan_runs, so a monitor reads them without the process that made them."""
    write_records(settings.sources[0].path, sample_records())
    run_pipeline(settings, session, provider=FakeProvider(), export=False)
    stored = session.scalar(select(ScanRun))
    assert obs.collect_metrics(session)["last_run"]["new"] == stored.stats["new"] == 6
