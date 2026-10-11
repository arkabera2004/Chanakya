"""Behaviour that must be the same on SQLite and PostgreSQL.

These run on SQLite by default and on PostgreSQL in the CI job ``test-postgres``.
Each one pins a place where the two databases would otherwise disagree: where
NULL sorts, the order of rows that tie, and case in text matching.
All notices here are SYNTHETIC.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from chanakya import server
from chanakya.app import open_session
from chanakya.db import Opportunity
from chanakya.server import create_app
from chanakya.timeutil import now_utc


def _notice(n: int, *, priority: str | None, score: float | None, closing: datetime | None,
            seen: datetime, caps: list[str] | None = None) -> Opportunity:
    return Opportunity(
        id=f"CHK-syn{n:03d}", dedup_key=f"h:syn{n:03d}", source_id="fixture", platform="Synthetic",
        source_record_key=f"k{n}", title=f"SYNTHETIC notice {n}", content_hash="0" * 64, status="active",
        priority=priority, relevance_score=score, closing_at=closing, first_seen_at=seen, top_capabilities=caps or [],
    )


@pytest.fixture
def desk(settings, monkeypatch):
    monkeypatch.setattr(server, "desk_settings", lambda: settings)
    monkeypatch.setattr(server, "_job", None)
    t0, soon = now_utc() - timedelta(days=1), now_utc() + timedelta(days=5)
    rows = [
        # Inserted out of order on purpose: the list must not depend on insertion order.
        _notice(5, priority="P2", score=60.0, closing=soon, seen=t0 + timedelta(minutes=5)),
        _notice(4, priority="P1", score=None, closing=soon, seen=t0 + timedelta(minutes=4)),
        _notice(3, priority="P1", score=80.0, closing=soon, seen=t0 + timedelta(minutes=3), caps=["CAP-SYN-A"]),
        _notice(2, priority="P1", score=80.0, closing=soon, seen=t0 + timedelta(minutes=2)),
        _notice(1, priority="P1", score=80.0, closing=None, seen=t0 + timedelta(minutes=9)),
        _notice(6, priority=None, score=None, closing=None, seen=t0 + timedelta(minutes=6)),
    ]
    with open_session(settings) as session:
        session.add_all(rows)
        session.commit()
    return settings


def _ids(client: TestClient, **params) -> list[str]:
    body = client.get("/api/opportunities", params={"include_rejected": True, **params}).json()
    return [o["id"] for o in body["opportunities"]]


def test_leads_sort_the_same_on_both_databases(desk):
    """Priority, then score with no score last, then closing with no closing first, then first seen."""
    with TestClient(create_app()) as client:
        assert _ids(client) == ["CHK-syn001", "CHK-syn002", "CHK-syn003", "CHK-syn004", "CHK-syn005", "CHK-syn006"]


def test_paging_through_tied_leads_shows_each_notice_once(desk):
    with TestClient(create_app()) as client:
        pages = [_ids(client, limit=2, offset=offset) for offset in (0, 2, 4)]
        assert [i for page in pages for i in page] == _ids(client)


def test_the_capability_filter_ignores_case_on_both_databases(desk):
    with TestClient(create_app()) as client:
        assert _ids(client, capability="CAP-SYN-A") == ["CHK-syn003"]
        assert _ids(client, capability="cap-syn-a") == ["CHK-syn003"]
        assert _ids(client, q="synthetic NOTICE 5") == ["CHK-syn005"]
