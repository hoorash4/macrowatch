"""Storage boundary for reusable series calculated from canonical source observations."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

from common import SupabaseRest


TABLE = "economic_chart_derived_points"
READ_VIEW = "economic_chart_series_points"
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
        page = database.request("GET", TABLE, params=params) or []
        for row in page:
            if row.get("value") is not None:
                values[date.fromisoformat(str(row["observation_date"])[:10])] = float(row["value"])
        if len(page) < 1000:
            return values
        offset += len(page)


def rows(series_code: str, values: Mapping[date, float], *, frequency: str,
         source: str) -> list[dict[str, object]]:
    if not source.startswith(("DERIVED:", "RESAMPLED:", "INTERNAL:")):
        raise ValueError(f"derived series source must describe an internal calculation: {source}")
    return [{"series_code": series_code, "observation_date": observed.isoformat(),
             "value": round(float(value), 8), "frequency": frequency, "source": source}
            for observed, value in sorted(values.items())]


def store(database: SupabaseRest, payload: Iterable[Mapping[str, object]]) -> int:
    normalized: list[dict[str, object]] = []
    keys: set[tuple[str, str]] = set()
    for index, item in enumerate(payload):
        missing = REQUIRED_FIELDS.difference(item)
        if missing:
            raise ValueError(f"derived series row {index} is missing: {', '.join(sorted(missing))}")
        row = dict(item)
        series_code = str(row["series_code"] or "").strip()
        observation_date = str(row["observation_date"] or "")[:10]
        source = str(row["source"] or "")
        if not series_code or len(observation_date) != 10:
            raise ValueError(f"derived series row {index} has an invalid key")
        if not source.startswith(("DERIVED:", "RESAMPLED:", "INTERNAL:")):
            raise ValueError(f"derived series row {index} has a non-derived source: {source}")
        key = (series_code, observation_date)
        if key in keys:
            raise ValueError(f"duplicate derived series key in one write: {series_code}/{observation_date}")
        keys.add(key)
        normalized.append(row)
    for offset in range(0, len(normalized), UPSERT_BATCH_SIZE):
        database.upsert(TABLE, normalized[offset:offset + UPSERT_BATCH_SIZE],
                        conflict="series_code,observation_date")
    return len(normalized)
