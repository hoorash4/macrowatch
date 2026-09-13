"""Explicit authoritative 2009+ backfill for Korea/U.S. policy rates."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from common import SupabaseRest
from signals.economic_chart_pipeline import TABLE
from sources.policy_rates import (
    fetch_korea_policy_rate_rows,
    fetch_us_policy_rate_rows,
    us_policy_change_chart_rows,
)


BACKFILL_START = date(2009, 1, 1)


def _batches(rows: list[dict[str, object]], size: int = 500):
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def _replace_chart_series(
    db: SupabaseRest,
    code: str,
    rows: list[dict[str, object]],
    end: date,
) -> None:
    for batch in _batches(rows):
        db.upsert(TABLE, batch, conflict="series_code,observation_date")
    expected = {str(row["observation_date"]) for row in rows}
    existing = db.request("GET", TABLE, params={
        "select": "observation_date",
        "series_code": f"eq.{code}",
        "observation_date": f"gte.{BACKFILL_START.isoformat()}",
        "and": f"(observation_date.lte.{end.isoformat()})",
        "limit": "10000",
    }) or []
    for row in existing:
        observed = str(row.get("observation_date") or "")
        if observed and observed not in expected:
            db.request("DELETE", TABLE, params={
                "series_code": f"eq.{code}",
                "observation_date": f"eq.{observed}",
            }, prefer="return=minimal")


def backfill(today: date | None = None, db: SupabaseRest | None = None) -> dict[str, int]:
    end = today or date.today()
    database = db or SupabaseRest()

    # Both external histories must be complete before this function changes DB state.
    us_source = fetch_us_policy_rate_rows(BACKFILL_START, end)
    kr_chart_rows = fetch_korea_policy_rate_rows(BACKFILL_START, end)
    us_chart_rows = us_policy_change_chart_rows(us_source)
    if not us_source or not us_chart_rows or not kr_chart_rows:
        raise RuntimeError("Policy-rate backfill source validation failed")

    stamped = datetime.now(timezone.utc).isoformat()
    for batch in _batches([{**row, "updated_at": stamped} for row in us_source]):
        database.upsert("us_policy_rate_daily", batch, conflict="observed_on")
    _replace_chart_series(database, "US_POLICY_RATE_MID", us_chart_rows, end)
    _replace_chart_series(database, "KR_POLICY_RATE", kr_chart_rows, end)
    counts = {
        "us_policy_rate_daily": len(us_source),
        "US_POLICY_RATE_MID": len(us_chart_rows),
        "KR_POLICY_RATE": len(kr_chart_rows),
    }
    print(json.dumps({
        "mode": "backfill",
        "stage": "policy-rates",
        "start": BACKFILL_START.isoformat(),
        "end": end.isoformat(),
        "rows": counts,
    }, ensure_ascii=False, sort_keys=True))
    return counts


def main() -> None:
    backfill()


if __name__ == "__main__":
    main()
