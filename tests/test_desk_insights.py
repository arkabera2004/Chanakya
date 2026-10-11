"""The Costs and Products pages read the same sums as the CLI and the workbook.

Every test runs the desk on a temporary database with the simulated model and a
synthetic inbox, so nothing here touches ``data/`` or the network. The usage
rows and the extra notices added below are SYNTHETIC.
"""

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select

from chanakya import server
from chanakya.app import open_session
from chanakya.db import AIUsageEvent, Opportunity, spend_since
from chanakya.pipeline.knowledge import load_knowledge_base
from chanakya.reporting.excel import export_workbook
from chanakya.reporting.text import plain
from chanakya.server import create_app
from chanakya.timeutil import now_utc, start_of_ist_day_utc

from .conftest import sample_records, write_records


@pytest.fixture
def desk(settings, monkeypatch):
    """A desk on synthetic records, after one pass with the simulated model."""
    write_records(settings.sources[0].path, sample_records())
    monkeypatch.setattr(server, "desk_settings", lambda: settings)
    monkeypatch.setattr(server, "_job", None)
    with TestClient(create_app()) as client:
        assert client.post("/api/runs").status_code == 200
        job = None
        for _ in range(120):
            job = client.get("/api/job").json()["job"]
            if job and job["status"] != "running":
                break
            time.sleep(0.25)
        assert job and job["status"] in ("ok", "partial"), job
    return settings


def _add_usage(settings) -> None:
    """SYNTHETIC usage: paid calls on three days, a cache hit, a refused call, and one row outside the window."""
    now = now_utc()
    today = start_of_ist_day_utc(now)
    rows = [
        # (created_at, model, purpose, result, cache_hit, input, output, cached, cost)
        (now, "gpt-oss:20b", "triage", "ok", False, 900, 110, 0, 0.000096),
        (now, "gpt-oss:20b", "triage", "ok", False, 950, 100, 400, 0.0000825),
        (now, "gpt-oss:120b", "deep", "ok", False, 4200, 800, 0, 0.00111),
        (now, "gpt-oss:20b", "triage", "cache_hit", True, 0, 0, 0, 0.0),
        (now, "gpt-oss:20b", "triage", "budget_blocked", False, 0, 0, 0, 0.0),
        (now, "gpt-oss:120b", "deep", "budget_blocked", False, 0, 0, 0, 0.0),
        (today - timedelta(days=3) + timedelta(hours=5), "gpt-oss:20b", "triage", "ok", False, 1000, 120, 0, 0.000106),
        (today - timedelta(days=12) + timedelta(hours=5), "mystery-model", "deep", "invalid_output", False, 3000, 500, 0, 0.0),
        (today - timedelta(days=45), "gpt-oss:120b", "deep", "ok", False, 5000, 900, 0, 0.00129),
    ]
    with open_session(settings) as session:
        for created, model, purpose, result, hit, tin, tout, cached, cost in rows:
            session.add(AIUsageEvent(
                run_id="run_synthetic", mode="cloud", model=model, purpose=purpose, result=result, cache_hit=hit,
                input_tokens=tin, output_tokens=tout, cached_tokens=cached, est_cost_usd=cost, created_at=created,
            ))
        session.commit()


