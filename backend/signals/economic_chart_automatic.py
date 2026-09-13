"""Collect every regular economic-chart series in one scheduled refresh.

Only a short recent source window is inspected. Historical backfill is a separate explicit
entrypoint and is never imported or invoked from this scheduled collector.
"""
from __future__ import annotations

import json
import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from common import (
    AUTOMATIC_DAILY_CALENDAR_DAYS,
    AUTOMATIC_DAILY_VALUES,
    AUTOMATIC_MONTHLY_PERIODS,
    AUTOMATIC_WEEKLY_WEEKS,
    SupabaseRest,
    month_start_months_ago,
    require_env,
)
from signals.economic_chart_pipeline import (
    ECOS_SERIES,
    FRED_SERIES,
    _derive_spread,
    _ecos_rows,
    _fred_rows,
    _fred_recent_rows,
    _insert_missing,
    check_collected_series_alerts,
)
from signals.policy_rate_automatic import collect_korea as collect_korea_policy_rate
from signals.policy_rate_automatic import collect_us as collect_us_policy_rate
from signals.korea_export_chart import derive_missing_segments, insert_missing_snapshots
from sources.census_retail_sales import chart_rows as census_retail_chart_rows
from sources.census_retail_sales import fetch_census_retail_sales
from sources.korea_export_intramonth import fetch_snapshots
from sources.krx_index_fundamentals import (
    SERIES as KRX_INDEX_FUNDAMENTALS,
    fetch_krx_kospi_fundamental_day,
    is_krx_business_day,
)
from sources.redbook import fetch_recent_redbook_rows
from sources.us_treasury_yields import fetch_treasury_real_yield_rows, fetch_treasury_yield_rows
from sources.wti_futures import fetch_wti_futures_rows

LIVE_NON_FRED_SERIES = {"US2Y", "US10Y", "WTI"}
KST = ZoneInfo("Asia/Seoul")


def latest_automatic_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: str(row["observation_date"]))[-AUTOMATIC_DAILY_VALUES:]


