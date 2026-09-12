"""Collect recent national PayNet loan-performance series."""
from __future__ import annotations

from datetime import date
import json

from common import AUTOMATIC_MONTHLY_PERIODS, SupabaseRest, month_start_months_ago
from sources.paynet_loan_performance import SERIES_DEFAULT, SERIES_DELINQUENCY, fetch_paynet_rows
from signals.economic_chart_pipeline import _insert_missing


def collect_recent() -> dict[str, int]:
    end = date.today()
    start = month_start_months_ago(end, AUTOMATIC_MONTHLY_PERIODS - 1)
    rows = fetch_paynet_rows(start, end)
    db = SupabaseRest()
    counts = {
        SERIES_DELINQUENCY: _insert_missing(db, rows[SERIES_DELINQUENCY], start),
        SERIES_DEFAULT: _insert_missing(db, rows[SERIES_DEFAULT], start),
    }
    print(json.dumps({"mode": "automatic", "stage": "paynet", "inserted": counts}, ensure_ascii=False, sort_keys=True))
    return counts


if __name__ == "__main__":
    collect_recent()