def _add_notices(settings, kb) -> None:
    """SYNTHETIC notices so more than one offering has a book, plus rows the sheet leaves out."""
    first, second = kb.capabilities[0], kb.capabilities[1]

    def match(cap, score):
        return {"id": cap.id, "score": score, "offering": cap.offering}

    rows = [
        ("A", "P1", 82.0, "active", "candidate", [match(first, 0.7), match(second, 0.2)]),
        ("B", "P3", 41.0, "active", "candidate", [match(first, 0.5)]),
        ("C", "P2", 63.0, "active", "strong", [match(second, 0.6), match(first, 0.3)]),
        ("D", "P2", 60.0, "active", "candidate", []),                                        # no match stored
        ("E", "P1", 90.0, "active", "candidate", [{"id": "CAP-RETIRED", "score": 0.9}]),      # not in the book
        ("F", "P4", 12.0, "active", "low", [match(first, 0.1)]),                             # outside P1 to P3
        ("G", "P1", 88.0, "closed", "candidate", [match(first, 0.8)]),                       # not active
        ("H", "P2", 55.0, "active", "rejected", [match(first, 0.4)]),                        # set aside
    ]
    with open_session(settings) as session:
        for tag, priority, score, status, decision, caps in rows:
            session.add(Opportunity(
                id=f"CHK-SYN{tag}", dedup_key=f"tid:syn{tag}", source_id="fixture", platform="Fixture portal",
                source_record_key=f"syn{tag}", title=f"Synthetic notice {tag}", status=status, priority=priority,
                relevance_score=score, filter_decision=decision, top_capabilities=caps, content_hash=f"syn{tag}",
                analysis_state="skipped_budget" if tag in ("A", "G") else "deferred_no_documents" if tag == "B" else "done",
            ))
        session.commit()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_costs_totals_match_the_usage_rows(desk):
    _add_usage(desk)
    now = now_utc()
    today = start_of_ist_day_utc(now)
    with open_session(desk) as session:
        events = session.scalars(select(AIUsageEvent)).all()
        spent_today = spend_since(session, today)  # the figure the budget guard compares with the day cap

    def expect(since):
        rows = [e for e in events if e.created_at >= since]
        return {
            "calls": sum(1 for e in rows if not e.cache_hit and e.result != "budget_blocked"),
            "cache_hits": sum(1 for e in rows if e.cache_hit),
            "blocked": sum(1 for e in rows if e.result == "budget_blocked"),
            "input_tokens": sum(e.input_tokens for e in rows),
            "output_tokens": sum(e.output_tokens for e in rows),
            "cached_tokens": sum(e.cached_tokens for e in rows),
            "cost_usd": sum(e.est_cost_usd for e in rows),
        }

    def same(got, want):
        for key, value in want.items():
            assert got[key] == pytest.approx(value), key

    with TestClient(create_app()) as client:
        body = client.get("/api/costs").json()

    same(body["spend"]["today"], expect(today))
    same(body["spend"]["last_7_days"], expect(today - timedelta(days=6)))
    same(body["spend"]["last_30_days"], expect(today - timedelta(days=29)))
    same(body["totals"], expect(today - timedelta(days=29)))
    assert body["spend"]["today"]["cost_usd"] == pytest.approx(spent_today)
    assert body["spend"]["today"]["cost_usd"] == pytest.approx(0.0012885)   # a real cost is never shown as zero
    assert body["spend"]["last_7_days"]["cost_usd"] > body["spend"]["today"]["cost_usd"]
    assert body["spend"]["last_30_days"]["cost_usd"] < sum(e.est_cost_usd for e in events)  # the 45 day old row is out

    # The chart is the same 30 days, oldest first, and adds up to the total.
    daily = body["daily"]
    assert len(daily) == body["window_days"] == 30
    assert [d["date"] for d in daily] == sorted(d["date"] for d in daily)
    assert sum(d["cost_usd"] for d in daily) == pytest.approx(body["totals"]["cost_usd"])
    assert sum(d["calls"] for d in daily) == body["totals"]["calls"]
    assert daily[-1]["cost_usd"] == pytest.approx(body["spend"]["today"]["cost_usd"])
    assert daily[-4]["cost_usd"] == pytest.approx(0.000106)

    # By purpose and by model are two cuts of the same rows.
    for cut in ("by_purpose", "by_model"):
        for key in ("calls", "cache_hits", "blocked", "input_tokens", "output_tokens", "cost_usd"):
            assert sum(row[key] for row in body[cut]) == pytest.approx(body["totals"][key]), (cut, key)
    purposes = {row["purpose"]: row for row in body["by_purpose"]}
    assert {"triage", "deep"} <= set(purposes)
    synthetic = [e for e in events if e.run_id == "run_synthetic" and e.created_at >= today - timedelta(days=29)]
    fake = [e for e in events if e.run_id != "run_synthetic"]
    assert purposes["deep"]["blocked"] == 1 + sum(1 for e in fake if e.purpose == "deep" and e.result == "budget_blocked")
    assert sum(row["blocked"] for row in body["by_purpose"]) >= 2
    assert sum(row["cache_hits"] for row in body["by_purpose"]) >= 1
    assert len(synthetic) == 8
    models = {row["model"]: row for row in body["by_model"]}
    assert models["gpt-oss:120b"]["priced"] is True
    assert models["mystery-model"]["priced"] is False and models["mystery-model"]["calls"] == 1


def test_costs_match_the_cli_lines(desk):
    """``chanakya costs`` and the desk read one function, so their totals are the same number."""
    from chanakya.reporting.insights import daily_window_start, usage_by_model_purpose, usage_total

    _add_usage(desk)
    with open_session(desk) as session:
        total = usage_total(usage_by_model_purpose(session, daily_window_start(30)))
    with TestClient(create_app()) as client:
        body = client.get("/api/costs").json()
    assert body["totals"]["cost_usd"] == pytest.approx(total.cost_usd)
    assert (body["totals"]["calls"], body["totals"]["cache_hits"], body["totals"]["blocked"]) == (
        total.calls, total.cache_hits, total.blocked)


