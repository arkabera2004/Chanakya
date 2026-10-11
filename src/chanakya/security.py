"""Security helpers: URL allowlisting / SSRF protection, safe file handling,
spreadsheet-injection neutralisation, secret redaction and prompt-injection
screening. Source content is always treated as untrusted data.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


class SecurityError(Exception):
    """Raised when untrusted input violates a security policy."""


# ---------------------------------------------------------------- URLs / SSRF

def host_allowed(host: str, allowed_domains: list[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    for d in allowed_domains:
        d = d.lower().lstrip(".")
        if host == d or host.endswith("." + d):
            return True
    return False


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None
            and not _is_public_ip(str(addr.ipv4_mapped)))
    )


def validate_url(
    url: str,
    allowed_domains: list[str],
    *,
    allow_http: bool = False,
    resolve_dns: bool = True,
    resolver=socket.getaddrinfo,
) -> str:
    """Validate a URL before Chanakya fetches it.

    - https only (http optional for explicitly configured local services)
    - host must be on the allowlist
    - no credentials in the URL, no IP literals pointing at private ranges
    - when DNS resolves, every address must be public (SSRF / DNS rebinding guard)
    """
    parts = urlsplit(url)
    if parts.scheme not in ("https",) + (("http",) if allow_http else ()):
        raise SecurityError(f"scheme not allowed: {parts.scheme!r}")
    if parts.username or parts.password:
        raise SecurityError("credentials in URL are not allowed")
    host = parts.hostname or ""
    if not host:
        raise SecurityError("URL has no host")
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        if not _is_public_ip(host):
            raise SecurityError(f"private or reserved address blocked: {host}")
        if not host_allowed(host, allowed_domains):
            raise SecurityError(f"IP host not on allowlist: {host}")
        return url
    if not host_allowed(host, allowed_domains):
        raise SecurityError(f"host not on allowlist: {host}")
    if resolve_dns:
        try:
            infos = resolver(host, parts.port or (443 if parts.scheme == "https" else 80))
        except (socket.gaierror, OSError):
            infos = []  # resolution happens at the proxy; allowlist already enforced
        for info in infos:
            ip = info[4][0]
            if not _is_public_ip(ip):
                raise SecurityError(f"{host} resolves to non-public address {ip}")
    return url


def is_safe_link(url: str | None, allowed_domains: list[str]) -> bool:
    """For links written into reports/emails: http(s) only, allowlisted host."""
    if not url:
        return False
    try:
        validate_url(url, allowed_domains, allow_http=True, resolve_dns=False)
        return True
    except SecurityError:
        return False


# ------------------------------------------------------------- files / zips

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, default: str = "document", max_len: int = 120) -> str:
    """Reduce an untrusted filename to a safe basename (no paths, no control chars)."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        name = default
    stem = name.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        name = "_" + name
    if len(name) > max_len:
        if "." in name:
            stem, ext = name.rsplit(".", 1)
            name = stem[: max_len - len(ext) - 1] + "." + ext[:10]
        else:
            name = name[:max_len]
    return name


@dataclass
class ZipLimits:
    max_members: int = 200
    max_total_bytes: int = 200 * 1024 * 1024
    max_ratio: int = 100
    max_member_bytes: int = 50 * 1024 * 1024


