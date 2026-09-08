"""Persistence representation of seasonal windows, not sampling policy."""
from __future__ import annotations
from typing import Any, Iterable
from decimal import Decimal
from .values import decimal_value, update_seasonal_window

def _seasonal_window_index(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str, int], tuple[list[int], list[Decimal]]]:
    windows: dict[tuple[str, str, int], tuple[list[int], list[Decimal]]] = {}
    for row in rows:
        pairs = [
            (int(year), parsed)
            for year, value in zip(row.get("sample_years") or [], row.get("sample_values") or [])
            if (parsed := decimal_value(value)) is not None
        ]
        windows[(str(row["entity_id"]), str(row["metric"]), int(row["fiscal_quarter"]))] = (
            [year for year, _ in pairs],
            [value for _, value in pairs],
        )
    return windows

def _window_samples(
    windows: dict[tuple[str, str, int], tuple[list[int], list[Decimal]]],
    entity_id: str,
    metric: str,
    quarter: int,
    before_year: int,
) -> list[Decimal]:
    years, values = windows.get((entity_id, metric, quarter), ([], []))
    return [value for year, value in zip(years, values) if year < before_year]

def _advance_window(
    windows: dict[tuple[str, str, int], tuple[list[int], list[Decimal]]],
    *,
    entity_type: str,
    entity_id: str,
    metric: str,
    year: int,
    quarter: int,
    value: Decimal | None,
) -> dict[str, Any] | None:
    key = (entity_id, metric, quarter)
    if key not in windows and value is None:
        return None
    years, values = windows.get(key, ([], []))
    updated_years, updated_values = update_seasonal_window(years, values, year=year, value=value)
    windows[key] = (updated_years, updated_values)
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metric": metric,
        "fiscal_quarter": quarter,
        "sample_years": updated_years,
        "sample_values": updated_values,
    }
