"""Standalone subprocess worker that runs exactly one Scrapy request and writes its outcome
as JSON to the given output path.

Why a subprocess: Scrapy's ``CrawlerProcess`` starts a Twisted reactor, and a Twisted
reactor can only be started once per process — a second ``fetch()`` call in the same
process would raise ``ReactorNotRestartable``. Running each request in a fresh subprocess
sidesteps that and matches the project's own instruction not to block normal FastAPI
requests on a long-running crawler (the parent process stays free; this worker is spawned
and waited on with a timeout).

Usage: ``python -m chanakya.acquisition.providers._scrapy_worker <url> <out_json_path>``
"""

from __future__ import annotations

import json
import sys


def _run(url: str, out_path: str) -> None:
    import scrapy
    from scrapy.crawler import CrawlerProcess

    outcome: dict = {}

    class _OneRequestSpider(scrapy.Spider):
        name = "chanakya_one_request"
        handle_httpstatus_list = list(range(300, 600))

        async def start(self):
            yield scrapy.Request(url, callback=self.parse, errback=self.on_error, dont_filter=True)

        def start_requests(self):  # Scrapy < 2.13 compatibility, per Spider.start()'s own docs
            yield scrapy.Request(url, callback=self.parse, errback=self.on_error, dont_filter=True)

        def parse(self, response):
            outcome["status"] = "ok" if response.status < 400 else ("blocked" if response.status in (401, 403) else "failed")
            outcome["status_code"] = response.status
            outcome["url"] = response.url
            outcome["content_type"] = response.headers.get("Content-Type", b"").decode("utf-8", "replace")
            outcome["body"] = response.text if "html" in outcome["content_type"] or "text" in outcome["content_type"] else None
            if outcome["status"] == "failed":
                outcome["error"] = f"HTTP {response.status}"
                outcome["retryable"] = response.status >= 500
            elif outcome["status"] == "blocked":
                outcome["retryable"] = False

        def on_error(self, failure):
            outcome["status"] = "failed"
            outcome["url"] = url
            outcome["error"] = f"{failure.type.__name__}: {failure.value}"
            outcome["retryable"] = True  # DNS/timeout/connection errors are treated as transient

    # ROBOTSTXT_OBEY stays on (Scrapy's default policy, not overridden): a robots.txt
    # disallow drops the request silently in Scrapy's own middleware, with no parse() or
    # errback() call — handled below as the "no outcome" / blocked case.
    process = CrawlerProcess(settings={"LOG_ENABLED": False, "RETRY_ENABLED": False, "ROBOTSTXT_OBEY": True})
    process.crawl(_OneRequestSpider)
    process.start()

    if not outcome:
        outcome = {
            "status": "blocked", "url": url, "retryable": False,
            "error": "no response handled — likely dropped by robots.txt disallow",
        }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(outcome, fh)


if __name__ == "__main__":
    _run(sys.argv[1], sys.argv[2])
