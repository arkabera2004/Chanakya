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
"""

from __future__ import annotations

from typing import Any

import httpx

from ...config import ApifySettings, Settings
from ..contract import AcquisitionProvider, AcquisitionRequest, AcquisitionResult, ProviderCapability, ProviderConfigError
from ..extraction import extract_by_items

_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
_DATASET_PAGE_SIZE = 100
_MAX_DATASET_ITEMS = 10_000  # safety cap against unbounded memory growth from a misbehaving Actor


class ApifyProvider(AcquisitionProvider):
    provider_id = "apify"
    capabilities = frozenset({
        ProviderCapability.ACTOR_EXECUTION, ProviderCapability.MANAGED_CRAWL, ProviderCapability.STRUCTURED_EXTRACTION,
    })
    requires_credentials = True

    def __init__(self, settings: ApifySettings, *, secret_resolver=None) -> None:
        super().__init__(settings)
        self._secret_resolver = secret_resolver or Settings().secret

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

    def abort_run(self, run_id: str, *, client: httpx.Client | None = None) -> None:
        token = self._token()
        owns_client = client is None
        client = client or httpx.Client(base_url=self.settings.base_url, timeout=self.settings.timeout_s)
        try:
            client.post(f"/actor-runs/{run_id}/abort", params={"token": token})
        finally:
            if owns_client:
                client.close()

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
            try:
                run = self.start_run(actor_id, request.extra.get("run_input", {"startUrls": [{"url": request.url}]}), client=client)
            except ProviderConfigError:
                raise
            except httpx.HTTPStatusError as exc:
                blocked = exc.response.status_code in (401, 403)
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url,
                    status="blocked" if blocked else "failed",
                    error=f"HTTP {exc.response.status_code}", retryable=not blocked,
                )

            status = run.get("status")
            max_polls = request.extra.get("max_polls", 1)  # default: do not actually poll in fetch()
            polls = 0
            while status not in _TERMINAL_STATUSES and polls < max_polls:
                try:
                    run = self.run_status(run["id"], client=client)
                except httpx.HTTPStatusError as exc:
                    blocked = exc.response.status_code in (401, 403)
                    return AcquisitionResult(
                        provider_id=self.provider_id, url=request.url,
                        status="blocked" if blocked else "failed",
                        error=f"HTTP {exc.response.status_code} while polling run {run['id']}", retryable=not blocked,
                        diagnostics={"run_id": run.get("id")},
                    )
                status = run.get("status")
                polls += 1

            if status != "SUCCEEDED":
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url, status="failed",
                    error=f"apify run ended with status {status}", retryable=status in ("TIMED-OUT",),
                    diagnostics={"run_id": run.get("id"), "status": status},
                )

            dataset_id = run.get("defaultDatasetId")
            try:
                items = self._all_dataset_items(dataset_id, client=client) if dataset_id else []
            except httpx.HTTPStatusError as exc:
                blocked = exc.response.status_code in (401, 403)
                return AcquisitionResult(
                    provider_id=self.provider_id, url=request.url,
                    status="blocked" if blocked else "failed",
                    error=f"HTTP {exc.response.status_code} retrieving dataset {dataset_id}", retryable=not blocked,
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
