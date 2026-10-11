"""Polite, defensive HTTP client used by every connector.

- allowlisted hosts only, re-validated on every redirect (SSRF protection)
- robots.txt honoured (a disallow marks the source as restricted, never bypassed)
- per-domain minimum interval between requests
- bounded retries with exponential backoff + jitter; honours Retry-After
- streamed downloads with a hard size cap and content-type sniffing
"""

from __future__ import annotations

import logging
import random
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .config import HttpSettings
from .security import SecurityError, validate_url

log = logging.getLogger(__name__)


class FetchError(Exception):
    """Network/HTTP failure after retries."""


class BlockedError(FetchError):
    """Access is restricted (robots.txt disallow, CAPTCHA, login, 401/403).
    Chanakya must not work around it."""


class RateLimitedError(FetchError):
    pass


@dataclass
class FetchStats:
    requests: int = 0
    retries: int = 0
    bytes: int = 0
    per_host: dict[str, int] = field(default_factory=dict)


class PoliteClient:
    def __init__(
        self,
        settings: HttpSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        resolve_dns: bool = True,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._resolve_dns = resolve_dns
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.stats = FetchStats()
        self._client = httpx.Client(
            timeout=httpx.Timeout(settings.timeout_s),
            follow_redirects=False,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
            },
            transport=transport,
        )

    # ------------------------------------------------------------ lifecycle
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    # ------------------------------------------------------------ helpers
    def _validate(self, url: str) -> str:
        return validate_url(url, self.settings.allowed_domains, resolve_dns=self._resolve_dns)

    def _throttle(self, host: str) -> None:
        with self._lock:
            last = self._last_request.get(host)
            now = time.monotonic()
            if last is not None:
                wait = self.settings.min_interval_s - (now - last)
                if wait > 0:
                    self._sleep(wait)
            self._last_request[host] = time.monotonic()

    def robots_allows(self, url: str) -> bool:
        if not self.settings.respect_robots_txt:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                resp = self._raw_get(urljoin(origin, "/robots.txt"), check_robots=False, retries=1)
                if resp.status_code == 200 and "text" in resp.headers.get("content-type", "text/plain"):
                    parser.parse(resp.text.splitlines())
                elif resp.status_code in (401, 403):
                    parser.disallow_all = True
                else:
                    parser.allow_all = True  # 404 etc.: no robots policy published
            except FetchError:
                parser = None  # unknown; decide below
            self._robots[origin] = parser
        parser = self._robots[origin]
        if parser is None:
            return True  # robots unreachable: proceed cautiously; failures surface on the real request
        return parser.can_fetch(self.settings.user_agent, url)

    def _raw_get(self, url: str, *, check_robots: bool = True, retries: int | None = None,
                 headers: dict[str, str] | None = None) -> httpx.Response:
        url = self._validate(url)
        if check_robots and not self.robots_allows(url):
            raise BlockedError(f"robots.txt disallows {url}")
        attempts = (self.settings.max_retries if retries is None else retries) + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            current = url
            try:
                for _hop in range(self.settings.max_redirects + 1):
                    host = urlsplit(current).hostname or ""
                    self._throttle(host)
                    self.stats.requests += 1
                    self.stats.per_host[host] = self.stats.per_host.get(host, 0) + 1
                    resp = self._client.get(current, headers=headers)
                    if resp.is_redirect:
                        nxt = urljoin(current, resp.headers.get("location", ""))
                        current = self._validate(nxt)
                        continue
                    break
                else:
                    raise FetchError(f"too many redirects for {url}")
            except SecurityError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                self._backoff(attempt, None)
                self.stats.retries += 1
                continue
            if resp.status_code in (401, 403):
                raise BlockedError(f"HTTP {resp.status_code} for {current}")
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = RateLimitedError(f"HTTP {resp.status_code}") if resp.status_code == 429 else FetchError(f"HTTP {resp.status_code}")
                if attempt < attempts - 1:
                    self._backoff(attempt, resp.headers.get("retry-after"))
                    self.stats.retries += 1
                    continue
                raise last_exc
            self.stats.bytes += len(resp.content)
            return resp
        raise FetchError(f"failed to fetch {url}: {last_exc}")

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                self._sleep(min(float(retry_after), 300.0))
                return
            except ValueError:
                pass
        delay = self.settings.backoff_base_s * (2 ** attempt) + random.uniform(0, 1)
        self._sleep(min(delay, 120.0))

    # ------------------------------------------------------------ public API
    def get_text(self, url: str) -> tuple[str, str]:
        """Return (final_url, text). Raises BlockedError/FetchError."""
        resp = self._raw_get(url)
        if resp.status_code == 404:
            raise FetchError(f"HTTP 404 for {url}")
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code} for {url}")
        return str(resp.url), resp.text

    def download(self, url: str, dest_dir: Path, filename: str) -> tuple[Path, str | None]:
        """Stream a document to disk with a hard size cap. Returns (path, content_type)."""
        from .security import safe_filename

        url = self._validate(url)
        if not self.robots_allows(url):
            raise BlockedError(f"robots.txt disallows {url}")
        limit = self.settings.max_download_mb * 1024 * 1024
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / safe_filename(filename)
        host = urlsplit(url).hostname or ""
        self._throttle(host)
        self.stats.requests += 1
        with self._client.stream("GET", url) as resp:
            if resp.status_code in (401, 403):
                raise BlockedError(f"HTTP {resp.status_code} for {url}")
            if resp.is_redirect:
                raise FetchError("redirects are not followed for downloads; resolve URL first")
            if resp.status_code >= 400:
                raise FetchError(f"HTTP {resp.status_code} for {url}")
            declared = int(resp.headers.get("content-length") or 0)
            if declared > limit:
                raise FetchError(f"document too large ({declared} bytes)")
            ctype = resp.headers.get("content-type")
            size = 0
            with open(target, "wb") as fh:
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        fh.close()
                        target.unlink(missing_ok=True)
                        raise FetchError("document exceeded size limit while downloading")
                    fh.write(chunk)
        self.stats.bytes += size
        return target, ctype
