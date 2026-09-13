"""Explicit full replacement of up to twenty years of official YoY inflation rates."""

from __future__ import annotations

import json
from datetime import date

from common import SupabaseRest, month_start_months_ago
from signals.canonical_series import TABLE, store as store_canonical_series
from sources.inflation_rates import ALL_SERIES_CODES, fetch_all_rates


def backfill(today: date | None = None, db: SupabaseRest | None = None) -> dict[str, int]:
    # A complete monthly YoY observation cannot exist for the current month.
    end = month_start_months_ago(today or date.today(), 1)
    start = date(end.year - 20, end.month, 1)
    database = db or SupabaseRest()

    # Fetch and validate every provider before making the first database change.
    source_rows = fetch_all_rates(start, end)
    expected = set(ALL_SERIES_CODES)
    if set(source_rows) != expected:
        raise RuntimeError("Inflation backfill source set is incomplete")

    counts: dict[str, int] = {}
    for code in ALL_SERIES_CODES:
        rows = source_rows[code]
        store_canonical_series(database, rows)
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
        "stage": "inflation-rates",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": counts,
    }, ensure_ascii=False, sort_keys=True))
    return counts


def main() -> None:
    backfill()


if __name__ == "__main__":
    main()
