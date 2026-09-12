"""Authoritative reverse-month backfill for PayNet 31-180 delinquency and SBDFI."""
from __future__ import annotations

from datetime import date
import json

from common import SupabaseRest
from sources.paynet_loan_performance import (
    SERIES_DEFAULT,
    SERIES_DELINQUENCY,
    fetch_paynet_rows,
)
from signals.business_credit_backfill import _validate_rows

TABLE = "economic_chart_points"
EXPECTED_MIN_LATEST = date(2026, 7, 1)


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
    latest_dates = []
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        rows = rows_by_code.get(code) or []
        if not rows:
            raise RuntimeError(f"{code}: PayNet returned no direct source rows")
        parsed = {row["observation_date"]: row for row in rows}
        by_code[code] = parsed
        latest_dates.append(max(date.fromisoformat(key) for key in parsed))

    latest = min(latest_dates)
    if latest < EXPECTED_MIN_LATEST:
        raise RuntimeError(f"PayNet latest month is unexpectedly stale: {latest}")

    required = _required_months(latest)
    missing: dict[str, list[str]] = {}
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        absent = [month.isoformat() for month in required if month.isoformat() not in by_code[code]]
        if absent:
            missing[code] = absent
    if missing:
        raise RuntimeError(f"PayNet ten-year history is incomplete; refusing partial replacement: {missing}")
    return latest, by_code


def run() -> dict[str, object]:
    today = date.today()
    provisional_start = date(today.year - 11, today.month, 1)
    direct = fetch_paynet_rows(provisional_start, today)
    latest, by_code = _prepare(direct)
    required = _required_months(latest)
    start = required[-1]

    # Validate all rows before changing the database. Replacement is all-or-nothing in intent:
    # if source discovery is incomplete, nothing is deleted.
    validated: dict[str, dict[str, dict]] = {}
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        ordered = [by_code[code][month.isoformat()] for month in required]
        clean = _validate_rows(code, ordered, start, latest)
        validated[code] = {row["observation_date"]: row for row in clean}

    db = SupabaseRest()
    # The two previous series are not authoritative and must never coexist with the new source.
    db.delete(TABLE, filters={"series_code": f"in.({SERIES_DELINQUENCY},{SERIES_DEFAULT})"})

    stored = 0
    # Explicitly write latest -> oldest one month at a time, as requested.
    for month in required:
        key = month.isoformat()
        batch = [validated[SERIES_DELINQUENCY][key], validated[SERIES_DEFAULT][key]]
        db.upsert(TABLE, batch, conflict="series_code,observation_date")
        stored += len(batch)
        print(json.dumps({
            "month": key[:7],
            SERIES_DELINQUENCY: batch[0]["value"],
            SERIES_DEFAULT: batch[1]["value"],
        }, ensure_ascii=False))

    return {
        "latest": latest.isoformat(),
        "oldest": start.isoformat(),
        "months": len(required),
        "rows": stored,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
