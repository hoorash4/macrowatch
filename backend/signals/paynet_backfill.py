"""Authoritative reverse-month backfill for unified Equifax delinquency and SBDFI."""
from __future__ import annotations

from datetime import date
import json

from common import SupabaseRest
from sources.paynet_derived_history import fetch_paynet_derived_month
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


def _first_missing_month(db: SupabaseRest, required: list[date]) -> date | None:
    existing = db.request(
        "GET",
        TABLE,
        params={
            "select": "series_code,observation_date",
            "series_code": f"in.({SERIES_DELINQUENCY},{SERIES_DEFAULT})",
            "observation_date": f"gte.{required[-1].isoformat()}",
            "limit": "1000",
        },
    ) or []
    by_month: dict[str, set[str]] = {}
    for row in existing:
        key = str(row.get("observation_date") or "")
        code = str(row.get("series_code") or "")
        if key and code:
            by_month.setdefault(key, set()).add(code)

    needed = {SERIES_DELINQUENCY, SERIES_DEFAULT}
    for month in required:
        if by_month.get(month.isoformat(), set()) != needed:
            return month
    return None


def run() -> dict[str, object]:
    required = _required_months(EXPECTED_LATEST)
    db = SupabaseRest()

    db.request(
        "DELETE",
        TABLE,
        params={"series_code": "in.(US_SBDI_31_90,US_SBDI_91_180)"},
        prefer="return=minimal",
    )

    resume = _first_missing_month(db, required)
    if resume is None:
        return {
            "latest": EXPECTED_LATEST.isoformat(),
            "oldest": required[-1].isoformat(),
            "months": len(required),
            "rows": 0,
            "status": "already-complete",
        }

    start_index = required.index(resume)
    remaining = required[start_index:]
    print(json.dumps({"resume_from": f"{resume:%Y-%m}", "remaining_months": len(remaining)}, ensure_ascii=False))

    stored = 0
    oldest_stored: date | None = None
    for month in remaining:
        rows = fetch_paynet_month(month)
        if any(rows[code] is None for code in (SERIES_DELINQUENCY, SERIES_DEFAULT)):
            derived = fetch_paynet_derived_month(month)
            for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
                if rows[code] is None:
                    rows[code] = derived[code]

        missing = [code for code in (SERIES_DELINQUENCY, SERIES_DEFAULT) if rows[code] is None]
        if missing:
            raise RuntimeError(
                f"{month:%Y-%m}: Equifax source missing {','.join(missing)} after direct, YoY and MoM recovery; "
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
            "source": batch[0].get("source"),
        }, ensure_ascii=False))

    return {
        "latest": EXPECTED_LATEST.isoformat(),
        "oldest": oldest_stored.isoformat() if oldest_stored else None,
        "months": stored // 2,
        "rows": stored,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
