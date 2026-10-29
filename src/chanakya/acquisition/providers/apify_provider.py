"""Apify adapter: Actor execution and dataset retrieval via the documented v2 API.

Auth was live-verified during this phase against the free, no-compute-cost
``GET /v2/users/me`` endpoint. Actor execution itself was NOT live-verified: running any
real Actor consumes the account's compute credits, and picking which Actor is appropriate
for procurement acquisition is a product decision (see
docs/CHANAKYA_UNIFIED_CRAWLING_FEATURE_INVENTORY.md), not something to do unprompted.
Execution is therefore TESTED only against mocked responses matching Apify's documented
schema, not against a live run.

Endpoints used (api.apify.com/v2):
  POST /acts/{actor_id}/runs            start a run
  GET  /actor-runs/{run_id}             poll status
  POST /actor-runs/{run_id}/abort       cancel
  GET  /datasets/{dataset_id}/items     paginated results (offset/limit)

Redirect handling / SSRF: unlike Crawlee, Scrapy, Playwright and Crawl4AI, this provider
never makes the actual page request itself — it only tells Apify's API which Actor to run
and what input to give it (typically a ``startUrls`` list); the Actor fetches pages (and
follows any redirects) entirely on Apify's own infrastructure. There is therefore no
"final URL after a redirect" for Chanakya to observe or validate at the AcquisitionResult
level the way the other providers' adapters now do — AcquisitionResult.url always reports
the originally-requested URL, not a per-item or per-redirect destination (individual
dataset items may carry their own URLs; those are not validated either). ``validate_url``
is intentionally NOT called here, consistent with the other managed-API providers
(Firecrawl, Browserless, Zyte): the risk model is different — Chanakya's own network is
never the one making the outbound request, so this is not an SSRF-against-Chanakya
exposure in the way a local-fetch provider's unvalidated redirect would be. What IS
unaddressed: nothing stops a job creator from directing Apify's infrastructure at an
arbitrary external target via ``run_input``; that is a third-party-service-abuse concern,
not covered by this change.

Job-manager retry and long-running Actors: job_manager.py's outer retry loop
(``job_manager._run``) builds one ``AcquisitionRequest`` before its retry loop and calls
``fetch()`` again on that SAME request object after a retryable failure — so ``fetch()``
stores the started run's id onto ``request.extra["apify_run_id"]`` after ``start_run()``.
A still-RUNNING Actor when the local ``max_polls`` budget is exhausted is (correctly)
classified ``retryable=True``; the next ``fetch()`` call on the same request sees
``apify_run_id`` already set and resumes polling that run via ``run_status()`` instead of
calling ``start_run()`` again — avoiding the brand-new (separately billable) Actor run a
naive retry would otherwise start. This only helps across retries within one job_manager
``_run()`` call (the same request object); ``job_manager.retry()`` creates an entirely new
``AcquisitionJob`` row with a fresh ``extra`` dict and therefore still starts a fresh run —
that path is a deliberate "give up and restart" operation, not an in-flight retry.
Confirmed by test (``test_retrying_a_still_running_actor_resumes_the_same_run_instead_of_starting_a_new_one``).

Cancellation: ``job_manager.cancel()`` sets a local DB flag that the job loop checks before
the first attempt and after ``provider.fetch()`` returns (never during a call — there is no
mechanism to interrupt a provider mid-call). When cancellation is detected after ``fetch()``
returns, job_manager calls ``provider.cancel_remote_run(request)``; this provider's override
calls ``abort_run()`` on the run id stored in ``request.extra["apify_run_id"]``, if one
exists. That call only reports whether Apify's API *accepted* the stop request (``True``),
not that the Actor has actually stopped — Apify's run status transitions through ABORTING
before reaching the terminal ABORTED state, and this codebase does not poll to confirm that
transition. Cancellation requested before any run has started, or after the run already
reached a terminal state, is a no-op (``cancel_remote_run`` returns ``None`` when no run id
is on the request). Do not assume a ``True`` result means billing has stopped.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ...config import ApifySettings, Settings
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability, ProviderConfigError
from ..extraction import extract_by_items

_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
_DATASET_PAGE_SIZE = 100
_MAX_DATASET_ITEMS = 10_000  # safety cap against unbounded memory growth from a misbehaving Actor

# HTTP status classification, consistent across start_run/run_status/dataset_items:
#   401/403 -> blocked (auth/authorization; a policy boundary, never retried)
#   408/429/5xx -> failed, retryable (transient: request timeout, rate limit, upstream fault)
#   everything else (400, 404, 409, or any other code) -> failed, not retryable. A 400/404/409
#   means THIS request is wrong (bad input, missing actor/run/dataset, conflicting state) —
#   repeating it unchanged would just fail the same way again. An unrecognised code defaults
#   to not-retryable too: retrying is never assumed safe for a status this code doesn't know.
_TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class ApifyProvider(AcquisitionProvider):
    provider_id = "apify"
    capabilities = frozenset({
        ProviderCapability.ACTOR_EXECUTION, ProviderCapability.MANAGED_CRAWL, ProviderCapability.STRUCTURED_EXTRACTION,
    })
    requires_credentials = True

    def __init__(self, settings: ApifySettings, *, secret_resolver=None, sleep=time.sleep) -> None:
        super().__init__(settings)
        self._secret_resolver = secret_resolver or Settings().secret
        self._sleep = sleep

    def _token(self) -> str | None:
        return self._secret_resolver(self.settings.api_key_env)

    def is_configured(self) -> bool:
        return self.settings.enabled and bool(self._token())

    def validate_config(self) -> list[str]:
        problems = []
        if not self.settings.enabled:
            problems.append("apify is disabled (acquisition_providers.apify.enabled = false)")
        if not self._token():
            problems.append(f"apify: {self.settings.api_key_env} is not set")
        return problems

    def start_run(self, actor_id: str, run_input: dict[str, Any], *, client: httpx.Client | None = None) -> dict[str, Any]:
        """Start an Actor run. Returns the raw ``data`` object (run id, status, dataset id)."""
        token = self._token()
        if not self.settings.enabled or not token:
            raise ProviderConfigError("apify is disabled or missing an API token")
        owns_client = client is None
        client = client or httpx.Client(base_url=self.settings.base_url, timeout=self.settings.timeout_s)
        try:
            resp = client.post(f"/acts/{actor_id}/runs", params={"token": token}, json=run_input)
            resp.raise_for_status()
            return resp.json()["data"]
        finally:
            if owns_client:
                client.close()

    def run_status(self, run_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
        token = self._token()
        owns_client = client is None
        client = client or httpx.Client(base_url=self.settings.base_url, timeout=self.settings.timeout_s)
        try:
            resp = client.get(f"/actor-runs/{run_id}", params={"token": token})
            resp.raise_for_status()
            return resp.json()["data"]
        finally:
            if owns_client:
                client.close()

    def abort_run(self, run_id: str, *, client: httpx.Client | None = None) -> bool:
        """Returns True only if Apify's API returned 2xx for the abort request — this is NOT
        a guarantee the Actor has actually stopped (Apify's own run status transitions
        through ABORTING before reaching the terminal ABORTED state), only that the request
        to stop it was accepted. False on any error response; the caller should not assume
        the remote run is unaffected, only that this call could not confirm it stopped."""
        token = self._token()
        owns_client = client is None
        client = client or httpx.Client(base_url=self.settings.base_url, timeout=self.settings.timeout_s)
        try:
            resp = client.post(f"/actor-runs/{run_id}/abort", params={"token": token})
            return resp.is_success
        finally:
            if owns_client:
                client.close()

    def _result_for_http_error(
        self, exc: httpx.HTTPStatusError, request: AcquisitionRequest, *, context: str, diagnostics: dict | None = None
    ) -> AcquisitionResult:
        code = exc.response.status_code
        if code in (401, 403):
            status, retryable = "blocked", False
        elif code in _TRANSIENT_HTTP_STATUS_CODES:
            status, retryable = "failed", True
        else:
            status, retryable = "failed", False
        diag = dict(diagnostics or {})
        if code == 429:
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    diag["retry_after_s"] = float(retry_after)
                except ValueError:
                    pass
        return AcquisitionResult(
            provider_id=self.provider_id, url=request.url, status=status,
            error=f"HTTP {code} {context}", retryable=retryable, diagnostics=diag,
        )

    def cancel_remote_run(self, request: AcquisitionRequest, *, client: httpx.Client | None = None) -> bool | None:
        """Request Apify to stop the run started for this request, if one was. ``True``
        means Apify's API accepted the abort request, not that the Actor has stopped yet —
        see ``abort_run``'s own docstring."""
        run_id = request.extra.get("apify_run_id")
        if not run_id:
            return None
        return self.abort_run(run_id, client=client)

    def dataset_items(
        self, dataset_id: str, *, offset: int = 0, limit: int = 100, client: httpx.Client | None = None
    ) -> list[dict[str, Any]]:
        token = self._token()
        owns_client = client is None
        client = client or httpx.Client(base_url=self.settings.base_url, timeout=self.settings.timeout_s)
        try:
            resp = client.get(
                f"/datasets/{dataset_id}/items",
                params={"token": token, "format": "json", "offset": offset, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns_client:
                client.close()

    def _all_dataset_items(self, dataset_id: str, *, client: httpx.Client) -> list[dict[str, Any]]:
        """Paginated dataset_items(): Apify returns at most one page per call, so a single
        call silently truncates any dataset larger than that page. Keeps requesting pages
        until one comes back shorter than requested (end of dataset) or the safety cap is
        reached — never unbounded."""
        items: list[dict[str, Any]] = []
        offset = 0
        while offset < _MAX_DATASET_ITEMS:
            page = self.dataset_items(dataset_id, offset=offset, limit=_DATASET_PAGE_SIZE, client=client)
            items.extend(page)
            if len(page) < _DATASET_PAGE_SIZE:
                break
            offset += _DATASET_PAGE_SIZE
        return items

    def fetch(self, request: AcquisitionRequest, *, client: httpx.Client | None = None) -> AcquisitionResult:
        """Poll-to-completion wrapper: starts a run (actor id + input in ``request.extra``),
        polls until terminal, then returns the dataset items as diagnostics. The job manager
        is expected to drive the async start/poll/cancel methods directly for long runs rather
        than call this blocking convenience method on a real, slow Actor."""
        actor_id = request.extra.get("actor_id")
        if not actor_id:
            return AcquisitionResult(
                provider_id=self.provider_id, url=request.url, status="failed",
                error="apify: request.extra['actor_id'] is required", retryable=False,
            )
        owns_client = client is None
        client = client or httpx.Client(base_url=self.settings.base_url, timeout=self.settings.timeout_s)
        try:
            resume_run_id = request.extra.get("apify_run_id")
            try:
                if resume_run_id:
                    # job_manager's outer retry calls fetch() again on the SAME request after a
                    # retryable (still-running) failure — resume polling the run already started
                    # instead of calling start_run() again, which would bill a second Actor run.
                    run = self.run_status(resume_run_id, client=client)
                else:
                    run = self.start_run(actor_id, request.extra.get("run_input", {"startUrls": [{"url": request.url}]}), client=client)
                request.extra["apify_run_id"] = run["id"]
            except ProviderConfigError:
                raise
            except httpx.HTTPStatusError as exc:
                return self._result_for_http_error(exc, request, context="starting/resuming the run")
            except httpx.TransportError as exc:
                # A network/connect/read timeout while STARTING a run is genuinely ambiguous:
                # Apify may have received and processed the request even though the response
                # never arrived, and there is no run id yet to resume. Retrying blindly risks
                # starting a second, separately billable Actor run for an already-ambiguous
                # first attempt — so, unlike the same error during polling or dataset
                # retrieval (both idempotent reads against an already-known run), this stays
                # non-retryable. A resumed poll (resume_run_id truthy) carries no such
                # ambiguity about duplicate creation, but is treated the same way here for
                # simplicity since a timeout mid-resume gives no new information either.
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"{type(exc).__name__} starting/resuming the run", retryable=False,
                )
            except (KeyError, ValueError) as exc:
                # The response didn't match Apify's documented schema (missing "data"/"id",
                # or not valid JSON) — a permanent problem with the response, not a transient
                # fault. Retrying the identical request would not produce a different schema.
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"malformed response starting/resuming the run: {type(exc).__name__}", retryable=False,
                )

            status = run.get("status")
            max_polls = request.extra.get("max_polls", 1)  # default: do not actually poll in fetch()
            poll_interval_s = request.extra.get("poll_interval_s", self.settings.poll_interval_s)
            polls = 0
            while status not in _TERMINAL_STATUSES and polls < max_polls:
                self._sleep(poll_interval_s)
                try:
                    run = self.run_status(run["id"], client=client)
                except httpx.HTTPStatusError as exc:
                    return self._result_for_http_error(
                        exc, request, context=f"while polling run {run['id']}", diagnostics={"run_id": run.get("id")},
                    )
                except httpx.TransportError as exc:
                    # Unlike a start_run timeout, this is an idempotent read against an
                    # already-known run — safe to retry, and the run id is preserved so a
                    # retry resumes polling the same run instead of starting a new one.
                    return AcquisitionResult(
                        provider_id=self.provider_id, url=request.url, status="failed",
                        error=f"{type(exc).__name__} while polling run {run['id']}", retryable=True,
                        diagnostics={"run_id": run.get("id")},
                    )
                status = run.get("status")
                polls += 1

            if status != "SUCCEEDED":
                # A real terminal failure (FAILED/ABORTED) is permanent — retrying would just
                # fail again. TIMED-OUT is Apify's own transient classification. A status
                # that's still not in _TERMINAL_STATUSES means the Actor is genuinely still
                # running and this call's local poll budget simply ran out — that is NOT a
                # failure of the Actor, and must stay retryable so the job manager's own
                # outer retry loop gives it another attempt later, not give up permanently.
                still_running = status not in _TERMINAL_STATUSES
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"apify run ended with status {status}",
                    retryable=still_running or status in ("TIMED-OUT",),
                    diagnostics={"run_id": run.get("id"), "status": status},
                )

            dataset_id = run.get("defaultDatasetId")
            try:
                items = self._all_dataset_items(dataset_id, client=client) if dataset_id else []
            except httpx.HTTPStatusError as exc:
                return self._result_for_http_error(
                    exc, request, context=f"retrieving dataset {dataset_id}",
                    diagnostics={"run_id": run.get("id"), "status": status, "dataset_id": dataset_id},
                )
            except httpx.TransportError as exc:
                # The Actor already reached a terminal state; only dataset retrieval failed.
                # Retrying is a safe, idempotent read of the same dataset_id/run_id.
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"{type(exc).__name__} retrieving dataset {dataset_id}", retryable=True,
                    diagnostics={"run_id": run.get("id"), "status": status, "dataset_id": dataset_id},
                )
            records: list = []
            diagnostics: dict = {"run_id": run.get("id"), "status": status, "dataset_id": dataset_id,
                                 "item_count": len(items), "items": items}
            item_fields: dict[str, str] = request.extra.get("item_fields", {})
            if item_fields and items:
                records, review = extract_by_items(
                    items, item_fields, source_id=request.source_id,
                    platform=request.extra.get("platform", "apify"), provider_id=self.provider_id,
                )
                if review:
                    diagnostics["needs_review"] = review

            return AcquisitionResult(
                provider_id=self.provider_id, url=request.url, status="ok",
                records=records, diagnostics=diagnostics,
            )
        finally:
            if owns_client:
                client.close()
