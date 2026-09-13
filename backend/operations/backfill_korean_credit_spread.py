"""One-time 2000+ replacement for Korean corporate credit spread inputs and result.

This entrypoint is deleted after its verified production run. Scheduled collectors do not
import it. ECOS observations remain canonical source rows; only their date intersection is
written to the derived-series table as AA- corporate 3Y minus Treasury 3Y.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from common import SupabaseRest, require_env
from signals.canonical_series import rows as canonical_rows
from signals.canonical_series import store as store_canonical
from signals.derived_series import rows as derived_rows
from signals.derived_series import store as store_derived
from signals.korea_stress_pipeline import daily_source_rows, parsed_daily


START = date(2000, 1, 1)
SOURCE_TABLE = "economic_chart_points"
DERIVED_TABLE = "economic_chart_derived_points"
SERIES = {
    "KR_AA_YIELD": ("010300000", "korea_stress"),
    "KR3Y": ("010200000", "economic_chart"),
}
STAT_CODE = "817Y002"


def existing_dates(database: SupabaseRest, table: str, series_code: str,
                   start: date, end: date) -> set[str]:
    result: set[str] = set()
    offset = 0
    while True:
        page = database.request("GET", table, params={
            "select": "observation_date",
            "series_code": f"eq.{series_code}",
            "observation_date": f"gte.{start.isoformat()}",
            "and": f"(observation_date.lte.{end.isoformat()})",
            "order": "observation_date.asc",
            "offset": str(offset),
            "limit": "1000",
        }) or []
        result.update(str(row["observation_date"]) for row in page)
        if len(page) < 1000:
            return result
        offset += len(page)


def delete_dates(database: SupabaseRest, table: str, series_code: str,
                 dates: set[str]) -> None:
    ordered = sorted(dates)
    for offset in range(0, len(ordered), 100):
        batch = ordered[offset:offset + 100]
        database.request(
            "DELETE", table,
            params={
                "series_code": f"eq.{series_code}",
                "observation_date": f"in.({','.join(batch)})",
            },
            prefer="return=minimal",
        )


def validate(values: dict[date, float], series_code: str, end: date) -> None:
    if len(values) < 5000:
        raise RuntimeError(f"{series_code}: too few authoritative rows ({len(values)})")
    if min(values) > date(2000, 1, 10):
        raise RuntimeError(f"{series_code}: history does not reach January 2000")
    if max(values) < end - timedelta(days=14):
        raise RuntimeError(f"{series_code}: latest observation is unexpectedly stale ({max(values)})")
    invalid = [value for value in values.values() if not math.isfinite(value) or value <= 0]
    if invalid:
        raise RuntimeError(f"{series_code}: invalid source values ({len(invalid)})")


def replace_canonical(database: SupabaseRest, series_code: str, values: dict[date, float],
                      owner: str, end: date) -> None:
    payload = canonical_rows(
        series_code, values, frequency="D",
        source=f"ECOS:{STAT_CODE}/{SERIES[series_code][0]}",
    )
    authoritative = {str(row["observation_date"]) for row in payload}
    previous = existing_dates(database, SOURCE_TABLE, series_code, START, end)
    store_canonical(database, payload, owner=owner)
    delete_dates(database, SOURCE_TABLE, series_code, previous - authoritative)
    print(
        f"source={series_code} rows={len(payload)} first={min(authoritative)} "
        f"last={max(authoritative)} stale_removed={len(previous - authoritative)}"
    )


def replace_derived(database: SupabaseRest, values: dict[date, float], end: date) -> None:
    series_code = "KR_CORP_CREDIT_SPREAD"
    payload = derived_rows(
        series_code, values, frequency="D", source="DERIVED:KR_AA_YIELD-KR3Y",
    )
    authoritative = {str(row["observation_date"]) for row in payload}
    previous = existing_dates(database, DERIVED_TABLE, series_code, START, end)
    store_derived(database, payload)
    delete_dates(database, DERIVED_TABLE, series_code, previous - authoritative)
    print(
        f"derived={series_code} rows={len(payload)} first={min(authoritative)} "
        f"last={max(authoritative)} stale_removed={len(previous - authoritative)}"
    )


def main() -> None:
    end = date.today()
    ecos_key = require_env("ECOS_API_KEY")
    database = SupabaseRest(
        url=require_env("SUPABASE_URL"),
        service_key=require_env("SUPABASE_SERVICE_ROLE_KEY"),
        timeout=45,
    )
    values: dict[str, dict[date, float]] = {}
    for series_code, (item_code, _owner) in SERIES.items():
        fetched = parsed_daily(daily_source_rows(ecos_key, STAT_CODE, item_code, START, end))
        validate(fetched, series_code, end)
        values[series_code] = fetched

    common_dates = values["KR_AA_YIELD"].keys() & values["KR3Y"].keys()
    spread = {
        observed: values["KR_AA_YIELD"][observed] - values["KR3Y"][observed]
        for observed in common_dates
    }
    if len(spread) < 5000 or min(spread) > date(2000, 1, 10):
        raise RuntimeError("derived spread history failed coverage validation")

    # All external reads and validations finish before the first database mutation.
    for series_code, (_item_code, owner) in SERIES.items():
        replace_canonical(database, series_code, values[series_code], owner, end)
    replace_derived(database, spread, end)


if __name__ == "__main__":
    main()
