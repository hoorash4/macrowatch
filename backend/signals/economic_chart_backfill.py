"""Explicit one-off historical backfill for economic-chart source series.

This runner is never scheduled. It fills up to ten years where the provider allows it,
otherwise every observation the provider currently exposes, and never deletes unrelated history.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta

from common import SupabaseRest
from signals.economic_chart_pipeline import (
    ECOS_SERIES,
    FRED_SERIES,
    _derive_spread,
    _ecos_rows,
    _fred_rows,
    _insert_missing,
)
from sources.krx_index_fundamentals import SERIES as KRX_INDEX_FUNDAMENTALS, fetch_krx_kospi_fundamental_rows
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
    inserted = {code: 0 for code in KRX_INDEX_FUNDAMENTALS}
    errors: dict[str, str] = {}
    first = True
    for chunk_start, chunk_end in _chunks(start, end):
        if not first and pause_seconds > 0:
            time.sleep(pause_seconds)
        first = False
        key = f"KOSPI_PER_PBR:{chunk_start.isoformat()}:{chunk_end.isoformat()}"
        try:
            rows_by_code = fetch_krx_kospi_fundamental_rows(chunk_start, chunk_end)
            for code in KRX_INDEX_FUNDAMENTALS:
                inserted[code] += _insert_missing(db, rows_by_code[code], chunk_start)
            print(json.dumps({
                "stage": "kospi_valuation_backfill_chunk_done",
                "start": chunk_start.isoformat(),
                "end": chunk_end.isoformat(),
                "rows": {code: len(rows_by_code[code]) for code in KRX_INDEX_FUNDAMENTALS},
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


def backfill(*, only: str = "all") -> tuple[dict[str, int], dict[str, str]]:
    today = date.today()
    start = today - timedelta(days=3660)
    db = SupabaseRest()
    inserted: dict[str, int] = {}
    errors: dict[str, str] = {}

    kospi_inserted, kospi_errors = backfill_kospi_valuation(db, start, today)
    inserted.update(kospi_inserted)
    errors.update(kospi_errors)
    if only == "kospi-valuation":
        print(json.dumps({
            "mode": "backfill",
            "target": only,
            "start": start.isoformat(),
            "end": today.isoformat(),
            "inserted": inserted,
            "errors": errors,
        }, ensure_ascii=False, sort_keys=True))
        return inserted, errors

    def run(name: str, action) -> None:
        try:
            inserted[name] = int(action())
        except Exception as error:
            inserted.setdefault(name, 0)
            errors[name] = f"{error.__class__.__name__}: {error}"
            print(json.dumps({"stage": "economic_chart_backfill_error", "series": name, "error": errors[name]}, ensure_ascii=False))

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

    print(json.dumps({
        "mode": "backfill",
        "target": only,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "inserted": inserted,
        "errors": errors,
    }, ensure_ascii=False, sort_keys=True))
    return inserted, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=("all", "kospi-valuation"), default="all")
    args = parser.parse_args()
    _, errors = backfill(only=args.only)
    if args.only == "kospi-valuation" and errors:
        raise RuntimeError("KOSPI PER/PBR 백필 실패 구간이 있습니다: " + "; ".join(sorted(errors)))


if __name__ == "__main__":
    main()
