"""Core crawl loop: robots.txt, sitemap discovery, same-origin link discovery, page/depth
limits. SSRF protection is delegated to ``chanakya.security.validate_url`` — this module
does not implement its own allowlisting or DNS checks.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from ..security import SecurityError, validate_url

USER_AGENT = "ChanakyaWebsiteIntelligenceBot/1.0 (+https://wayam.ai)"
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_SKIPPED_HREF_PREFIXES = ("mailto:", "tel:", "javascript:", "#")
DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx")
MAX_PAGE_TEXT_CHARS = 50_000


@dataclass
class CrawlConfig:
    max_pages: int = 50
    max_depth: int = 2
    extra_allowed_domains: list[str] = field(default_factory=list)
    resolve_dns: bool = True


@dataclass
class DiscoveredPage:
    url: str
    depth: int
    status: str  # fetched | failed | skipped_robots | skipped_offsite
    http_status: int | None = None
    content_type: str | None = None
    error: str | None = None
    document_links: list[str] = field(default_factory=list)
    text: str | None = None


def _allowed_domains(base_url: str, config: CrawlConfig) -> list[str]:
    host = urlsplit(base_url).hostname or ""
    return [host, *config.extra_allowed_domains]


def _in_scope(url: str, allowed: list[str], config: CrawlConfig) -> bool:
    try:
        validate_url(url, allowed, allow_http=True, resolve_dns=config.resolve_dns)
        return True
    except SecurityError:
        return False


def _get(client: httpx.Client, url: str) -> httpx.Response | None:
    try:
        return client.get(url, headers={"User-Agent": USER_AGENT}, timeout=15.0)
    except httpx.HTTPError:
        return None


def _robots_allows(client: httpx.Client, base_url: str, path: str) -> bool:
    parts = urlsplit(base_url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    resp = _get(client, robots_url)
    parser = RobotFileParser()
    parser.parse(resp.text.splitlines() if resp is not None and resp.status_code == 200 else [])
    return parser.can_fetch(USER_AGENT, path)


def discover_sitemap_urls(client: httpx.Client, base_url: str) -> list[str]:
    """Fetch ``/sitemap.xml`` at the site root and return its listed URLs. Follows one level
    of sitemap-index nesting. Returns an empty list if there is no sitemap or it fails to parse
    — a crawl always has the homepage as a fallback seed."""
    parts = urlsplit(base_url)
    resp = _get(client, f"{parts.scheme}://{parts.netloc}/sitemap.xml")
    if resp is None or resp.status_code != 200:
        return []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return []

    def locs(elem: ET.Element) -> list[str]:
        found = [el.text.strip() for el in elem.findall(".//sm:loc", _SITEMAP_NS) if el.text]
        return found or [el.text.strip() for el in elem.findall(".//loc") if el.text]

    top_locs = locs(root)
    if root.tag.endswith("sitemapindex"):
        urls: list[str] = []
        for loc in top_locs:
            sub = _get(client, loc)
            if sub is not None and sub.status_code == 200:
                try:
                    urls.extend(locs(ET.fromstring(sub.text)))
                except ET.ParseError:
                    continue
        return urls
    return top_locs


def page_text(html: str) -> str:
    """Plain text of an HTML page, for tender-field extraction — ``extract_document`` (which
    already strips script/style/nav/header/footer for standalone documents) is not reused here
    since a full page's chrome differs from a document's, but the stripped-tag set matches it."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)[:MAX_PAGE_TEXT_CHARS]


def extract_links(html: str, page_url: str) -> list[str]:
    """Every navigable ``<a href>`` on the page, resolved to absolute URLs. Scope filtering
    (same-origin, SSRF) is the caller's job — ``crawl_website`` does it via ``validate_url``."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(_SKIPPED_HREF_PREFIXES):
            continue
        links.append(urljoin(page_url, href))
    return links


def crawl_website(
    base_url: str, config: CrawlConfig, client: httpx.Client,
    should_stop: Callable[[], bool] | None = None,
) -> list[DiscoveredPage]:
    """Breadth-first crawl starting at ``base_url`` plus any sitemap-seeded URLs, bounded by
    ``config.max_pages``/``config.max_depth``. Every URL is validated against the site's own
    host (plus ``config.extra_allowed_domains``) before being fetched; off-scope links are
    recorded as ``skipped_offsite`` rather than silently dropped, and robots.txt disallows are
    recorded as ``skipped_robots`` — both visible to the crawl monitor, not hidden.

    ``should_stop`` is checked before dequeuing each URL so a caller can observe a cancel/pause
    flag mid-crawl without this module depending on any job-manager concept — it returns
    whatever pages were collected before the stop."""
    allowed = _allowed_domains(base_url, config)
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(base_url, 0)]

    for seed in discover_sitemap_urls(client, base_url):
        if seed not in seen and seed != base_url:
            queue.append((seed, 1))

    pages: list[DiscoveredPage] = []
    while queue and len(pages) < config.max_pages:
        if should_stop is not None and should_stop():
            break
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)

        try:
            validate_url(url, allowed, allow_http=True, resolve_dns=config.resolve_dns)
        except SecurityError:
            pages.append(DiscoveredPage(url=url, depth=depth, status="skipped_offsite"))
            continue

        path = urlsplit(url).path or "/"
        if not _robots_allows(client, base_url, path):
            pages.append(DiscoveredPage(url=url, depth=depth, status="skipped_robots"))
            continue

        resp = _get(client, url)
        if resp is None:
            pages.append(DiscoveredPage(url=url, depth=depth, status="failed", error="request failed"))
            continue

        content_type = resp.headers.get("content-type", "")
        page = DiscoveredPage(
            url=url, depth=depth, status="fetched" if resp.status_code < 400 else "failed",
            http_status=resp.status_code, content_type=content_type,
        )
        pages.append(page)

        if resp.status_code < 400 and "html" in content_type:
            page.text = page_text(resp.text)
            links = extract_links(resp.text, url)
            page.document_links = [
                link for link in links
                if urlsplit(link).path.lower().endswith(DOCUMENT_EXTENSIONS)
                and _in_scope(link, allowed, config)
            ]
            if depth < config.max_depth:
                for link in links:
                    if link not in seen and not urlsplit(link).path.lower().endswith(DOCUMENT_EXTENSIONS):
                        queue.append((link, depth + 1))

    return pages
