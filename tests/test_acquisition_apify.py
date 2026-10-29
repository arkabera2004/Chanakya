"""Apify adapter: mocked API tests.

Live verification note: auth was confirmed during development against the free,
no-compute-cost GET /v2/users/me endpoint. Actor execution is tested only against mocked
responses matching Apify's documented schema — running a real Actor costs compute credits
and picking an appropriate one is a product decision, not made here.
"""

from __future__ import annotations

import httpx

from chanakya.acquisition import AcquisitionRequest
from chanakya.acquisition.providers.apify_provider import ApifyProvider
from chanakya.config import ApifySettings


def _provider(enabled: bool = True, token: str | None = "apify-test-token") -> ApifyProvider:
    return ApifyProvider(ApifySettings(enabled=enabled), secret_resolver=lambda _env: token)


def test_disabled_provider_is_not_configured():
    assert _provider(enabled=False).is_configured() is False


def test_missing_token_is_not_configured():
    p = _provider(token=None)
    assert p.is_configured() is False
    assert "APIFY_API_TOKEN" in p.validate_config()[0]


def test_fetch_requires_actor_id():
    res = _provider().fetch(AcquisitionRequest(url="https://x/", source_id="test"))
    assert res.status == "failed" and "actor_id" in res.error


def test_dataset_items_beyond_the_first_page_are_not_silently_dropped():
    """fetch() calls dataset_items() exactly once with the default offset=0, limit=100 — a
    dataset with more than 100 items currently has the rest silently truncated with no
    error or indication anything was lost."""
    all_items = [{"title": f"Tender {i}"} for i in range(150)]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/datasets/ds1/items":
            offset = int(req.url.params.get("offset", "0"))
            limit = int(req.url.params.get("limit", "100"))
            return httpx.Response(200, json=all_items[offset:offset + limit])
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor"})
    res = _provider().fetch(req, client=client)
    assert res.status == "ok"
    assert res.diagnostics["item_count"] == 150, f"expected all 150 items, got {res.diagnostics['item_count']}"


def test_successful_run_returns_dataset_items():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/datasets/ds1/items":
            return httpx.Response(200, json=[{"title": "Tender A"}, {"title": "Tender B"}])
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor"})
    res = _provider().fetch(req, client=client)
    assert res.status == "ok"
    assert res.diagnostics["item_count"] == 2
    assert res.diagnostics["items"][0]["title"] == "Tender A"


def test_dataset_items_are_converted_to_tender_records_when_item_fields_given():
    """AcquisitionResult.records is never populated by fetch() — dataset items only ever
    land in diagnostics. Apify is the only one of the 9 providers that produces no canonical
    TenderRecords, even on a fully successful run with a field mapping supplied."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/datasets/ds1/items":
            return httpx.Response(200, json=[{"pageTitle": "Tender A", "tenderRef": "T-1"}])
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(
        url="https://x/", source_id="test",
        extra={"actor_id": "my-actor", "item_fields": {"title": "pageTitle", "ref_no": "tenderRef"}},
    )
    res = _provider().fetch(req, client=client)
    assert res.status == "ok"
    assert len(res.records) == 1, "dataset items with a field mapping must produce real TenderRecords"
    assert res.records[0].title == "Tender A" and res.records[0].ref_no == "T-1"


def test_a_still_running_actor_when_the_poll_budget_is_exhausted_is_retryable():
    """A run that is still genuinely RUNNING — not FAILED, ABORTED, or TIMED-OUT — just
    hasn't finished within this call's local max_polls budget. That is categorically
    different from a real terminal failure and must stay retryable, so the job manager's
    own outer retry loop gets another attempt (and more wall-clock time) later. This matters
    most on the real job-manager path, which creates jobs without an explicit max_polls
    override (fetch()'s own default is 1) — so every real Apify Actor that takes any time to
    run currently hits exactly this path."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/actor-runs/run1":
            return httpx.Response(200, json={"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1"}})
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor"})  # default max_polls=1
    res = _provider().fetch(req, client=client)
    assert res.status == "failed"
    assert res.retryable is True, "a still-RUNNING Actor must be retryable, not a permanent failure"


