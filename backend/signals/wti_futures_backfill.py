"""Explicit WTI front-month futures history replacement.

Manual/one-off only. This replaces the WTI chart series with EIA PET.RCLC1.D for the
requested ten-year window and does not collect or modify any other economic series.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from common import SupabaseRest
from sources.eia_wti_futures import SOURCE, fetch_wti_futures_rows

TABLE = "economic_chart_points"


def backfill(days: int = 3660) -> int:
    today = date.today()
    start = today - timedelta(days=days)
    rows = fetch_wti_futures_rows(start, today)
    if not rows:
        raise RuntimeError("No WTI front-month futures rows were returned.")
    db = SupabaseRest()
    db.upsert(TABLE, rows, conflict="series_code,observation_date")
    print(json.dumps({
        "mode": "wti_front_month_backfill",
        "source": SOURCE,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "rows": len(rows),
    }, sort_keys=True))
    return len(rows)


def main() -> None:
    backfill()


if __name__ == "__main__":
    main()
