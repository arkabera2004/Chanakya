"""Phase 9 (database integrity): prove upsert_record cannot produce a duplicate Opportunity
row under genuine concurrent writes for the same tender, not just sequential re-submission
(already covered by test_acquisition_pipeline_integration.py and test_store_documents.py).

Two real threads, each with its own Session against the same database, race to upsert an
identical record. Whichever code path actually executes (one succeeds and the other sees it
and updates; or one succeeds and the other hits the dedup_key unique constraint and errors)
is an implementation detail — the invariant this test enforces is the one the brief asks for:
exactly one row ever exists, never a duplicate, regardless of timing.
"""

from __future__ import annotations

import threading

from sqlalchemy import select

from chanakya.db import Opportunity, init_db, make_engine, session_factory
from chanakya.models import TenderRecord
from chanakya.pipeline.store import upsert_record

from .dbsupport import isolated_database


def _record() -> TenderRecord:
    return TenderRecord(
        source_id="scrapling", platform="scrapling", source_record_key="TID-RACE-1",
        source_tender_id="TID-RACE-1", title="Supply of Widgets (race test)", organisation="Synthetic Dept",
    )


def test_concurrent_upserts_of_the_same_tender_never_duplicate(tmp_path):
    with isolated_database(tmp_path) as url:
        engine = make_engine(url)
        init_db(engine)
        sf = session_factory(engine)

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def worker() -> None:
            with sf() as session:
                barrier.wait(timeout=5)  # maximize the chance both threads race the same window
                try:
                    upsert_record(session, _record(), run_id=None)
                    session.commit()
                except BaseException as exc:  # noqa: BLE001 - a lost race (e.g. IntegrityError) is an expected outcome
                    session.rollback()
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        with sf() as session:
            rows = session.scalars(select(Opportunity).where(Opportunity.source_tender_id == "TID-RACE-1")).all()
            assert len(rows) == 1, f"expected exactly one Opportunity row, found {len(rows)} (errors: {errors})"
