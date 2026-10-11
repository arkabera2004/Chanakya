"""Table extraction for PDF pages (bills of quantities, price schedules).

Plain text extraction interleaves the columns of a table, so "item, description,
quantity, unit, rate, amount" reaches the analyser as jumbled text. For pages that
carry a RULED table this module renders each row on one line, cells separated by
" | ", between ``[TABLE n ...]`` and ``[END TABLE n]`` markers, in reading order with
the rest of the page text.

Rules that keep this safe and bounded:
- Detection uses pdfplumber's default table finder (drawn lines only). A page with
  no drawn lines or rectangles is never touched: the caller keeps its plain text.
- Tables are data, never instructions. Cell text is flattened to one line and cannot
  forge the table markers; the excerpt that later reaches a model goes through the
  same injection screening as any other document text.
- Row budget per document (``documents.max_table_rows_per_document``). When it runs
  out, the remaining rows of that table follow as plain text and later pages fall
  back to plain text extraction, so the cap never drops content. Cells are cut at
  160 characters and tables at 12 columns.
- Any failure returns ``None`` so the caller falls back to plain text extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_CELL_CHARS = 160
MAX_COLUMNS = 12
MIN_ROWS = 2
MIN_COLUMNS = 2
MIN_FILLED_CELLS = 4

_WS = re.compile(r"\s+")
_MARKER = re.compile(r"\[\s*(END\s+)?TABLE\b", re.I)


@dataclass
class TableState:
    """Carried across the pages of one document."""

    rows_left: int
    tables: int = 0                    # running table number
    rows_rendered: int = 0
    rows_omitted: int = 0
    capped: bool = False
    last_page: int | None = None       # page number of the last table rendered
    last_header: list[str] = field(default_factory=list)


def clean_cell(value) -> str:
    text = _WS.sub(" ", str(value) if value is not None else "").strip()
    text = text.replace("|", "/")
    text = _MARKER.sub(lambda m: "(" + m.group(0)[1:].strip(), text)  # a cell cannot open or close a table block
    if len(text) > MAX_CELL_CHARS:
        text = text[: MAX_CELL_CHARS - 3].rstrip() + "..."
    return text


def clean_rows(raw: list[list]) -> list[list[str]]:
    rows = []
    for raw_row in raw or []:
        cells = [clean_cell(c) for c in (raw_row or [])][:MAX_COLUMNS]
        if any(cells):
            rows.append(cells)
    return rows


def qualifies(rows: list[list[str]]) -> bool:
    if len(rows) < MIN_ROWS or max((len(r) for r in rows), default=0) < MIN_COLUMNS:
        return False
    return sum(1 for r in rows for c in r if c) >= MIN_FILLED_CELLS


def has_ruling(page) -> bool:
    """Cheap pre-check: the default finder needs drawn lines or rectangles."""
    return bool(page.lines or page.rects or page.curves)


def render_table(rows: list[list[str]], page_no: int, state: TableState) -> str:
    """Render one table within the remaining row budget and update ``state``."""
    ncols = max(len(r) for r in rows)
    continued = (state.last_page == page_no - 1 and len(state.last_header) == ncols and bool(state.last_header))
    header = state.last_header if continued else rows[0]
    body = rows
    if continued and [c.casefold() for c in rows[0]] == [c.casefold() for c in header]:
        body = rows[1:]  # header repeated at the top of the next page
    state.tables += 1
    n = state.tables
    if continued:
        head = f"[TABLE {n} p.{page_no}, continues the table on p.{page_no - 1}; columns: {' | '.join(header)}]"
    else:
        head = f"[TABLE {n} p.{page_no}, {ncols} columns; first row is the header]"
    shown = body[: max(0, state.rows_left)]
    lines = [head] + [" | ".join(r) for r in shown]
    lines.append(f"[END TABLE {n}]")
    omitted = len(body) - len(shown)
    if omitted:
        lines.append(f"(table row limit for this document reached; {omitted} more rows follow as plain text)")
        lines += [" ".join(c for c in r if c) for r in body[len(shown):]]
        state.capped = True
    state.rows_left -= len(shown)
    state.rows_rendered += len(shown)
    state.rows_omitted += omitted
    state.last_page, state.last_header = page_no, header
    return "\n".join(lines)


def _inside(obj: dict, boxes: list[tuple[float, float, float, float]]) -> bool:
    cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
    return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)


def page_text_with_tables(page, page_no: int, state: TableState) -> str | None:
    """Text of a PDF page with its ruled tables rendered as delimited rows.

    Returns ``None`` when the page has no qualifying table, when the row budget is
    spent, or when anything goes wrong: the caller then keeps plain text extraction."""
    if state.rows_left <= 0:
        return None
    try:
        if not has_ruling(page):
            return None
        found = []
        for table in page.find_tables():
            rows = clean_rows(table.extract())
            if qualifies(rows):
                found.append((table.bbox, rows))
        if not found:
            return None
        found.sort(key=lambda item: (item[0][1], item[0][0]))
        boxes = [bbox for bbox, _ in found]
        px0, ptop, px1, pbottom = page.bbox
        rest = page.filter(lambda obj: obj.get("object_type") != "char" or not _inside(obj, boxes))

        def band(y0: float, y1: float) -> str:
            y0, y1 = max(y0, ptop), min(y1, pbottom)
            if y1 - y0 < 1:
                return ""
            return (rest.crop((px0, y0, px1, y1)).extract_text() or "").strip()

        parts: list[str] = []
        cursor = ptop
        for bbox, rows in found:
            # Text above (and beside) the table first, then the table itself.
            above = band(cursor, bbox[3])
            if above:
                parts.append(above)
            if state.rows_left > 0:
                parts.append(render_table(rows, page_no, state))
            else:
                state.capped = True
                state.rows_omitted += len(rows)
                parts.append("\n".join(" ".join(c for c in r if c) for r in rows))  # plain, nothing lost
            cursor = max(cursor, bbox[3])
        below = band(cursor, pbottom)
        if below:
            parts.append(below)
        return "\n".join(parts)
    except Exception:  # noqa: BLE001 - malformed page geometry must never break extraction
        return None
