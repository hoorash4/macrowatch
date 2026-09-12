"""One-time/resumable backfill for monthly business-credit economic-chart series."""
from __future__ import annotations

from datetime import date
import os

from signals.economic_chart_pipeline import _insert_missing
from sources.business_credit_monthly import (
    fetch_epiq_ch11_rows,
    fetch_equifax_rows,
    fetch_korea_business_delinquency_rows,
    fetch_korea_default_company_rows,
)


def _ten_year_start(today: date) -> date:
    try:
        return today.replace(year=today.year - 10, day=1)
    except ValueError:
        return date(today.year - 10, today.month, 1)


def run() -> dict[str, int]:
    end = date.today()
    start = _ten_year_start(end)
    totals: dict[str, int] = {}

    # Insert source-by-source so a later source timeout never loses earlier progress.
    for code, rows in fetch_equifax_rows(start, end).items():
        inserted = _insert_missing(rows)
        totals[code] = inserted
        print({"series": code, "fetched": len(rows), "inserted": inserted,
               "first": rows[0]["observation_date"] if rows else None,
               "last": rows[-1]["observation_date"] if rows else None})

    rows = fetch_epiq_ch11_rows(start, end, max_pages=int(os.getenv("EPIQ_BACKFILL_PAGES", "20")))
    inserted = _insert_missing(rows)
    totals["US_COMMERCIAL_CH11"] = inserted
    print({"series": "US_COMMERCIAL_CH11", "fetched": len(rows), "inserted": inserted,
           "first": rows[0]["observation_date"] if rows else None,
           "last": rows[-1]["observation_date"] if rows else None})

    rows = fetch_korea_business_delinquency_rows(start, end)
    inserted = _insert_missing(rows)
    totals["KR_CORP_DELINQ"] = inserted
    print({"series": "KR_CORP_DELINQ", "fetched": len(rows), "inserted": inserted,
           "first": rows[0]["observation_date"] if rows else None,
           "last": rows[-1]["observation_date"] if rows else None})

    rows = fetch_korea_default_company_rows(start, end)
    inserted = _insert_missing(rows)
    totals["KR_DEFAULT_COMPANIES"] = inserted
    print({"series": "KR_DEFAULT_COMPANIES", "fetched": len(rows), "inserted": inserted,
           "first": rows[0]["observation_date"] if rows else None,
           "last": rows[-1]["observation_date"] if rows else None})

    return totals


if __name__ == "__main__":
    print({"inserted": run()})
