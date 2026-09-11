"""Explicit WTI continuous front-month futures history replacement.

Manual/one-off only. It fetches the full replacement first, then replaces only the WTI rows
inside the ten-year window. No other economic series is touched.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from common import SupabaseRest
from sources.wti_futures import SOURCE, fetch_wti_futures_rows

TABLE = "economic_chart_points"


def backfill(days: int = 3660) -> int:
    today = date.today()
    start = today - timedelta(days=days)
    rows = fetch_wti_futures_rows(start, today)
    if not rows:
        raise RuntimeError("No WTI continuous front-month futures rows were returned.")

    # Fetch succeeded before any destructive write. Replace only WTI in the explicit
    # requested window so the series cannot remain a spot/futures mixture.
    db = SupabaseRest()
    db.request(
        "DELETE",
        TABLE,
        params={
            "series_code": "eq.WTI",
            "observation_date": f"gte.{start.isoformat()}",
        },
        prefer="return=minimal",
    )
    db.upsert(TABLE, rows, conflict="series_code,observation_date")
    print(json.dumps({
        "mode": "wti_continuous_front_month_backfill",
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
