"""Recent-only automatic collection of official U.S. and Korean YoY inflation rates."""

from __future__ import annotations

import json
from datetime import date

from common import AUTOMATIC_MONTHLY_PERIODS, SupabaseRest, month_start_months_ago
from sources.inflation_rates import fetch_all_rates


def collect(today: date | None = None, db: SupabaseRest | None = None) -> dict[str, int]:
    # Monthly YoY sources cannot publish a value for the in-progress month.
    end = month_start_months_ago(today or date.today(), 1)
    start = month_start_months_ago(end, AUTOMATIC_MONTHLY_PERIODS - 1)
    database = db or SupabaseRest()
    source_rows = fetch_all_rates(start, end)
    inserted = {}
    for code, rows in source_rows.items():
        database.upsert("economic_chart_points", rows, conflict="series_code,observation_date")
        inserted[code] = len(rows)
    print(json.dumps({
        "mode": "automatic",
        "stage": "inflation-rates",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "inserted": inserted,
    }, ensure_ascii=False, sort_keys=True))
    return inserted


def main() -> None:
    collect()


if __name__ == "__main__":
    main()
