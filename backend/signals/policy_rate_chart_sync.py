"""One-time projection of stored 2009+ FOMC rates into the chart read model."""
from __future__ import annotations

import json
from datetime import date

from common import SupabaseRest
from signals.economic_chart_pipeline import TABLE
from sources.policy_rates import fetch_us_policy_rate_chart_rows


SYNC_START = date(2009, 1, 1)


def _batches(rows: list[dict[str, object]], size: int = 500):
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def sync(today: date | None = None, db: SupabaseRest | None = None) -> dict[str, object]:
    end = today or date.today()
    database = db or SupabaseRest()
    rows = fetch_us_policy_rate_chart_rows(database, SYNC_START, end)
    if not rows or not str(rows[0]["observation_date"]).startswith("2009-"):
        raise RuntimeError("Stored FOMC policy-rate history does not begin in 2009")

    # Complete and validate the projection before changing chart storage.
    expected = {str(row["observation_date"]) for row in rows}
    for batch in _batches(rows):
        database.upsert(TABLE, batch, conflict="series_code,observation_date")

    existing = database.request("GET", TABLE, params={
        "select": "observation_date",
        "series_code": "eq.US_POLICY_RATE_MID",
        "observation_date": f"gte.{SYNC_START.isoformat()}",
        "and": f"(observation_date.lte.{end.isoformat()})",
        "limit": "10000",
    }) or []
    for row in existing:
        observed = str(row.get("observation_date") or "")
        if observed and observed not in expected:
            database.request("DELETE", TABLE, params={
                "series_code": "eq.US_POLICY_RATE_MID",
                "observation_date": f"eq.{observed}",
            }, prefer="return=minimal")

    result: dict[str, object] = {
        "rows": len(rows),
        "earliest": rows[0]["observation_date"],
        "latest": rows[-1]["observation_date"],
    }
    print(json.dumps({"mode": "stored-db-chart-sync", **result}, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    sync()


if __name__ == "__main__":
    main()
