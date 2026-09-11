"""Explicit historical backfill for KCS intra-month export chart data."""
from __future__ import annotations

import json
from datetime import date, timedelta

from common import SupabaseRest
from signals.korea_export_chart import derive_missing_segments, insert_missing_snapshots
from sources.korea_export_intramonth import fetch_snapshots


def main() -> None:
    today = date.today()
    start = today - timedelta(days=3660)
    start_month = start.replace(day=1)
    snapshots, errors = fetch_snapshots(start_month)
    db = SupabaseRest()
    raw_inserted = insert_missing_snapshots(db, snapshots, start_month)
    segments_inserted = derive_missing_segments(db, start_month)
    print(json.dumps({
        "mode": "backfill",
        "start_month": start_month.isoformat(),
        "snapshots_fetched": len(snapshots),
        "raw_inserted": raw_inserted,
        "segments_inserted": segments_inserted,
        "errors": errors,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