def test_run_polls_until_terminal_status():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/actor-runs/run1":
            calls["n"] += 1
            status = "RUNNING" if calls["n"] == 1 else "SUCCEEDED"
            return httpx.Response(200, json={"data": {"id": "run1", "status": status, "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/datasets/ds1/items":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor", "max_polls": 3})
    res = _provider().fetch(req, client=client)
    assert res.status == "ok" and calls["n"] == 2


def test_polling_waits_between_attempts_instead_of_hammering_the_api():
    """The polling loop currently has no delay between iterations at all — a caller who sets
    max_polls high enough to actually wait for a real Actor (the only safe way to use this
    provider today, per the module docstring) would hammer GET /actor-runs/{id} back-to-back
    as fast as the network allows, for every poll. Injects a sleep spy (same DI pattern
    job_manager itself already uses) to prove a delay is requested between polls."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/actor-runs/run1":
            return httpx.Response(200, json={"data": {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/datasets/ds1/items":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected path {req.url.path}")

    sleeps: list[float] = []
    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    provider = ApifyProvider(ApifySettings(enabled=True), secret_resolver=lambda _env: "tok", sleep=sleeps.append)
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor", "max_polls": 3})
    res = provider.fetch(req, client=client)
    assert res.status == "ok"
    assert sleeps, "expected at least one sleep between the start-run status check and the first poll"
    assert all(s > 0 for s in sleeps)


def test_failed_run_is_classified_failed():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"data": {"id": "run1", "status": "FAILED", "defaultDatasetId": None}})

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor"})
    res = _provider().fetch(req, client=client)
    assert res.status == "failed" and res.diagnostics["status"] == "FAILED"
    assert res.retryable is False, "a real terminal Actor failure must stay non-retryable"


def test_rate_limit_during_dataset_retrieval_is_classified_failed_and_retryable():
    """Same gap as run_status() polling, for the final dataset_items() call: a SUCCEEDED
    run whose dataset retrieval hits a 429 currently raises unhandled instead of being
    reported as a transient, retryable failure."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/datasets/ds1/items":
            return httpx.Response(429, json={"error": {"type": "rate-limit-exceeded"}})
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor"})
    res = _provider().fetch(req, client=client)
    assert res.status == "failed" and res.retryable is True


def test_rate_limit_during_polling_is_classified_failed_and_retryable():
    """start_run() already classifies a 429 correctly (status != 401/403 -> failed,
    retryable=True) — but run_status(), called in the polling loop, is not wrapped in any
    error handling at all. A 429 (or any HTTP error) while polling currently propagates as
    an unhandled httpx.HTTPStatusError out of fetch(), which the real job-manager path
    catches generically as a provider bug (retryable=False) — losing the fact that a rate
    limit is genuinely transient and should be retried."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v2/acts/my-actor/runs":
            return httpx.Response(201, json={"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1"}})
        if req.url.path == "/v2/actor-runs/run1":
            return httpx.Response(429, json={"error": {"type": "rate-limit-exceeded"}})
        raise AssertionError(f"unexpected path {req.url.path}")

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor", "max_polls": 3})
    res = _provider().fetch(req, client=client)
    assert res.status == "failed" and res.retryable is True


def test_invalid_token_is_classified_blocked():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "token-not-provided"}})

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(handler))
    req = AcquisitionRequest(url="https://x/", source_id="test", extra={"actor_id": "my-actor"})
    res = _provider().fetch(req, client=client)
    assert res.status == "blocked" and res.retryable is False


def test_abort_run_reports_whether_the_remote_run_actually_acknowledged_it():
    """abort_run() currently has no return value at all and silently swallows every HTTP
    status, including errors — a caller has no way to know whether a cancellation actually
    reached the remote run or not. Not wired into job_manager yet (documented separately as
    a known gap), but the method itself should at minimum report success/failure honestly so
    a future caller isn't blind to it."""
    def ok_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": "run1", "status": "ABORTING"}})

    def error_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"type": "record-not-found"}})

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(ok_handler))
    assert _provider().abort_run("run1", client=client) is True

    client = httpx.Client(base_url="https://api.apify.com/v2", transport=httpx.MockTransport(error_handler))
    assert _provider().abort_run("run1", client=client) is False
