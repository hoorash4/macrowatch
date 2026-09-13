"""Short-window automatic collection for Korea/U.S. policy-rate chart series."""
from __future__ import annotations

import json
from datetime import date

from common import AUTOMATIC_MONTHLY_PERIODS, SupabaseRest, month_start_months_ago
from signals.economic_chart_pipeline import _insert_missing, _insert_missing_derived
from sources.policy_rates import (
    fetch_korea_policy_rate_rows,
    fetch_us_policy_rate_chart_rows,
)


# Automatic reconciliation is limited to the latest five FOMC decisions.
US_HISTORY_START = date(2009, 1, 1)
US_RECENT_DECISIONS = 5


def collect_us(today: date | None = None, db: SupabaseRest | None = None) -> int:
    end = today or date.today()
    database = db or SupabaseRest()
    us_chart_rows = fetch_us_policy_rate_chart_rows(
        database, US_HISTORY_START, end, recent_limit=US_RECENT_DECISIONS,
    )
    us_check_start = date.fromisoformat(str(us_chart_rows[0]["observation_date"])) if us_chart_rows else end
    inserted = _insert_missing_derived(database, us_chart_rows, us_check_start)
    print(json.dumps({
        "mode": "automatic",
        "stage": "us-policy-rate",
        "us_recent_decisions": US_RECENT_DECISIONS,
        "inserted": inserted,
    }, ensure_ascii=False, sort_keys=True))
    return inserted


def collect_korea(today: date | None = None, db: SupabaseRest | None = None) -> int:
    end = today or date.today()
    database = db or SupabaseRest()
    start = month_start_months_ago(end, AUTOMATIC_MONTHLY_PERIODS - 1)
    rows = fetch_korea_policy_rate_rows(start, end)
    inserted = _insert_missing(database, rows, start)
    print(json.dumps({
        "mode": "automatic", "stage": "korea-policy-rate",
        "start": start.isoformat(), "inserted": inserted,
    }, ensure_ascii=False, sort_keys=True))
    return inserted


def main() -> None:
    database = SupabaseRest()
    collect_us(db=database)
    collect_korea(db=database)


if __name__ == "__main__":
    main()
