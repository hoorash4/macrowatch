"""Authoritative reverse-month backfill for unified Equifax delinquency and SBDFI."""
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
    """Integrity helper retained for tests and complete-history validation."""
    by_code: dict[str, dict[str, dict]] = {}
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        rows = rows_by_code.get(code) or []
        if not rows:
            raise RuntimeError(f"{code}: no Equifax source rows")
        by_code[code] = {row["observation_date"]: row for row in rows}
        latest = max(date.fromisoformat(key) for key in by_code[code])
        if latest != EXPECTED_LATEST:
            raise RuntimeError(f"{code}: expected latest {EXPECTED_LATEST}, got {latest}")

    required = _required_months(EXPECTED_LATEST)
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        absent = [month.isoformat() for month in required if month.isoformat() not in by_code[code]]
        if absent:
            raise RuntimeError(f"{code}: incomplete Equifax history; first missing={absent[0]}")
    return EXPECTED_LATEST, by_code


def run() -> dict[str, object]:
    required = _required_months(EXPECTED_LATEST)
    db = SupabaseRest()

    # Old split series are no longer part of the product. Remove them once up front;
    # the unified delinquency/default rows are then repaired newest -> oldest.
    db.request(
        "DELETE",
        TABLE,
        params={"series_code": "in.(US_SBDI_31_90,US_SBDI_91_180)"},
        prefer="return=minimal",
    )

    stored = 0
    oldest_stored: date | None = None
    for month in required:
        rows = fetch_paynet_month(month)
        missing = [code for code in (SERIES_DELINQUENCY, SERIES_DEFAULT) if rows[code] is None]
        if missing:
            raise RuntimeError(
                f"{month:%Y-%m}: Equifax source missing {','.join(missing)}; "
                "no partial month will be stored"
            )

        batch = []
        for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
            validated = _validate_rows(code, [rows[code]], month, month)
            if len(validated) != 1:
                raise RuntimeError(f"{month:%Y-%m}: {code} failed validation")
            batch.append(validated[0])

        db.upsert(TABLE, batch, conflict="series_code,observation_date")
        stored += 2
        oldest_stored = month
        print(json.dumps({
            "stored_month": f"{month:%Y-%m}",
            SERIES_DELINQUENCY: batch[0]["value"],
            SERIES_DEFAULT: batch[1]["value"],
        }, ensure_ascii=False))

    return {
        "latest": EXPECTED_LATEST.isoformat(),
        "oldest": oldest_stored.isoformat() if oldest_stored else None,
        "months": stored // 2,
        "rows": stored,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