def collect_kospi_valuation(target: date | None = None, db: SupabaseRest | None = None) -> dict[str, int]:
    """Collect one completed KRX trading day's KOSPI PER/PBR with strict validation.

    The regular 07:30 KST refresh runs before the Korean market opens, so its default target is
    the previous Korean calendar day. Holidays/weekends are skipped; malformed or missing pykrx
    responses still propagate and fail the workflow instead of being silently accepted.
    """
    target = target or (datetime.now(KST).date() - timedelta(days=1))
    if not is_krx_business_day(target):
        print(json.dumps({
            "mode": "automatic",
            "stage": "kospi-valuation",
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
    print(json.dumps({
        "mode": "automatic",
        "stage": "kospi-valuation",
        "date": target.isoformat(),
        "inserted": inserted,
    }, ensure_ascii=False, sort_keys=True))
    return inserted


def collect_sources() -> tuple[dict[str, int], dict[str, str]]:
    """Fetch and persist only externally published observations."""
    today = date.today()
    starts = {
        "D": today - timedelta(days=AUTOMATIC_DAILY_CALENDAR_DAYS),
        "W": today - timedelta(weeks=AUTOMATIC_WEEKLY_WEEKS),
        "M": month_start_months_ago(today, AUTOMATIC_MONTHLY_PERIODS - 1),
    }
    daily_start = starts["D"]
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
        if frequency == "Q":
            run(code, lambda code=code, source_id=source_id, frequency=frequency: (
                lambda rows: _insert_missing(
                    db, rows,
                    date.fromisoformat(str(rows[0]["observation_date"])) if rows else today,
                )
            )(_fred_recent_rows(code, source_id, frequency, AUTOMATIC_DAILY_VALUES)))
            continue
        series_start = starts.get(frequency, daily_start)
        run(code, lambda code=code, source_id=source_id, frequency=frequency, series_start=series_start: _insert_missing(
            db, latest_automatic_rows(_fred_rows(code, source_id, frequency, series_start, today)), series_start
        ))

    try:
        treasury_rows = fetch_treasury_yield_rows(daily_start, today)
        for code in ("US2Y", "US10Y"):
            run(code, lambda code=code: _insert_missing(db, latest_automatic_rows(treasury_rows.get(code, [])), daily_start))
    except Exception as error:
        for code in ("US2Y", "US10Y"):
            inserted.setdefault(code, 0)
            errors[code] = f"{error.__class__.__name__}: {error}"

    try:
        treasury_real_rows = fetch_treasury_real_yield_rows(daily_start, today)
        run("US10Y_REAL", lambda: _insert_missing(db, latest_automatic_rows(treasury_real_rows.get("US10Y_REAL", [])), daily_start))
    except Exception as error:
        inserted.setdefault("US10Y_REAL", 0)
        errors["US10Y_REAL"] = f"{error.__class__.__name__}: {error}"

    run("WTI", lambda: _insert_missing(db, latest_automatic_rows(fetch_wti_futures_rows(daily_start, today)), daily_start))
    for code, (stat_code, item_code, frequency) in ECOS_SERIES.items():
        series_start = starts.get(frequency, daily_start)
        run(code, lambda code=code, stat_code=stat_code, item_code=item_code, frequency=frequency, series_start=series_start: _insert_missing(
            db, latest_automatic_rows(_ecos_rows(code, stat_code, item_code, frequency, series_start, today)), series_start
        ))

    run("KR_POLICY_RATE", lambda: collect_korea_policy_rate(today=today, db=db))
    run("REDBOOK", lambda: _insert_missing(db, latest_automatic_rows(fetch_recent_redbook_rows()), starts["W"]))
    run("US_RETAIL_SALES", lambda: _insert_missing(
        db,
        latest_automatic_rows(census_retail_chart_rows(fetch_census_retail_sales(
            starts["M"],
            today,
            api_key=require_env("CENSUS_API_KEY"),
        ))),
        starts["M"],
    ))

    export_start_month = (today - timedelta(days=65)).replace(day=1)
    try:
        snapshots, export_errors = fetch_snapshots(export_start_month, max_pages=6)
        if export_errors:
            errors["KR_EXPORT_SOURCE"] = " | ".join(export_errors[:5])
        inserted["KR_EXPORT_RAW"] = insert_missing_snapshots(db, snapshots, export_start_month)
    except Exception as error:
        inserted.setdefault("KR_EXPORT_RAW", 0)
        errors["KR_EXPORT_RAW"] = f"{error.__class__.__name__}: {error}"

    # KOSPI valuation is part of the same regular refresh, but unlike tolerant source adapters its
    # strict pykrx validation must fail the workflow when KRX returns malformed/missing data.
    inserted.update(collect_kospi_valuation(db=db))

    print(json.dumps({
        "mode": "automatic",
        "stage": "sources",
        "starts": {frequency: value.isoformat() for frequency, value in starts.items()},
        "end": today.isoformat(),
        "inserted": inserted,
        "errors": errors,
    }, ensure_ascii=False, sort_keys=True))
    return inserted, errors


def calculate_derived() -> tuple[dict[str, int], dict[str, str]]:
    """Calculate chart series using stored source data only; no provider calls are allowed here."""
    today = date.today()
    daily_start = today - timedelta(days=AUTOMATIC_DAILY_CALENDAR_DAYS)
    export_start_month = (today - timedelta(days=65)).replace(day=1)
    db = SupabaseRest()
    inserted: dict[str, int] = {}
    errors: dict[str, str] = {}

    def run(name: str, action) -> None:
        try:
            inserted[name] = int(action())
        except Exception as error:
            inserted.setdefault(name, 0)
            errors[name] = f"{error.__class__.__name__}: {error}"

    run("US10Y2Y", lambda: _derive_spread(
        db, "US10Y2Y", "US10Y", "US2Y", "D", daily_start, today,
        max_rows=AUTOMATIC_DAILY_VALUES,
    ))
    run("KR10Y3Y", lambda: _derive_spread(
        db, "KR10Y3Y", "KR10Y", "KR3Y", "D", daily_start, today,
        max_rows=AUTOMATIC_DAILY_VALUES,
    ))
    run("US_POLICY_RATE_MID", lambda: collect_us_policy_rate(today=today, db=db))
    run("KR_EXPORT_DAILY_AVG", lambda: derive_missing_segments(db, export_start_month))

    # Checking every supported code keeps alert state correct across the job boundary.
    # An unchanged value cannot retrigger a crossing alert.
    tracked_codes = set(FRED_SERIES) | set(ECOS_SERIES) | set(KRX_INDEX_FUNDAMENTALS) | {
        "US2Y", "US10Y", "US10Y_REAL", "US10Y2Y", "WTI", "KR_POLICY_RATE",
        "US_POLICY_RATE_MID", "KR10Y3Y", "REDBOOK", "US_RETAIL_SALES", "KR_EXPORT_DAILY_AVG",
        "NFCI_RISK", "KR_CORP_CREDIT_SPREAD",
    }
    alerts = check_collected_series_alerts(db, tracked_codes)
    print(json.dumps({"mode": "automatic", "stage": "derived", "inserted": inserted,
                      "errors": errors, "alerts": alerts}, ensure_ascii=False, sort_keys=True))
    return inserted, errors


def collect() -> tuple[dict[str, int], dict[str, str]]:
    """Compatibility entrypoint: source publication, then DB-only derivation."""
    source_inserted, source_errors = collect_sources()
    if source_errors:
        return source_inserted, source_errors
    derived_inserted, derived_errors = calculate_derived()
    return {**source_inserted, **derived_inserted}, derived_errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("sources", "derived", "all"), default="all")
    args = parser.parse_args()
    if args.stage == "sources":
        _inserted, errors = collect_sources()
    elif args.stage == "derived":
        _inserted, errors = calculate_derived()
    else:
        _inserted, errors = collect()
    if errors:
        raise RuntimeError("Economic chart collection partially failed: " + " | ".join(
            f"{series}: {message}" for series, message in sorted(errors.items())
        ))


if __name__ == "__main__":
    main()
