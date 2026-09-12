"""Recent-only automatic collection of official U.S. and Korean inflation indexes."""

from __future__ import annotations

import json
from datetime import date

from common import AUTOMATIC_MONTHLY_PERIODS, SupabaseRest, month_start_months_ago
from signals.economic_chart_pipeline import _insert_missing
from sources.inflation_indexes import fetch_all_indexes


def collect(today: date | None = None, db: SupabaseRest | None = None) -> dict[str, int]:
    end = today or date.today()
    start = month_start_months_ago(end, AUTOMATIC_MONTHLY_PERIODS - 1)
    database = db or SupabaseRest()
    source_rows = fetch_all_indexes(start, end)
    inserted = {
        code: _insert_missing(database, rows, start)
        for code, rows in source_rows.items()
    }
    print(json.dumps({
        "mode": "automatic",
        "stage": "inflation-indexes",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "inserted": inserted,
    }, ensure_ascii=False, sort_keys=True))
    return inserted


def main() -> None:
    collect()


if __name__ == "__main__":
    main()