def test_budget_figures_come_from_settings(desk, kb):
    desk.budget.max_cost_per_day_usd = 1.25
    desk.budget.max_cost_per_run_usd = 0.4
    desk.budget.max_calls_per_run = 17
    desk.budget.max_deep_analyses_per_run = 3
    _add_notices(desk, kb)
    with TestClient(create_app()) as client:
        body = client.get("/api/costs").json()
    assert body["budget"] == {
        "max_cost_per_day_usd": 1.25, "max_cost_per_run_usd": 0.4, "max_calls_per_run": 17, "max_deep_analyses_per_run": 3,
    }
    assert body["llm_mode"] == "fake" and body["demo"] is True
    # Only active notices wait: the closed one is never sent to a model.
    assert body["waiting"]["budget"] == 1
    assert body["waiting"]["documents"] >= 1
    assert body["last_run"]["id"].startswith("run_") and body["last_run"]["status"] in ("ok", "partial")


def test_costs_on_an_empty_desk(settings, monkeypatch):
    monkeypatch.setattr(server, "desk_settings", lambda: settings)
    with TestClient(create_app()) as client:
        body = client.get("/api/costs").json()
        products = client.get("/api/products").json()
    assert body["totals"]["calls"] == 0 and body["totals"]["cost_usd"] == 0
    assert body["last_run"] is None and len(body["daily"]) == 30
    assert products["total"] == 0 and all(group["total"] == 0 and group["notices"] == [] for group in products["groups"])


def test_products_match_the_product_wise_sheet(desk, kb, tmp_path):
    _add_notices(desk, kb)
    with open_session(desk) as session:
        path, _ = export_workbook(session, desk, kb, None, out_dir=tmp_path / "book")
    sheet = load_workbook(path)["Product-Wise"]
    rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2) if row[0].value]
    in_sheet = [(row[1], row[3], row[4]) for row in rows]  # capability id, opportunity id, priority
    assert in_sheet, "the synthetic book should fill the sheet"

    with TestClient(create_app()) as client:
        body = client.get("/api/products", params={"top": 50}).json()
        short = client.get("/api/products", params={"top": 1}).json()

    # One section per capability in the book, in the book's order, empty ones included.
    assert [group["id"] for group in body["groups"]] == [cap.id for cap in kb.capabilities]
    on_page = [(group["id"], notice["id"], notice["priority"]) for group in body["groups"] for notice in group["notices"]]
    assert on_page == in_sheet

    first, second = body["groups"][0], body["groups"][1]
    assert {"CHK-SYNA", "CHK-SYNB"} <= {notice["id"] for notice in first["notices"]}
    assert "CHK-SYNC" in {notice["id"] for notice in second["notices"]}
    shown = {notice["id"] for group in body["groups"] for notice in group["notices"]}
    assert not shown & {"CHK-SYND", "CHK-SYNE", "CHK-SYNF", "CHK-SYNG", "CHK-SYNH"}
    for group in body["groups"]:
        assert group["total"] == len(group["notices"]) == sum(group["bands"].values())
        assert group["more"] == 0
        scores = [notice["score"] for notice in group["notices"]]
        assert scores == sorted(scores, reverse=True)
    assert any(group["total"] == 0 for group in body["groups"])
    assert body["total"] == len(in_sheet) + body["ungrouped"] and body["ungrouped"] == 2
    assert sum(body["bands"].values()) == body["total"]
    a = next(notice for notice in first["notices"] if notice["id"] == "CHK-SYNA")
    assert a["capability_score"] == 0.7

    # ``top`` trims the list, never the count.
    for full, cut in zip(body["groups"], short["groups"], strict=True):
        assert cut["total"] == full["total"] and len(cut["notices"]) == min(1, full["total"])
        assert cut["more"] == full["total"] - len(cut["notices"])
        assert cut["notices"] == full["notices"][:1]


def test_capabilities_match_the_knowledge_base(desk):
    kb = load_knowledge_base(desk.app.capabilities_file)
    with TestClient(create_app()) as client:
        body = client.get("/api/capabilities").json()
        products = client.get("/api/products").json()
    assert body["kb_version"] == kb.kb_version
    assert [cap["id"] for cap in body["capabilities"]] == [cap.id for cap in kb.capabilities]
    for shown, cap in zip(body["capabilities"], kb.capabilities, strict=True):
        assert shown == {
            "id": cap.id, "name": plain(cap.name), "offering": plain(cap.offering),
            "offering_status": cap.offering_status, "confidence": cap.confidence,
        }
    for group, cap in zip(products["groups"], kb.capabilities, strict=True):
        assert (group["name"], group["offering"]) == (plain(cap.name), plain(cap.offering))


def test_insight_text_has_no_dashes(desk, kb):
    _add_usage(desk)
    _add_notices(desk, kb)
    assert any("—" in cap.offering for cap in kb.capabilities), "the book should exercise plain()"
    with TestClient(create_app()) as client:
        bodies = [
            client.get("/api/costs").json(),
            client.get("/api/products", params={"top": 50}).json(),
            client.get("/api/capabilities").json(),
        ]
    dashed = [text for body in bodies for text in _strings(body) if "—" in text or "–" in text]
    assert not dashed
