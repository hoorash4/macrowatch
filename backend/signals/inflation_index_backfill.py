"""Explicit full replacement of up to twenty years of official inflation indexes."""

from __future__ import annotations

import json
from datetime import date

from common import SupabaseRest
from signals.economic_chart_pipeline import TABLE
from sources.inflation_indexes import ALL_SERIES_CODES, fetch_all_indexes


def backfill(today: date | None = None, db: SupabaseRest | None = None) -> dict[str, int]:
    end = today or date.today()
    start = date(end.year - 20, end.month, 1)
    database = db or SupabaseRest()

    # Fetch and validate every provider before making the first database change.
    source_rows = fetch_all_indexes(start, end)
    expected = set(ALL_SERIES_CODES)
    if set(source_rows) != expected:
        raise RuntimeError("Inflation backfill source set is incomplete")

    counts: dict[str, int] = {}
    for code in ALL_SERIES_CODES:
        rows = source_rows[code]
        database.upsert(TABLE, rows, conflict="series_code,observation_date")
        source_dates = {str(row["observation_date"]) for row in rows}
        existing = database.request("GET", TABLE, params={
            "select": "observation_date",
            "series_code": f"eq.{code}",
            "observation_date": f"gte.{start.isoformat()}",
            "and": f"(observation_date.lte.{end.isoformat()})",
            "limit": "10000",
        }) or []
        stale = [str(row["observation_date"]) for row in existing if str(row["observation_date"]) not in source_dates]
        for observed in stale:
            database.request("DELETE", TABLE, params={
                "series_code": f"eq.{code}", "observation_date": f"eq.{observed}",
            }, prefer="return=minimal")
        counts[code] = len(rows)

    print(json.dumps({
        "mode": "backfill",
        "stage": "inflation-indexes",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": counts,
    }, ensure_ascii=False, sort_keys=True))
    return counts


def main() -> None:
    backfill()


if __name__ == "__main__":
    main()
