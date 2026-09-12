"""Private source cache for calculations whose formulas require long history."""

from __future__ import annotations

from datetime import date, datetime, timezone

try:
    from ..common import SupabaseRest
except ImportError:  # PYTHONPATH=backend execution used by GitHub Actions.
    from common import SupabaseRest


TABLE = "automatic_source_points"
UPSERT_BATCH_SIZE = 500
INITIALIZATION_SERIES = "__INITIALIZED__"


def load(database: SupabaseRest, collector: str) -> dict[str, dict[date, tuple[date, float]]]:
    grouped: dict[str, dict[date, tuple[date, float]]] = {}
    offset = 0
    while True:
        rows = database.request("GET", TABLE, params={
            "select": "series_code,period_date,observed_on,value",
            "collector": f"eq.{collector}",
            "order": "series_code.asc,period_date.asc",
            "offset": str(offset),
            "limit": "1000",
        }) or []
        for row in rows:
            period = date.fromisoformat(str(row["period_date"])[:10])
            observed = date.fromisoformat(str(row["observed_on"])[:10])
            grouped.setdefault(str(row["series_code"]), {})[period] = (observed, float(row["value"]))
        if len(rows) < 1000:
            break
        offset += len(rows)
    return grouped


def store(
    database: SupabaseRest,
    collector: str,
    values: dict[str, dict[date, tuple[date, float]]],
) -> int:
    updated_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "collector": collector,
            "series_code": series,
            "period_date": period.isoformat(),
            "observed_on": observed.isoformat(),
            "value": round(value, 8),
            "updated_at": updated_at,
        }
        for series, points in values.items()
        for period, (observed, value) in points.items()
    ]
    for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
        database.upsert(
            TABLE,
            rows[offset:offset + UPSERT_BATCH_SIZE],
            conflict="collector,series_code,period_date",
        )
    return len(rows)


def is_initialized(cache: dict[str, dict[date, tuple[date, float]]]) -> bool:
    return bool(cache.get(INITIALIZATION_SERIES))


def mark_initialized(database: SupabaseRest, collector: str, completed_on: date) -> None:
    store(database, collector, {
        INITIALIZATION_SERIES: {date(1970, 1, 1): (completed_on, 1.0)},
    })
