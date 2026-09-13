"""Single storage boundary for reusable economic and market time series."""

from __future__ import annotations

from datetime import date
from typing import Iterable

from common import SupabaseRest


TABLE = "economic_chart_points"
UPSERT_BATCH_SIZE = 500


def load(database: SupabaseRest, series_code: str, *, start: date | None = None,
         end: date | None = None) -> dict[date, float]:
    values: dict[date, float] = {}
    offset = 0
    while True:
        params = {"select": "observation_date,value", "series_code": f"eq.{series_code}",
                  "order": "observation_date.asc", "offset": str(offset), "limit": "1000"}
        if start is not None:
            params["observation_date"] = f"gte.{start.isoformat()}"
        if end is not None:
            params["and"] = f"(observation_date.lte.{end.isoformat()})"
        rows = database.request("GET", TABLE, params=params) or []
        for row in rows:
            if row.get("value") is not None:
                values[date.fromisoformat(str(row["observation_date"])[:10])] = float(row["value"])
        if len(rows) < 1000:
            return values
        offset += len(rows)


def load_many(database: SupabaseRest, codes: Iterable[str], *, start: date | None = None,
              end: date | None = None) -> dict[str, dict[date, float]]:
    return {code: load(database, code, start=start, end=end) for code in codes}


def rows(series_code: str, values: dict[date, float], *, frequency: str,
         source: str) -> list[dict[str, object]]:
    return [{"series_code": series_code, "observation_date": observed.isoformat(),
             "value": round(value, 8), "frequency": frequency, "source": source}
            for observed, value in sorted(values.items())]


def store(database: SupabaseRest, payload: list[dict[str, object]]) -> int:
    for offset in range(0, len(payload), UPSERT_BATCH_SIZE):
        database.upsert(TABLE, payload[offset:offset + UPSERT_BATCH_SIZE],
                        conflict="series_code,observation_date")
    return len(payload)
