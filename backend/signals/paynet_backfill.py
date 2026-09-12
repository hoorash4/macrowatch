"""Authoritative reverse-month backfill for unified Equifax delinquency and SBDFI."""
from __future__ import annotations

from datetime import date
import json

from common import SupabaseRest
from sources.paynet_derived_history import fetch_paynet_derived_month
from sources.paynet_loan_performance import SERIES_DEFAULT, SERIES_DELINQUENCY, fetch_paynet_month
from signals.business_credit_backfill import _validate_rows

TABLE = "economic_chart_points"
EXPECTED_LATEST = date(2026, 7, 1)
LEGACY_DERIVE_FIRST = date(2020, 4, 1)


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


def _first_missing_month(db: SupabaseRest, required: list[date]) -> date | None:
    existing = db.request(
        "GET", TABLE,
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


def _recover_month(month: date) -> dict[str, dict | None]:
    # Historical reconstruction policy: once we reach Apr-2020 and earlier,
    # stop wasting time hunting for the target month's direct PDF. Reconstruct
    # strictly from official Equifax YoY first, then MoM, using a later report's
    # published level + change. This intentionally favors complete trend history.
    if month <= LEGACY_DERIVE_FIRST:
        return fetch_paynet_derived_month(month)

    rows = fetch_paynet_month(month)
    if any(rows[code] is None for code in (SERIES_DELINQUENCY, SERIES_DEFAULT)):
        derived = fetch_paynet_derived_month(month)
        for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
            if rows[code] is None:
                rows[code] = derived[code]
    return rows


def run() -> dict[str, object]:
    required = _required_months(EXPECTED_LATEST)
    db = SupabaseRest()
    db.request(
        "DELETE", TABLE,
        params={"series_code": "in.(US_SBDI_31_90,US_SBDI_91_180)"},
        prefer="return=minimal",
    )

    resume = _first_missing_month(db, required)
    if resume is None:
        return {"latest": EXPECTED_LATEST.isoformat(), "oldest": required[-1].isoformat(), "months": len(required), "rows": 0, "status": "already-complete"}

    remaining = required[required.index(resume):]
    print(json.dumps({"resume_from": f"{resume:%Y-%m}", "remaining_months": len(remaining)}, ensure_ascii=False))

    stored = 0
    oldest_stored: date | None = None
    unresolved: list[str] = []
    for month in remaining:
        rows = _recover_month(month)
        missing = [code for code in (SERIES_DELINQUENCY, SERIES_DEFAULT) if rows[code] is None]
        if missing:
            unresolved.append(f"{month:%Y-%m}")
            print(json.dumps({"unresolved_month": f"{month:%Y-%m}", "missing": missing}, ensure_ascii=False))
            continue

        batch = []
        valid = True
        for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
            validated = _validate_rows(code, [rows[code]], month, month)
            if len(validated) != 1:
                valid = False
                break
            batch.append(validated[0])
        if not valid:
            unresolved.append(f"{month:%Y-%m}")
            print(json.dumps({"unresolved_month": f"{month:%Y-%m}", "reason": "validation"}, ensure_ascii=False))
            continue

        db.upsert(TABLE, batch, conflict="series_code,observation_date")
        stored += 2
        oldest_stored = month
        print(json.dumps({"stored_month": f"{month:%Y-%m}", SERIES_DELINQUENCY: batch[0]["value"], SERIES_DEFAULT: batch[1]["value"], "source": batch[0].get("source")}, ensure_ascii=False))

    return {
        "latest": EXPECTED_LATEST.isoformat(),
        "oldest_stored": oldest_stored.isoformat() if oldest_stored else None,
        "months_stored": stored // 2,
        "rows": stored,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
