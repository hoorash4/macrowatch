"""One-time/resumable backfill for monthly business-credit economic-chart series."""
from __future__ import annotations

from datetime import date
import json
import math
import os

from common import SupabaseRest
from sources.business_credit_monthly import (
    fetch_equifax_rows,
    fetch_korea_business_delinquency_rows,
    fetch_korea_default_company_rows,
)
from sources.court_rehabilitation import fetch_korea_corporate_rehab_rows
from sources.epiq_ch11_source import fetch_epiq_ch11_rows
from sources.equifax_archive_source import fetch_equifax_archive_rows

TABLE = "economic_chart_points"


def _ten_year_start(today: date) -> date:
    return date(today.year - 10, today.month, 1)


def _validate_rows(code: str, rows: list[dict], start: date, end: date) -> list[dict]:
    """Validate and deduplicate source rows before an authoritative backfill upsert.

    Backfill must never preserve a stale value merely because the date already exists.
    At the same time, sparse external archives (notably historical Equifax reports) must
    not cause valid dates to be deleted when the public archive itself is incomplete.
    """
    first = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    validated_by_date: dict[str, dict] = {}
    seen: dict[str, float] = {}
    for raw in rows:
        if raw.get("series_code") != code:
            raise RuntimeError(f"{code}: unexpected series_code {raw.get('series_code')!r}")
        observed_s = str(raw.get("observation_date") or "")
        try:
            observed = date.fromisoformat(observed_s)
        except ValueError as error:
            raise RuntimeError(f"{code}: invalid observation_date {observed_s!r}") from error
        if observed.day != 1 or not (first <= observed <= last):
            raise RuntimeError(f"{code}: out-of-range/non-month-start observation {observed_s}")
        if raw.get("frequency") != "M":
            raise RuntimeError(f"{code}: non-monthly frequency for {observed_s}")
        try:
            value = float(raw.get("value"))
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"{code}: non-numeric value for {observed_s}") from error
        if not math.isfinite(value) or value < 0:
            raise RuntimeError(f"{code}: invalid value {value!r} for {observed_s}")
        if not str(raw.get("source") or "").strip():
            raise RuntimeError(f"{code}: missing source for {observed_s}")
        previous = seen.get(observed_s)
        if previous is not None and not math.isclose(previous, value, rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError(f"{code}: conflicting source values for {observed_s}: {previous} vs {value}")
        seen[observed_s] = value
        # Equal overlapping first-party discoveries are the same observation. Keep one row
        # so Postgres ON CONFLICT never sees the same key twice in a single statement.
        validated_by_date.setdefault(observed_s, raw)
    return [validated_by_date[key] for key in sorted(validated_by_date)]


def _store(db: SupabaseRest, code: str, rows: list[dict], start: date, end: date, totals: dict[str, int]) -> None:
    validated = _validate_rows(code, rows, start, end)
    if validated:
        db.upsert(TABLE, validated, conflict="series_code,observation_date")
    totals[code] = len(validated)
    print(json.dumps({
        "series": code,
        "fetched": len(rows),
        "upserted": len(validated),
        "first": validated[0]["observation_date"] if validated else None,
        "last": validated[-1]["observation_date"] if validated else None,
    }, ensure_ascii=False))


def run() -> dict[str, int]:
    end = date.today()
    start = _ten_year_start(end)
    db = SupabaseRest()
    totals: dict[str, int] = {}

    # Search both current and legacy first-party Equifax asset naming schemes. Overlap is
    # deliberate: validation rejects any conflicting values for the same observation month.
    equifax = fetch_equifax_rows(start, end)
    equifax_archive = fetch_equifax_archive_rows(start, end)
    for code, rows in equifax.items():
        _store(db, code, rows + equifax_archive.get(code, []), start, end, totals)

    _store(
        db,
        "US_COMMERCIAL_CH11",
        fetch_epiq_ch11_rows(start, end, max_pages=int(os.getenv("EPIQ_BACKFILL_PAGES", "20"))),
        start,
        end,
        totals,
    )
    _store(db, "KR_CORP_DELINQ", fetch_korea_business_delinquency_rows(start, end), start, end, totals)
    _store(db, "KR_DEFAULT_COMPANIES", fetch_korea_default_company_rows(start, end), start, end, totals)
    _store(db, "KR_CORP_REHAB", fetch_korea_corporate_rehab_rows(start, end), start, end, totals)
    return totals


if __name__ == "__main__":
    print(json.dumps({"upserted": run()}, ensure_ascii=False, sort_keys=True))
