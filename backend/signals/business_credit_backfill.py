"""One-time/resumable backfill for monthly business-credit economic-chart series."""
from __future__ import annotations

from datetime import date
import json
import os

from common import SupabaseRest
from signals.economic_chart_pipeline import _insert_missing
from sources.business_credit_monthly import (
    fetch_epiq_ch11_rows,
    fetch_equifax_rows,
    fetch_korea_business_delinquency_rows,
    fetch_korea_default_company_rows,
)


def _ten_year_start(today: date) -> date:
    return date(today.year - 10, today.month, 1)


def _store(db: SupabaseRest, code: str, rows: list[dict], start: date, totals: dict[str, int]) -> None:
    inserted = _insert_missing(db, rows, start)
    totals[code] = inserted
    print(json.dumps({
        "series": code,
        "fetched": len(rows),
        "inserted": inserted,
        "first": rows[0]["observation_date"] if rows else None,
        "last": rows[-1]["observation_date"] if rows else None,
    }, ensure_ascii=False))


def run() -> dict[str, int]:
    end = date.today()
    start = _ten_year_start(end)
    db = SupabaseRest()
    totals: dict[str, int] = {}

    # Source-by-source writes make the job safe to resume after a later source timeout.
    equifax = fetch_equifax_rows(start, end)
    for code, rows in equifax.items():
        _store(db, code, rows, start, totals)

    _store(
        db,
        "US_COMMERCIAL_CH11",
        fetch_epiq_ch11_rows(start, end, max_pages=int(os.getenv("EPIQ_BACKFILL_PAGES", "20"))),
        start,
        totals,
    )
    _store(db, "KR_CORP_DELINQ", fetch_korea_business_delinquency_rows(start, end), start, totals)
    _store(db, "KR_DEFAULT_COMPANIES", fetch_korea_default_company_rows(start, end), start, totals)

    # KR_CORP_REHAB is intentionally not historical-backfilled. The regular monthly
    # collector keeps reading the recent Supreme Court tail and will add new releases.
    totals["KR_CORP_REHAB"] = 0
    return totals


if __name__ == "__main__":
    print(json.dumps({"inserted": run()}, ensure_ascii=False, sort_keys=True))