def safe_extract_zip(zip_path: Path, dest: Path, limits: ZipLimits | None = None) -> list[Path]:
    """Extract a ZIP defensively: rejects path traversal, absolute paths, symlinks,
    encrypted members, zip bombs (size/ratio) and excessive member counts. Nested
    archives are extracted as plain files and are not recursed into."""
    limits = limits or ZipLimits()
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > limits.max_members:
            raise SecurityError(f"archive has {len(infos)} members (limit {limits.max_members})")
        total = sum(i.file_size for i in infos)
        if total > limits.max_total_bytes:
            raise SecurityError("archive uncompressed size exceeds limit")
        compressed = max(1, sum(i.compress_size for i in infos))
        if total / compressed > limits.max_ratio:
            raise SecurityError("archive compression ratio suggests a zip bomb")
        used: set[str] = set()
        for info in infos:
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise SecurityError(f"encrypted member not allowed: {info.filename!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise SecurityError(f"symlink member not allowed: {info.filename!r}")
            p = PurePosixPath(info.filename.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts or re.match(r"^[A-Za-z]:", info.filename):
                raise SecurityError(f"unsafe path in archive: {info.filename!r}")
            if info.file_size > limits.max_member_bytes:
                raise SecurityError(f"member too large: {info.filename!r}")
            name = safe_filename(p.name)
            base, n = name, 1
            while name in used:
                stem, dot, ext = base.rpartition(".")
                name = f"{stem or base}_{n}{dot}{ext}" if dot else f"{base}_{n}"
                n += 1
            used.add(name)
            target = (dest / name).resolve()
            if dest not in target.parents:
                raise SecurityError("extraction escaped destination")
            with zf.open(info) as src, open(target, "wb") as fh:
                remaining = limits.max_member_bytes
                while chunk := src.read(65536):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise SecurityError("member exceeded declared size")
                    fh.write(chunk)
            out.append(target)
    return out


# --------------------------------------------------------- spreadsheet cells

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")


def neutralize_cell(value: object) -> object:
    """Prevent spreadsheet formula injection from untrusted text (OWASP CSV
    injection guidance): strings starting with a formula trigger get a leading
    apostrophe so Excel treats them as text. Numbers/dates pass through."""
    if not isinstance(value, str):
        return value
    cleaned = "".join(ch for ch in value if ch in "\n\t" or ch.isprintable())
    if cleaned.startswith(_FORMULA_PREFIXES):
        # Allow plain negative numbers such as "-5" written as text.
        if re.fullmatch(r"-\d+(\.\d+)?", cleaned):
            return cleaned
        return "'" + cleaned
    return cleaned[:32000]  # Excel cell limit is 32,767 chars


# ------------------------------------------------------------- secrets

_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I),
    re.compile(r"((?:api[_-]?key|password|passwd|secret|token)\s*[=:]\s*)\S+", re.I),
]


def redact(text: str, secrets: list[str | None] | None = None) -> str:
    out = text or ""
    for s in secrets or []:
        if s and len(s) >= 4:
            out = out.replace(s, "***REDACTED***")
    for pat in _SECRET_PATTERNS:
        out = pat.sub(lambda m: m.group(1) + "***REDACTED***", out)
    return out


# ------------------------------------------------------ prompt injection

_INJECTION_PATTERNS = [
    r"ignore (all|any|the)? ?(previous|prior|above) (instructions|prompts?)",
    r"disregard (the )?(system|previous) (prompt|instructions)",
    r"you are now",
    r"system prompt",
    r"send (an )?e-?mail",
    r"reveal (your|the) (api|secret|key|password|instructions)",
    r"<\s*/?\s*(system|assistant|tender_data)\s*>",
    r"act as (an? )?",
    r"rate this (tender|opportunity) (as )?(100|p1|high)",
    r"mark (this|wayam) as (eligible|qualified)",
]
_INJ_RE = re.compile("|".join(_INJECTION_PATTERNS), re.I)


def screen_untrusted_text(text: str) -> tuple[str, list[str]]:
    """Neutralise obvious prompt-injection attempts inside source text.

    The text is still passed to the model as *data* (inside delimiters, with an
    instruction that it is untrusted), but matched phrases are defanged and
    reported so the record can be flagged for human review."""
    flags = sorted({m.group(0).lower() for m in _INJ_RE.finditer(text or "")})
    cleaned = _INJ_RE.sub(lambda m: "[removed-instruction-like-text]", text or "")
    cleaned = cleaned.replace("<tender_data>", "").replace("</tender_data>", "")
    cleaned = "".join(ch for ch in cleaned if ch in "\n\t" or ch.isprintable())
    return cleaned, flags
