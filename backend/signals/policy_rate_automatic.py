"""Short-window automatic collection for the combined Korea/U.S. policy-rate chart."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from common import AUTOMATIC_MONTHLY_PERIODS, SupabaseRest, month_start_months_ago
from signals.economic_chart_pipeline import TABLE, _insert_missing
from sources.policy_rates import (
    fetch_korea_policy_rate_rows,
    fetch_us_policy_rate_rows,
    us_policy_change_chart_rows,
)


# A 180-day context detects changes inside the 120-day write window while
# keeping automatic collection far away from the 2009 historical backfill.
US_CONTEXT_DAYS = 180
US_WRITE_DAYS = 120


def collect(today: date | None = None, db: SupabaseRest | None = None) -> dict[str, int]:
    end = today or date.today()
    database = db or SupabaseRest()
    us_context_start = end - timedelta(days=US_CONTEXT_DAYS)
    us_write_start = end - timedelta(days=US_WRITE_DAYS)
    kr_start = month_start_months_ago(end, AUTOMATIC_MONTHLY_PERIODS - 1)

    # Fetch both providers before the first write.  A source failure therefore
    # cannot leave one country updated and the other untouched.
    us_source = fetch_us_policy_rate_rows(us_context_start, end)
    kr_chart_rows = fetch_korea_policy_rate_rows(kr_start, end)
    us_recent = [
        row for row in us_source
        if str(row["observed_on"]) >= us_write_start.isoformat()
    ]
    us_chart_rows = us_policy_change_chart_rows(us_source, start=us_write_start)

    stamped = datetime.now(timezone.utc).isoformat()
    database.upsert(
        "us_policy_rate_daily",
        [{**row, "updated_at": stamped} for row in us_recent],
        conflict="observed_on",
    )
    counts = {
        "US_POLICY_RATE_MID": _insert_missing(database, us_chart_rows, us_write_start),
        "KR_POLICY_RATE": _insert_missing(database, kr_chart_rows, kr_start),
    }
    print(json.dumps({
        "mode": "automatic",
        "stage": "policy-rates",
        "us_context_start": us_context_start.isoformat(),
        "us_write_start": us_write_start.isoformat(),
        "kr_start": kr_start.isoformat(),
        "inserted": counts,
    }, ensure_ascii=False, sort_keys=True))
    return counts


def main() -> None:
    collect()


if __name__ == "__main__":
    main()
