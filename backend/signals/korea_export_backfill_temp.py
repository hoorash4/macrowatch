"""Temporary one-off Korea export backfill. Delete after verified run."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from common import SupabaseRest
from sources.korea_export_intramonth import fetch_snapshots, independent_segment_rows
from sources.korea_export_monthly import monthly_row_from_snapshot

SERIES = "KR_EXPORT_DAILY_AVG"
START = date(2016, 1, 1)
RAW_TABLE = "korea_export_intramonth_snapshots"


def main() -> None:
    today = date.today()
    snapshots, errors = fetch_snapshots(START, max_pages=80)
    if not snapshots:
        raise RuntimeError(f"No KCS snapshots fetched; errors={errors[:10]}")

    by_month = defaultdict(list)
    for snapshot in snapshots:
        by_month[snapshot.reference_month].append(snapshot)

    segment_rows = independent_segment_rows(snapshots)
    segment_months = {date.fromisoformat(row["observation_date"]).replace(day=1) for row in segment_rows}

    monthly_rows = []
    for month, items in sorted(by_month.items()):
        if month in segment_months or month >= today.replace(day=1):
            continue
        month_end = next((item for item in items if item.stage == "month_end"), None)
        if month_end is not None:
            monthly_rows.append(monthly_row_from_snapshot(month_end))

    rows = sorted(segment_rows + monthly_rows, key=lambda row: row["observation_date"])
    if not rows:
        raise RuntimeError("KCS backfill produced no rows")

    db = SupabaseRest()
    # Build first; only after a valid replacement exists do we replace the sparse old series/raw cache.
    db.request("DELETE", "economic_chart_points", params={"series_code": f"eq.{SERIES}"}, prefer="return=minimal")
    db.request("DELETE", RAW_TABLE, params={"reference_month": f"gte.{START.isoformat()}"}, prefer="return=minimal")

    raw_rows = [{
        "reference_month": s.reference_month.isoformat(),
        "stage": s.stage,
        "period_end": s.period_end.isoformat(),
        "cumulative_export_musd": s.cumulative_export_musd,
        "cumulative_workdays": s.cumulative_workdays,
        "published_on": s.published_on.isoformat() if s.published_on else None,
        "source_url": s.source_url,
    } for s in snapshots]
    if raw_rows:
        db.upsert(RAW_TABLE, raw_rows, conflict="reference_month,stage")
    db.upsert("economic_chart_points", rows, conflict="series_code,observation_date")

    source_counts = defaultdict(int)
    for row in rows:
        source_counts[row["source"]]+=1
    print(json.dumps({
        "stage": "korea_export_backfill_temp",
        "start": START.isoformat(),
        "today": today.isoformat(),
        "snapshots": len(snapshots),
        "rows": len(rows),
        "segment_rows": len(segment_rows),
        "monthly_fallback_rows": len(monthly_rows),
        "min_date": rows[0]["observation_date"],
        "max_date": rows[-1]["observation_date"],
        "source_counts": dict(source_counts),
        "errors_count": len(errors),
        "errors_sample": errors[:20],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
