"""Explicit one-off historical backfill for economic-chart source series.

This runner is never scheduled. It fills up to ten years where the provider allows it,
otherwise every observation the provider currently exposes, and never deletes history.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from common import SupabaseRest
from signals.economic_chart_pipeline import (
    ECOS_SERIES,
    FRED_SERIES,
    KRX_INDEX_FUNDAMENTALS,
    _derive_spread,
    _ecos_rows,
    _fred_rows,
    _insert_missing,
    _krx_index_rows,
)
from sources.eia_wti_futures import fetch_wti_futures_rows
from sources.redbook import fetch_recent_redbook_rows


def backfill() -> tuple[dict[str, int], dict[str, str]]:
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
            print(json.dumps({
                "stage": "economic_chart_backfill_error",
                "series": name,
                "error": errors[name],
            }, ensure_ascii=False))

    # Include US2Y/US10Y themselves in the chart read model. A chart must read the
    # actual ten-year daily backfill rather than a shorter legacy table fallback.
    # WTI is intentionally excluded here: it is replaced below with EIA's official
    # continuously rolled front-month futures series, not the old FRED spot series.
    for code, (source_id, frequency) in FRED_SERIES.items():
        if code == "WTI":
            continue
        run(code, lambda code=code, source_id=source_id, frequency=frequency: _insert_missing(
            db, _fred_rows(code, source_id, frequency, start, today), start
        ))

    def replace_wti_with_front_month_futures() -> int:
        rows = fetch_wti_futures_rows(start, today)
        if rows:
            db.upsert("economic_chart_points", rows, conflict="series_code,observation_date")
        return len(rows)

    run("WTI", replace_wti_with_front_month_futures)

    for code, (stat_code, item_code, frequency) in ECOS_SERIES.items():
        run(code, lambda code=code, stat_code=stat_code, item_code=item_code, frequency=frequency: _insert_missing(
            db, _ecos_rows(code, stat_code, item_code, frequency, start, today), start
        ))

    # KRX Data Marketplace internally chunks at <=730 days. If KRX exposes less than
    # ten years, preserve the maximum range it returns; KRX failure cannot discard
    # successfully backfilled FRED/ECOS series.
    try:
        rows_by_code = _krx_index_rows(start, today)
        for code in KRX_INDEX_FUNDAMENTALS:
            run(code, lambda code=code: _insert_missing(db, rows_by_code.get(code, []), start))
    except Exception as error:
        for code in KRX_INDEX_FUNDAMENTALS:
            inserted.setdefault(code, 0)
            errors.setdefault(code, f"{error.__class__.__name__}: {error}")

    run("KR10Y3Y", lambda: _derive_spread(db, "KR10Y3Y", "KR10Y", "KR3Y", "D", start, today))

    # Redbook has no free long-history source. Seed only the recent publicly available
    # observations; the scheduled collector then accumulates one weekly print at a time.
    run("REDBOOK", lambda: _insert_missing(db, fetch_recent_redbook_rows(), today - timedelta(days=35)))

    print(json.dumps({
        "mode": "backfill",
        "start": start.isoformat(),
        "end": today.isoformat(),
        "inserted": inserted,
        "errors": errors,
    }, ensure_ascii=False, sort_keys=True))
    return inserted, errors


def main() -> None:
    backfill()


if __name__ == "__main__":
    main()
