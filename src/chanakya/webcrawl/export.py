"""CSV/XLSX export of extracted WebsiteTenders. Every row carries the source URL and, for
every field, the exact excerpt the value came from — exports must stay as traceable as the
UI, never a flattened summary that drops evidence.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from openpyxl import Workbook

_FIXED_COLUMNS = ("source_url",)
_TRAILING_COLUMNS = ("top_capability", "top_capability_score")


def _field_columns(tenders: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for tender in tenders:
        for name in tender.get("fields", {}):
            if name not in names:
                names.append(name)
    return names


def _row_for(tender: dict[str, Any], field_names: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = {"source_url": tender["source_url"]}
    fields = tender.get("fields", {})
    for name in field_names:
        entry = fields.get(name) or {}
        row[name] = entry.get("value")
        row[f"{name}_excerpt"] = entry.get("excerpt")
    matches = tender.get("matches") or []
    row["top_capability"] = matches[0]["capability_id"] if matches else None
    row["top_capability_score"] = matches[0]["score"] if matches else None
    return row


def _header_for(field_names: list[str]) -> list[str]:
    header = list(_FIXED_COLUMNS)
    for name in field_names:
        header.append(name)
        header.append(f"{name}_excerpt")
    header.extend(_TRAILING_COLUMNS)
    return header


def export_csv(tenders: list[dict[str, Any]]) -> str:
    field_names = _field_columns(tenders)
    header = _header_for(field_names)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header)
    writer.writeheader()
    for tender in tenders:
        writer.writerow(_row_for(tender, field_names))
    return buf.getvalue()


def export_xlsx(tenders: list[dict[str, Any]]) -> bytes:
    field_names = _field_columns(tenders)
    header = _header_for(field_names)
    wb = Workbook()
    ws = wb.active
    ws.title = "Tenders"
    ws.append(header)
    for tender in tenders:
        row = _row_for(tender, field_names)
        ws.append([row.get(col) for col in header])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
