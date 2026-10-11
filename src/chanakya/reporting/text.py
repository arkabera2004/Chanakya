"""Plain punctuation for anything a person reads: the desk, the digest, the alert.

Stored values keep their original form (statuses, flags and reasons are compared
and exported as written). Only the copy shown to people is rewritten.
"""

from __future__ import annotations

from typing import Any

_DASH = " \u2014 "
# Identifiers and official text are shown exactly as the portal published them.
VERBATIM = frozenset({"id", "title", "tender_id", "ref_no", "official_url", "url", "description", "text", "old", "new", "at"})


def plain(value: Any) -> Any:
    """Replace spaced em dashes with a colon or a comma, through strings, lists and dicts."""
    if isinstance(value, str):
        if _DASH not in value:
            return value
        head, *rest = value.split(_DASH)
        for part in rest:
            head += (", " if ":" in part[:16] else ": ") + part
        return head
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {key: (item if key in VERBATIM else plain(item)) for key, item in value.items()}
    return value
