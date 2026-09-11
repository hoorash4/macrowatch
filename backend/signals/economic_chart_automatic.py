"""Incremental automatic collection for economic charts.

Only a short recent source window is inspected. Historical backfill is a separate explicit
entrypoint and is never imported or invoked from this scheduled collector.
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
    check_collected_series_alerts,
)
from signals.korea_export_chart import derive_missing_segments, insert_missing_snapshots
from sources.korea_export_intramonth import fetch_snapshots
from sources.redbook import fetch_recent_redbook_rows
from sources.wti_futures import fetch_wti_futures_rows


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
            print(json.dumps({
                "stage": "economic_chart_automatic_error",
                "series": name,
                "error": errors[name],
            }, ensure_ascii=False))

    # WTI uses the provider's continuous front-month CL=F series directly. MacroWatch
    # never selects, stitches or back-adjusts individual contract months itself.
    for code, (source_id, frequency) in FRED_SERIES.items():
        if code == "WTI":
            continue
        run(code, lambda code=code, source_id=source_id, frequency=frequency: _insert_missing(
            db, _fred_rows(code, source_id, frequency, start, today), start
        ))
    run("WTI", lambda: _insert_missing(db, fetch_wti_futures_rows(start, today), start))

    for code, (stat_code, item_code, frequency) in ECOS_SERIES.items():
        run(code, lambda code=code, stat_code=stat_code, item_code=item_code, frequency=frequency: _insert_missing(
            db, _ecos_rows(code, stat_code, item_code, frequency, start, today), start
        ))

    try:
        rows_by_code = _krx_index_rows(start, today)
        for code in KRX_INDEX_FUNDAMENTALS:
            run(code, lambda code=code: _insert_missing(db, rows_by_code.get(code, []), start))
    except Exception as error:
        for code in KRX_INDEX_FUNDAMENTALS:
            inserted.setdefault(code, 0)
            errors[code] = f"{error.__class__.__name__}: {error}"

    run("KR10Y3Y", lambda: _derive_spread(db, "KR10Y3Y", "KR10Y", "KR3Y", "D", start, today))
    run("REDBOOK", lambda: _insert_missing(db, fetch_recent_redbook_rows(), start))

    export_start_month = (today - timedelta(days=65)).replace(day=1)
    try:
        snapshots, export_errors = fetch_snapshots(export_start_month, max_pages=6)
        if export_errors:
            errors["KR_EXPORT_SOURCE"] = " | ".join(export_errors[:5])
        raw_count = insert_missing_snapshots(db, snapshots, export_start_month)
        segment_count = derive_missing_segments(db, export_start_month)
        inserted["KR_EXPORT_RAW"] = raw_count
        inserted["KR_EXPORT_DAILY_AVG"] = segment_count
    except Exception as error:
        inserted.setdefault("KR_EXPORT_DAILY_AVG", 0)
        errors["KR_EXPORT_DAILY_AVG"] = f"{error.__class__.__name__}: {error}"

    changed = {code for code, count in inserted.items()
               if count > 0 and code not in {"KR_EXPORT_RAW"}}
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
