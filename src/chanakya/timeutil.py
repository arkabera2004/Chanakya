"""Time handling.

Rule: every timestamp is stored in the database as naive UTC. Portal dates are
parsed as Asia/Kolkata (IST) unless the notice states another zone, and are
displayed in IST. Indian portals write dates day-first (``09-Oct-2026 10:53 PM``,
``02/07/2026 17:00``), so ambiguous numeric dates are always read day-first.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_FORMATS = (
    "%d-%b-%Y %I:%M %p",
    "%d-%b-%Y %H:%M",
    "%d-%b-%Y",
    "%d-%B-%Y %I:%M %p",
    "%d-%B-%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %I:%M %p",
    "%d/%m/%Y",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y %I:%M %p",
    "%d-%m-%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
    "%d %b %Y %I:%M %p",
    "%d %b %Y %H:%M",
    "%d %b %Y",
    "%d %B %Y",
)


def now_utc() -> datetime:
    """Current time as naive UTC (the storage convention)."""
    return datetime.now(UTC).replace(tzinfo=None)


def now_ist() -> datetime:
    return datetime.now(IST)


def _clean(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    t = t.replace(",", " ").replace("hrs", "").replace("Hrs", "").replace("IST", "")
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    # "up to 17:00" / "upto 17:00"
    t = re.sub(r"\bup ?to\b", "", t, flags=re.I).strip()
    return t


def parse_portal_datetime(value: str | None, tz: ZoneInfo = IST) -> datetime | None:
    """Parse a portal date string into an aware datetime in ``tz`` (default IST).

    Returns ``None`` for empty or unparseable input — callers must treat that as
    "not stated", never as a default date.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw or raw in {"--", "-", "NA", "N/A", "null"}:
        return None
    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
    except ValueError:
        pass
    text = _clean(raw)
    for fmt in _FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=tz)
        except ValueError:
            continue
    m = re.search(r"(\d{1,2}[-/.][A-Za-z]{3,9}[-/.]\d{4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4})(?:\s+(\d{1,2}:\d{2})(?:\s*([AaPp][Mm]))?)?", text)
    if m:
        d, t, ampm = m.group(1), m.group(2), m.group(3)
        candidate = d.replace(".", "-").replace("/", "-")
        if t:
            candidate += f" {t}" + (f" {ampm.upper()}" if ampm else "")
        for fmt in ("%d-%b-%Y %I:%M %p", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y %I:%M %p",
                    "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d-%B-%Y %H:%M", "%d-%B-%Y"):
            try:
                return datetime.strptime(candidate, fmt).replace(tzinfo=tz)
            except ValueError:
                continue
    return None


def to_storage(dt: datetime | None) -> datetime | None:
    """Aware datetime -> naive UTC for storage. Naive input is assumed IST."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(UTC).replace(tzinfo=None)


def from_storage(dt: datetime | None, tz: ZoneInfo = IST) -> datetime | None:
    """Naive UTC from storage -> aware datetime in ``tz``."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC).astimezone(tz)


def days_remaining(closing_utc_naive: datetime | None, now: datetime | None = None) -> float | None:
    """Days (fractional) until closing. Negative when already closed."""
    if closing_utc_naive is None:
        return None
    now = now or now_utc()
    return (closing_utc_naive - now).total_seconds() / 86400.0


def ist_date_str(d: datetime | date | None = None) -> str:
    if d is None:
        return now_ist().strftime("%Y-%m-%d")
    if isinstance(d, datetime):
        return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(IST).strftime("%Y-%m-%d")
    return d.isoformat()


def format_ist(dt_utc_naive: datetime | None) -> str:
    if dt_utc_naive is None:
        return ""
    return from_storage(dt_utc_naive).strftime("%d-%b-%Y %I:%M %p IST")


def start_of_ist_day_utc(now: datetime | None = None) -> datetime:
    """Start of the current IST day, as naive UTC (for daily budget windows)."""
    ist_now = (now.replace(tzinfo=UTC).astimezone(IST) if now else now_ist())
    start = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).replace(tzinfo=None)


def window_start(days: int, now: datetime | None = None) -> datetime:
    return (now or now_utc()) - timedelta(days=days)
