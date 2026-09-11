"""Explicit historical backfill for Korean export chart data.

Historical 1-10/1-20/month-end KCS releases are used when available.  Missing historical
coverage falls back to official ECOS monthly export amounts divided by Korean export working
days.  The scheduled collector remains KCS intra-month only, so monthly fallback never leaks
into ongoing automatic collection.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from common import SupabaseRest
from signals.economic_chart_pipeline import _insert_missing
from signals.korea_export_chart import derive_missing_segments, insert_missing_snapshots
from sources.korea_export_intramonth import fetch_snapshots
from sources.korea_export_monthly import fetch_monthly_export_rows, historical_month_end_cutoff


def main() -> None:
    today = date.today()
    start = today - timedelta(days=3660)
    start_month = start.replace(day=1)
    db = SupabaseRest()

    # First preserve every historical KCS intra-month snapshot that is actually available.
    snapshots, errors = fetch_snapshots(start_month)
    raw_inserted = insert_missing_snapshots(db, snapshots, start_month)
    segments_inserted = derive_missing_segments(db, start_month)

    # Then fill completed historical months with an official monthly daily-average fallback.
    # _insert_missing never replaces an existing KCS month-end segment on the same date.
    monthly_end = historical_month_end_cutoff(today)
    monthly_rows = fetch_monthly_export_rows(start_month, monthly_end.replace(day=1))
    monthly_inserted = _insert_missing(db, monthly_rows, start_month) if monthly_rows else 0

    print(json.dumps({
        "mode": "backfill",
        "start_month": start_month.isoformat(),
        "snapshots_fetched": len(snapshots),
        "raw_inserted": raw_inserted,
        "segments_inserted": segments_inserted,
        "monthly_fallback_rows": len(monthly_rows),
        "monthly_fallback_inserted": monthly_inserted,
        "errors": errors,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
