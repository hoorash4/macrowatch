"""Incremental automatic collection for economic charts.

Historical bootstrap/backfill is deliberately not exposed here.  Exact observations that
already live in other MacroWatch tables (US2Y, US10Y, USDKRW, RRP, TGA) are read by the
frontend through fallbacks and are not duplicated by this collector.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from common import SupabaseRest
from signals.economic_chart_backfill import BACKFILL_FRED_SERIES, _redbook_rows
from signals.economic_chart_pipeline import (
    ECOS_SERIES,
    KRX_INDEX_FUNDAMENTALS,
    _derive_spread,
    _ecos_rows,
    _fred_rows,
    _insert_missing,
    _krx_index_rows,
    check_collected_series_alerts,
)


def collect() -> tuple[dict[str, int], dict[str, str]]:
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
            print(json.dumps({"stage": "economic_chart_automatic_error", "series": name, "error": errors[name]}, ensure_ascii=False))

    for code, (source_id, frequency) in BACKFILL_FRED_SERIES.items():
        run(code, lambda code=code, source_id=source_id, frequency=frequency: _insert_missing(
            db, _fred_rows(code, source_id, frequency, start, today), start
        ))

    for code, (stat_code, item_code, frequency) in ECOS_SERIES.items():
        run(code, lambda code=code, stat_code=stat_code, item_code=item_code, frequency=frequency: _insert_missing(
            db, _ecos_rows(code, stat_code, item_code, frequency, start, today), start
        ))

    # KRX is isolated because the public Data Marketplace endpoint may reject
    # automated callers independently of every other source.
    try:
        rows_by_code = _krx_index_rows(start, today)
        for code in KRX_INDEX_FUNDAMENTALS:
            run(code, lambda code=code: _insert_missing(db, rows_by_code.get(code, []), start))
    except Exception as error:
        for code in KRX_INDEX_FUNDAMENTALS:
            inserted.setdefault(code, 0)
            errors[code] = f"{error.__class__.__name__}: {error}"

    run("KR10Y3Y", lambda: _derive_spread(db, "KR10Y3Y", "KR10Y", "KR3Y", "D", start, today))
    run("REDBOOK", lambda: _insert_missing(db, _redbook_rows(), start))

    changed = {code for code, count in inserted.items() if count > 0}
    alerts = check_collected_series_alerts(db, changed)
    print(json.dumps({
        "mode": "automatic",
        "start": start.isoformat(),
        "end": today.isoformat(),
        "inserted": inserted,
        "errors": errors,
        "alerts": alerts,
    }, ensure_ascii=False, sort_keys=True))
    return inserted, errors


def main() -> None:
    collect()


if __name__ == "__main__":
    main()
