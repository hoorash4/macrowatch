"""Collect unified U.S. small-business delinquency and default series from public Equifax reports."""
from __future__ import annotations

from datetime import date
import json

from common import SupabaseRest
from signals.economic_chart_pipeline import _insert_missing
from sources.equifax_small_business import (
    SERIES_DEFAULT,
    SERIES_DELINQUENCY,
    fetch_equifax_rows,
)


def _months_ago(d: date, months: int) -> date:
    serial = d.year * 12 + d.month - 1 - months
    return date(serial // 12, serial % 12 + 1, 1)


def collect_recent() -> dict[str, int]:
    end = date.today()
    start = _months_ago(end, 18)
    rows = fetch_equifax_rows(start, end)
    db = SupabaseRest()
    counts = {
        SERIES_DELINQUENCY: _insert_missing(db, rows[SERIES_DELINQUENCY], start),
        SERIES_DEFAULT: _insert_missing(db, rows[SERIES_DEFAULT], start),
    }
    print(json.dumps({"mode": "automatic", "stage": "equifax", "inserted": counts}, ensure_ascii=False, sort_keys=True))
    return counts


if __name__ == "__main__":
    collect_recent()
