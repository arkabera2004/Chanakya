"""Deterministic crawling-reliability benchmark.

Each case in ``tests/fixtures/benchmark/crawling_cases.yaml`` points at an existing fixture and a
known-correct outcome (copied from the assertions already proven in test_connectors.py /
test_awards.py, not invented). Running this file against those fixtures is the repeatable
"before/after" comparison described in docs/CRAWLING_BENCHMARK_SPECIFICATION.md.

Scope: this harness exercises extraction-stage correctness and failure classification from
in-memory HTML, not network retrieval (bounded retries, redirects, download limits — those are
already covered by the test_http_* cases in test_connectors.py, which do mock the transport).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from chanakya.connectors import CPPPConnector, GePNICConnector
from chanakya.connectors.cppp_awards import CPPPAwardsConnector
from chanakya.config import AwardsSettings, SourceConfig
from chanakya.http import BlockedError

from .conftest import FIXTURES, ROOT

MANIFEST = ROOT / "tests" / "fixtures" / "benchmark" / "crawling_cases.yaml"
CASES = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["cases"]


def _run_case(case: dict) -> list:
    html = (FIXTURES / Path(case["fixture"])).read_text(encoding="utf-8")
    url = "https://eprocure.gov.in/cppp/x"
    if case["connector"] == "cppp":
        return CPPPConnector(SourceConfig(id="cppp_central", type="cppp")).parse_listing(html, url).records
    if case["connector"] == "gepnic":
        return GePNICConnector(SourceConfig(id="gep", type="gepnic", base_url=url)).parse_listing(html, url).records
    if case["connector"] == "cppp_awards":
        return CPPPAwardsConnector(AwardsSettings()).parse_page(html, url).records
    raise AssertionError(f"unknown connector in manifest: {case['connector']}")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_crawling_benchmark_case(case: dict):
    if case["outcome"] == "blocked":
        with pytest.raises(BlockedError):
            _run_case(case)
        return

    records = _run_case(case)
    assert len(records) == case["expected_record_count"], (
        f"{case['id']}: expected {case['expected_record_count']} records, got {len(records)}"
    )
    for field in case.get("required_fields", []):
        for rec in records:
            assert getattr(rec, field) not in (None, ""), f"{case['id']}: record missing required field {field!r}"
