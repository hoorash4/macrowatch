"""Explicit one-off historical backfill for economic-chart source series.

This runner is never scheduled. It fills up to ten years where the provider allows it,
otherwise every observation the provider currently exposes, and never deletes unrelated history.
"""
from __future__ import annotations

import json
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
            print(json.dumps({"stage": "economic_chart_backfill_error", "series": name, "error": errors[name]}, ensure_ascii=False))

    # Treasury yields use the first-party Treasury XML feed for both history and live collection.
    # FRED is intentionally retained only for series without an equivalent first-party source.
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

    try:
        rows_by_code = fetch_krx_kospi_fundamental_rows(start, today)
        for code in KRX_INDEX_FUNDAMENTALS:
            run(code, lambda code=code: _insert_missing(db, rows_by_code.get(code, []), start))
    except Exception as error:
        for code in KRX_INDEX_FUNDAMENTALS:
            inserted.setdefault(code, 0)
            errors.setdefault(code, f"{error.__class__.__name__}: {error}")

    run("KR10Y3Y", lambda: _derive_spread(db, "KR10Y3Y", "KR10Y", "KR3Y", "D", start, today))
    run("REDBOOK", lambda: _insert_missing(db, fetch_recent_redbook_rows(), today - timedelta(days=35)))

    print(json.dumps({"mode": "backfill", "start": start.isoformat(), "end": today.isoformat(), "inserted": inserted, "errors": errors}, ensure_ascii=False, sort_keys=True))
    return inserted, errors


def main() -> None:
    backfill()


if __name__ == "__main__":
    main()
