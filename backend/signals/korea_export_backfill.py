"""Explicit historical backfill for Korean export chart data.

Historical backfill stores one official KCS month-end daily-average observation per completed
month. It never reconstructs 10-day/20-day segments. Any month that already contains a real
KCS intra-month observation is left entirely to the scheduled collector and is not backfilled.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from common import SupabaseRest
from signals.economic_chart_pipeline import TABLE, _insert_missing
from sources.korea_export_monthly import fetch_monthly_export_rows, historical_month_end_cutoff

SERIES_CODE = "KR_EXPORT_DAILY_AVG"
INTRAMONTH_SOURCE_PREFIX = "KCS:INTRAMONTH_EXPORT/"


def existing_intramonth_months(db: SupabaseRest, start_month: date, end_date: date) -> set[str]:
    """Return YYYY-MM months already owned by real KCS intra-month observations."""
    rows = db.request("GET", TABLE, params={
        "select": "observation_date,source",
        "series_code": f"eq.{SERIES_CODE}",
        "observation_date": f"gte.{start_month.isoformat()}",
        "and": f"(observation_date.lte.{end_date.isoformat()})",
        "order": "observation_date.asc",
        "limit": "10000",
    }) or []
    return {
        str(row.get("observation_date") or "")[:7]
        for row in rows
        if str(row.get("source") or "").startswith(INTRAMONTH_SOURCE_PREFIX)
        and len(str(row.get("observation_date") or "")) >= 7
    }


def monthly_rows_without_intramonth(
    rows: list[dict[str, Any]], protected_months: set[str]
) -> list[dict[str, Any]]:
    """Exclude whole months that already contain real 10-day KCS data."""
    return [
        row for row in rows
        if str(row.get("observation_date") or "")[:7] not in protected_months
    ]


def main() -> None:
    today = date.today()
    start = today - timedelta(days=3660)
    start_month = start.replace(day=1)
    db = SupabaseRest()

    monthly_end = historical_month_end_cutoff(today)
    monthly_rows, errors = fetch_monthly_export_rows(start_month, monthly_end.replace(day=1))
    protected_months = existing_intramonth_months(db, start_month, monthly_end)
    writable_rows = monthly_rows_without_intramonth(monthly_rows, protected_months)
    monthly_inserted = _insert_missing(db, writable_rows, start_month) if writable_rows else 0

    print(json.dumps({
        "mode": "backfill",
        "start_month": start_month.isoformat(),
        "monthly_rows_fetched": len(monthly_rows),
        "protected_intramonth_months": len(protected_months),
        "monthly_rows_writable": len(writable_rows),
        "monthly_rows_inserted": monthly_inserted,
        "errors": errors,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
