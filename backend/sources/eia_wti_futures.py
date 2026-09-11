"""Official EIA front-month WTI futures history.

EIA publishes "Cushing, OK Crude Oil Future Contract 1" as PET.RCLC1.D.
Contract 1 is the continuously rolled nearest/front-month WTI futures series, so callers
must not stitch individual CL contract months themselves.
"""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from typing import Any

import requests
import xlrd

from common import request_with_retry

EIA_WTI_FUTURES_XLS = "https://www.eia.gov/dnav/pet/hist_xls/RCLC1d.xls"
SOURCE = "EIA:PET.RCLC1.D"


def _cell_date(book: xlrd.book.Book, cell: xlrd.sheet.Cell) -> date | None:
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xlrd.xldate_as_datetime(cell.value, book.datemode).date()
    raw = str(cell.value or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _cell_number(cell: xlrd.sheet.Cell) -> float | None:
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        value = float(cell.value)
        return value if value == value else None
    raw = str(cell.value or "").strip().replace(",", "")
    if not raw or raw in {"-", "--", "NA", "N/A"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_wti_futures_xls(content: bytes, start: date, end: date) -> list[dict[str, Any]]:
    book = xlrd.open_workbook(file_contents=content)
    rows: list[dict[str, Any]] = []
    for sheet in book.sheets():
        for row_index in range(sheet.nrows):
            row = sheet.row(row_index)
            if len(row) < 2:
                continue
            observed = _cell_date(book, row[0])
            value = _cell_number(row[1])
            if observed is None or value is None or not (start <= observed <= end):
                continue
            rows.append({
                "series_code": "WTI",
                "observation_date": observed.isoformat(),
                "value": value,
                "frequency": "D",
                "source": SOURCE,
            })
    unique = {str(row["observation_date"]): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def fetch_wti_futures_rows(start: date, end: date) -> list[dict[str, Any]]:
    response = request_with_retry(lambda: requests.get(EIA_WTI_FUTURES_XLS, timeout=45))
    response.raise_for_status()
    rows = parse_wti_futures_xls(response.content, start, end)
    if not rows:
        raise RuntimeError("EIA WTI front-month futures history returned no usable rows.")
    return rows
