"""Explicit one-off historical backfill for economic-chart source series.

This runner is never scheduled. It fills up to ten years where the provider allows it,
otherwise every observation the provider currently exposes, and never deletes unrelated history.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta

from common import SupabaseRest, require_env
from signals.economic_chart_pipeline import (
    ECOS_SERIES,
    FRED_SERIES,
    _derive_spread,
    _ecos_rows,
    _fred_rows,
    _insert_missing,
)
from sources.census_retail_sales import chart_rows as census_retail_chart_rows
from sources.census_retail_sales import fetch_census_retail_sales
from sources.redbook import fetch_recent_redbook_rows
from sources.us_treasury_yields import fetch_treasury_real_yield_rows, fetch_treasury_yield_rows
from sources.wti_futures import fetch_wti_futures_rows

TREASURY_ECONOMIC_SERIES = {"US2Y", "US10Y", "US10Y2Y", "US10Y_REAL"}
KOSPI_BACKFILL_CHUNK_DAYS = 90
KOSPI_BACKFILL_PAUSE_SECONDS = 1.0


def _chunks(start: date, end: date, days: int = KOSPI_BACKFILL_CHUNK_DAYS):
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def backfill_kospi_valuation(
    db: SupabaseRest,
    start: date,
    end: date,
    *,
    pause_seconds: float = KOSPI_BACKFILL_PAUSE_SECONDS,
) -> tuple[dict[str, int], dict[str, str]]:
    """Persist each successful pykrx chunk immediately and continue after failed chunks."""
    # KRX is intentionally imported only inside the KOSPI-specific operation. A Census-only
    # backfill must not initialize/login to an unrelated provider merely by importing this runner.
    from sources.krx_index_fundamentals import SERIES as krx_series
    from sources.krx_index_fundamentals import fetch_krx_kospi_fundamental_rows

    inserted = {code: 0 for code in krx_series}
    errors: dict[str, str] = {}
    first = True
    for chunk_start, chunk_end in _chunks(start, end):
        if not first and pause_seconds > 0:
            time.sleep(pause_seconds)
        first = False
        key = f"KOSPI_PER_PBR:{chunk_start.isoformat()}:{chunk_end.isoformat()}"
        try:
            rows_by_code = fetch_krx_kospi_fundamental_rows(chunk_start, chunk_end)
            for code in krx_series:
                inserted[code] += _insert_missing(db, rows_by_code[code], chunk_start)
            print(json.dumps({
                "stage": "kospi_valuation_backfill_chunk_done",
                "start": chunk_start.isoformat(),
                "end": chunk_end.isoformat(),
                "rows": {code: len(rows_by_code[code]) for code in krx_series},
            }, ensure_ascii=False))
        except Exception as error:
            errors[key] = f"{error.__class__.__name__}: {error}"
            print(json.dumps({
                "stage": "kospi_valuation_backfill_chunk_error",
                "start": chunk_start.isoformat(),
                "end": chunk_end.isoformat(),
                "error": errors[key],
            }, ensure_ascii=False))
    return inserted, errors


def _summary(only: str, start: date, today: date, inserted: dict[str, int], errors: dict[str, str]) -> None:
    print(json.dumps({
        "mode": "backfill",
        "target": only,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "inserted": inserted,
        "errors": errors,
    }, ensure_ascii=False, sort_keys=True))


def backfill(*, only: str = "all") -> tuple[dict[str, int], dict[str, str]]:
    today = date.today()
    start = today - timedelta(days=3660)
    db = SupabaseRest()
    inserted: dict[str, int] = {}
    errors: dict[str, str] = {}

    def run(name: str, action) -> None:
        try:
            inserted[name] = int(action())
        except Exception as error:
            inserted.setdefault(name, 0)
            errors[name] = f"{error.__class__.__name__}: {error}"
            print(json.dumps({"stage": "economic_chart_backfill_error", "series": name, "error": errors[name]}, ensure_ascii=False))

    # Explicit targets are physically bounded: selecting Census retail sales cannot invoke KOSPI
    # valuation or any other historical source. "all" is the only multi-source backfill mode.
    if only in {"all", "kospi-valuation"}:
        kospi_inserted, kospi_errors = backfill_kospi_valuation(db, start, today)
        inserted.update(kospi_inserted)
        errors.update(kospi_errors)
        if only == "kospi-valuation":
            _summary(only, start, today, inserted, errors)
            return inserted, errors

    if only in {"all", "census-retail"}:
        run("US_RETAIL_SALES", lambda: _insert_missing(
            db,
            census_retail_chart_rows(fetch_census_retail_sales(
                start,
                today,
                api_key=require_env("CENSUS_API_KEY"),
            )),
            start,
        ))
        if only == "census-retail":
            _summary(only, start, today, inserted, errors)
            return inserted, errors

    for code, (source_id, frequency) in FRED_SERIES.items():
        if code == "WTI" or code in TREASURY_ECONOMIC_SERIES:
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
            errors.setdefault(code, f"{error.__class__.__name__}: {error}")

    try:
        treasury_real_rows = fetch_treasury_real_yield_rows(start, today)
        run("US10Y_REAL", lambda: _insert_missing(db, treasury_real_rows.get("US10Y_REAL", []), start))
    except Exception as error:
        inserted.setdefault("US10Y_REAL", 0)
        errors.setdefault("US10Y_REAL", f"{error.__class__.__name__}: {error}")

    run("US10Y2Y", lambda: _derive_spread(db, "US10Y2Y", "US10Y", "US2Y", "D", start, today))
    run("WTI", lambda: _insert_missing(db, fetch_wti_futures_rows(start, today), start))

    for code, (stat_code, item_code, frequency) in ECOS_SERIES.items():
        run(code, lambda code=code, stat_code=stat_code, item_code=item_code, frequency=frequency: _insert_missing(
            db, _ecos_rows(code, stat_code, item_code, frequency, start, today), start
        ))

    run("KR10Y3Y", lambda: _derive_spread(db, "KR10Y3Y", "KR10Y", "KR3Y", "D", start, today))
    run("REDBOOK", lambda: _insert_missing(db, fetch_recent_redbook_rows(), today - timedelta(days=35)))

    _summary(only, start, today, inserted, errors)
    return inserted, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=("all", "kospi-valuation", "census-retail"), default="all")
    args = parser.parse_args()
    _, errors = backfill(only=args.only)
    if args.only in {"kospi-valuation", "census-retail"} and errors:
        raise RuntimeError(f"{args.only} 백필 실패: " + "; ".join(sorted(errors)))


if __name__ == "__main__":
    main()
