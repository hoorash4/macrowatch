"""Single storage boundary for reusable economic and market time series."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

from common import SupabaseRest


TABLE = "economic_chart_points"
UPSERT_BATCH_SIZE = 500
REQUIRED_FIELDS = frozenset({"series_code", "observation_date", "value", "frequency", "source"})


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


def _validate_payload(payload: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    keys: set[tuple[str, str]] = set()
    for index, item in enumerate(payload):
        missing = REQUIRED_FIELDS.difference(item)
        if missing:
            raise ValueError(f"canonical series row {index} is missing: {', '.join(sorted(missing))}")
        series_code = str(item["series_code"] or "").strip()
        observation_date = str(item["observation_date"] or "")[:10]
        if not series_code or len(observation_date) != 10:
            raise ValueError(f"canonical series row {index} has an invalid key")
        key = (series_code, observation_date)
        if key in keys:
            raise ValueError(f"duplicate canonical series key in one write: {series_code}/{observation_date}")
        keys.add(key)
        normalized.append(dict(item))
    return normalized


def store(database: SupabaseRest, payload: Iterable[Mapping[str, object]]) -> int:
    """Validate and persist canonical observations through the sole Python write boundary."""
    rows_to_store = _validate_payload(payload)
    for offset in range(0, len(rows_to_store), UPSERT_BATCH_SIZE):
        database.upsert(TABLE, rows_to_store[offset:offset + UPSERT_BATCH_SIZE],
                        conflict="series_code,observation_date")
    return len(rows_to_store)
