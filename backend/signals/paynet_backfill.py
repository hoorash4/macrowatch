"""Authoritative reverse-month backfill for direct 31-180 delinquency and SBDFI."""
from __future__ import annotations

from datetime import date
import json

from common import SupabaseRest
from sources.paynet_loan_performance import (
    SERIES_DEFAULT,
    SERIES_DELINQUENCY,
    fetch_paynet_month,
)
from signals.business_credit_backfill import _validate_rows

TABLE = "economic_chart_points"
EXPECTED_LATEST = date(2026, 7, 1)


def _shift_month(d: date, months: int) -> date:
    serial = d.year * 12 + d.month - 1 + months
    return date(serial // 12, serial % 12 + 1, 1)


def _required_months(latest: date, years: int = 10) -> list[date]:
    start = date(latest.year - years, latest.month, 1)
    months: list[date] = []
    current = latest
    while current >= start:
        months.append(current)
        current = _shift_month(current, -1)
    return months


def _prepare(rows_by_code: dict[str, list[dict]]) -> tuple[date, dict[str, dict[str, dict]]]:
    by_code: dict[str, dict[str, dict]] = {}
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        rows = rows_by_code.get(code) or []
        if not rows:
            raise RuntimeError(f"{code}: no direct public source rows")
        by_code[code] = {row["observation_date"]: row for row in rows}
        latest = max(date.fromisoformat(key) for key in by_code[code])
        if latest != EXPECTED_LATEST:
            raise RuntimeError(f"{code}: expected latest {EXPECTED_LATEST}, got {latest}")

    required = _required_months(EXPECTED_LATEST)
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        absent = [month.isoformat() for month in required if month.isoformat() not in by_code[code]]
        if absent:
            raise RuntimeError(f"{code}: incomplete direct history; first missing={absent[0]}")
    return EXPECTED_LATEST, by_code


def run() -> dict[str, object]:
    required = _required_months(EXPECTED_LATEST)
    collected = {SERIES_DELINQUENCY: [], SERIES_DEFAULT: []}

    # Verify July 2026 first, then walk backwards exactly one month at a time.
    # No database changes happen until every one of the 121 months is present.
    for month in required:
        rows = fetch_paynet_month(month)
        missing = [code for code in (SERIES_DELINQUENCY, SERIES_DEFAULT) if rows[code] is None]
        if missing:
            raise RuntimeError(
                f"{month:%Y-%m}: direct public source missing {','.join(missing)}; "
                "split delinquency buckets will not be substituted"
            )
        for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
            collected[code].append(rows[code])
        print(json.dumps({"verified_month": f"{month:%Y-%m}"}, ensure_ascii=False))

    latest, by_code = _prepare(collected)
    start = required[-1]
    validated: dict[str, dict[str, dict]] = {}
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        ordered = [by_code[code][month.isoformat()] for month in required]
        clean = _validate_rows(code, ordered, start, latest)
        validated[code] = {row["observation_date"]: row for row in clean}

    db = SupabaseRest()
    db.request(
        "DELETE",
        TABLE,
        params={
            "series_code": (
                "in.(US_SBDI_31_90,US_SBDI_91_180,"
                f"{SERIES_DELINQUENCY},{SERIES_DEFAULT})"
            )
        },
        prefer="return=minimal",
    )

    stored = 0
    for month in required:
        key = month.isoformat()
        batch = [validated[SERIES_DELINQUENCY][key], validated[SERIES_DEFAULT][key]]
        db.upsert(TABLE, batch, conflict="series_code,observation_date")
        stored += 2

    return {
        "latest": latest.isoformat(),
        "oldest": start.isoformat(),
        "months": len(required),
        "rows": stored,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
