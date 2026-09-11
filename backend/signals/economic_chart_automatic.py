"""Incremental automatic collection for economic charts.

Only a short recent source window is inspected. Historical backfill is a separate explicit
entrypoint and is never imported or invoked from this scheduled collector.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from common import SupabaseRest
from signals.economic_chart_pipeline import (
    ECOS_SERIES,
    FRED_SERIES,
    _derive_spread,
    _ecos_rows,
    _fred_rows,
    _insert_missing,
    check_collected_series_alerts,
)
from signals.korea_export_chart import derive_missing_segments, insert_missing_snapshots
from sources.korea_export_intramonth import fetch_snapshots
from sources.krx_index_fundamentals import (
    SERIES as KRX_INDEX_FUNDAMENTALS,
    fetch_krx_kospi_fundamental_day,
    is_krx_business_day,
)
from sources.redbook import fetch_recent_redbook_rows
from sources.us_treasury_yields import fetch_treasury_real_yield_rows, fetch_treasury_yield_rows
from sources.wti_futures import fetch_wti_futures_rows
from sources.yahoo_daily import fetch_yahoo_daily_rows

LIVE_NON_FRED_SERIES = {"US2Y", "US10Y", "US10Y2Y", "WTI", "USDKRW"}
KST = ZoneInfo("Asia/Seoul")


def collect() -> tuple[dict[str, int], dict[str, str]]:
    """Collect the regular economic-chart series except KOSPI valuation."""
    today = date.today()
    start = today - timedelta(days=45)
    db = SupabaseRest()
    inserted: dict[str, int] = {}
    errors: dict[str, str] = {}

    def run(name: str, action) -> None:
        try:
            inserted[name] = int(action())
        except Exception as error:
            inserted.setdefault(name, 0)
            errors[name] = f"{error.__class__.__name__}: {error}"
            print(json.dumps({
                "stage": "economic_chart_automatic_error",
                "series": name,
                "error": errors[name],
            }, ensure_ascii=False))

    for code, (source_id, frequency) in FRED_SERIES.items():
        if code in LIVE_NON_FRED_SERIES:
            continue
        run(code, lambda code=code, source_id=source_id, frequency=frequency: _insert_missing(
            db, _fred_rows(code, source_id, frequency, start, today), start
        ))

    try:
        treasury_rows = fetch_treasury_yield_rows(start, today)
        for code in ("US2Y", "US10Y"):
            run(code, lambda code=code: _insert_missing(db, treasury_rows.get(code, []), start))
    except Exception as error:
        for code in ("US2Y", "US10Y"):
            inserted.setdefault(code, 0)
            errors[code] = f"{error.__class__.__name__}: {error}"

    try:
        treasury_real_rows = fetch_treasury_real_yield_rows(start, today)
        run("US10Y_REAL", lambda: _insert_missing(db, treasury_real_rows.get("US10Y_REAL", []), start))
    except Exception as error:
        inserted.setdefault("US10Y_REAL", 0)
        errors["US10Y_REAL"] = f"{error.__class__.__name__}: {error}"

    run("US10Y2Y", lambda: _derive_spread(db, "US10Y2Y", "US10Y", "US2Y", "D", start, today))
    run("WTI", lambda: _insert_missing(db, fetch_wti_futures_rows(start, today), start))
    run("USDKRW", lambda: _insert_missing(db, fetch_yahoo_daily_rows("USDKRW", "KRW=X", start, today), start))

    for code, (stat_code, item_code, frequency) in ECOS_SERIES.items():
        run(code, lambda code=code, stat_code=stat_code, item_code=item_code, frequency=frequency: _insert_missing(
            db, _ecos_rows(code, stat_code, item_code, frequency, start, today), start
        ))

    run("KR10Y3Y", lambda: _derive_spread(db, "KR10Y3Y", "KR10Y", "KR3Y", "D", start, today))
    run("REDBOOK", lambda: _insert_missing(db, fetch_recent_redbook_rows(), start))

    export_start_month = (today - timedelta(days=65)).replace(day=1)
    try:
        snapshots, export_errors = fetch_snapshots(export_start_month, max_pages=6)
        if export_errors:
            errors["KR_EXPORT_SOURCE"] = " | ".join(export_errors[:5])
        inserted["KR_EXPORT_RAW"] = insert_missing_snapshots(db, snapshots, export_start_month)
        inserted["KR_EXPORT_DAILY_AVG"] = derive_missing_segments(db, export_start_month)
    except Exception as error:
        inserted.setdefault("KR_EXPORT_DAILY_AVG", 0)
        errors["KR_EXPORT_DAILY_AVG"] = f"{error.__class__.__name__}: {error}"

    changed = {code for code, count in inserted.items() if count > 0 and code != "KR_EXPORT_RAW"}
    alerts = check_collected_series_alerts(db, changed)
    print(json.dumps({
        "mode": "automatic",
        "phase": "core",
        "start": start.isoformat(),
        "end": today.isoformat(),
        "inserted": inserted,
        "errors": errors,
        "alerts": alerts,
    }, ensure_ascii=False, sort_keys=True))
    return inserted, errors


def collect_kospi_valuation(target: date | None = None, db: SupabaseRest | None = None) -> dict[str, int]:
    """Collect exactly one KRX trading day's KOSPI PER/PBR; failures propagate to Actions."""
    target = target or datetime.now(KST).date()
    if not is_krx_business_day(target):
        print(json.dumps({
            "mode": "automatic",
            "phase": "kospi-valuation",
            "date": target.isoformat(),
            "skipped": "krx_market_closed",
        }, ensure_ascii=False))
        return {code: 0 for code in KRX_INDEX_FUNDAMENTALS}

    database = db or SupabaseRest()
    rows_by_code = fetch_krx_kospi_fundamental_day(target)
    inserted = {
        code: _insert_missing(database, rows_by_code[code], target)
        for code in KRX_INDEX_FUNDAMENTALS
    }
    changed = {code for code, count in inserted.items() if count > 0}
    alerts = check_collected_series_alerts(database, changed)
    print(json.dumps({
        "mode": "automatic",
        "phase": "kospi-valuation",
        "date": target.isoformat(),
        "inserted": inserted,
        "alerts": alerts,
    }, ensure_ascii=False, sort_keys=True))
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("core", "kospi-valuation", "all"), default="all")
    args = parser.parse_args()
    if args.phase in {"core", "all"}:
        collect()
    if args.phase in {"kospi-valuation", "all"}:
        collect_kospi_valuation()


if __name__ == "__main__":
    main()
