"""Generic URL-driven crawler core: SSRF-safe scope, robots.txt, sitemap discovery,
same-origin link discovery, page/depth limits. All HTTP is mocked via httpx.MockTransport —
no real network calls.
"""

from __future__ import annotations

import httpx

from chanakya.webcrawl.crawler import CrawlConfig, crawl_website, discover_sitemap_urls, extract_links

BASE = "https://example.gov.in/"

HOME_HTML = """
<html><body>
<a href="/tenders/1">Tender 1</a>
<a href="/tenders/2">Tender 2</a>
<a href="https://evil.example.com/steal">offsite</a>
<a href="mailto:x@example.com">mail</a>
</body></html>
"""

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.gov.in/tenders/3</loc></url>
</urlset>
"""


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_extract_links_filters_non_navigable_schemes():
    links = extract_links(HOME_HTML, BASE)
    assert "https://example.gov.in/tenders/1" in links
    assert "https://example.gov.in/tenders/2" in links
    assert "https://evil.example.com/steal" in links  # filtering by scope happens in crawl_website, not here
    assert not any(link.startswith("mailto:") for link in links)


def test_discover_sitemap_urls_returns_locs():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP_XML)
        return httpx.Response(404)

    client = _client(handler)
    urls = discover_sitemap_urls(client, BASE)
    assert urls == ["https://example.gov.in/tenders/3"]


def test_discover_sitemap_urls_returns_empty_when_missing():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    assert discover_sitemap_urls(client, BASE) == []


def test_crawl_discovers_same_origin_pages_and_skips_offsite():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text=HOME_HTML, headers={"content-type": "text/html"})
        if path in ("/tenders/1", "/tenders/2"):
            return httpx.Response(200, text="<html><body>tender</body></html>",
                                   headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = _client(handler)
    config = CrawlConfig(max_pages=10, max_depth=2, resolve_dns=False)
    pages = crawl_website(BASE, config, client)

    by_url = {p.url: p for p in pages}
    assert by_url[BASE].status == "fetched"
    assert by_url["https://example.gov.in/tenders/1"].status == "fetched"
    assert by_url["https://example.gov.in/tenders/2"].status == "fetched"
    assert by_url["https://evil.example.com/steal"].status == "skipped_offsite"


def test_crawl_includes_sitemap_seeded_urls():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP_XML)
        if path == "/":
            return httpx.Response(200, text="<html><body>home</body></html>",
                                   headers={"content-type": "text/html"})
        if path == "/tenders/3":
            return httpx.Response(200, text="<html><body>tender 3</body></html>",
                                   headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = _client(handler)
    config = CrawlConfig(max_pages=10, max_depth=2, resolve_dns=False)
    pages = crawl_website(BASE, config, client)
    by_url = {p.url: p for p in pages}
    assert by_url["https://example.gov.in/tenders/3"].status == "fetched"


def test_crawl_respects_robots_disallow():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /tenders/1\n")
        if path == "/sitemap.xml":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text=HOME_HTML, headers={"content-type": "text/html"})
        if path == "/tenders/2":
            return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = _client(handler)
    config = CrawlConfig(max_pages=10, max_depth=2, resolve_dns=False)
    pages = crawl_website(BASE, config, client)
    by_url = {p.url: p for p in pages}
    assert by_url["https://example.gov.in/tenders/1"].status == "skipped_robots"
    assert by_url["https://example.gov.in/tenders/2"].status == "fetched"


def test_crawl_respects_max_pages_limit():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text=HOME_HTML, headers={"content-type": "text/html"})
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    client = _client(handler)
    config = CrawlConfig(max_pages=1, max_depth=2, resolve_dns=False)
    pages = crawl_website(BASE, config, client)
    assert len(pages) == 1


def test_crawl_respects_max_depth():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text='<a href="/a">a</a>', headers={"content-type": "text/html"})
        if path == "/a":
            return httpx.Response(200, text='<a href="/b">b</a>', headers={"content-type": "text/html"})
        if path == "/b":
            return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = _client(handler)
    config = CrawlConfig(max_pages=10, max_depth=1, resolve_dns=False)
    pages = crawl_website(BASE, config, client)
    urls = {p.url for p in pages}
    assert "https://example.gov.in/a" in urls
    assert "https://example.gov.in/b" not in urls  # depth 2, beyond max_depth=1


def test_crawl_stops_early_when_should_stop_returns_true():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text='<a href="/a">a</a><a href="/b">b</a>',
                                   headers={"content-type": "text/html"})
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # allow the first page (home), stop before the rest

    client = _client(handler)
    config = CrawlConfig(max_pages=10, max_depth=2, resolve_dns=False)
    pages = crawl_website(BASE, config, client, should_stop=should_stop)
    assert len(pages) == 1


def test_crawl_collects_document_links_from_fetched_pages():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text=(
                '<a href="/notice.pdf">Notice PDF</a>'
                '<a href="/form.docx">Form</a>'
                '<a href="https://evil.example.com/malware.pdf">offsite doc</a>'
            ), headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = _client(handler)
    config = CrawlConfig(max_pages=10, max_depth=1, resolve_dns=False)
    pages = crawl_website(BASE, config, client)
    home = next(p for p in pages if p.url == BASE)
    assert home.document_links == ["https://example.gov.in/notice.pdf", "https://example.gov.in/form.docx"]


def test_crawl_captures_plain_text_of_fetched_html_pages():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text="<html><body><h1>Tender Notice</h1><p>Deadline: 2026-12-01</p></body></html>",
                                   headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = _client(handler)
    config = CrawlConfig(max_pages=5, max_depth=1, resolve_dns=False)
    pages = crawl_website(BASE, config, client)
    home = next(p for p in pages if p.url == BASE)
    assert "Tender Notice" in home.text
    assert "Deadline: 2026-12-01" in home.text


def test_crawl_captures_page_title():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text="<html><head><title>Acme Robotics</title></head><body>Hi</body></html>",
                                   headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = _client(handler)
    config = CrawlConfig(max_pages=5, max_depth=1, resolve_dns=False)
    pages = crawl_website(BASE, config, client)
    home = next(p for p in pages if p.url == BASE)
    assert home.title == "Acme Robotics"
