"""U.S. Census MARTS retail-sales source for the economic-chart service."""
from __future__ import annotations

from datetime import date
from typing import Any

import requests

from common import request_with_retry

BASE_URL = "https://api.census.gov/data/timeseries/eits/marts"
CATEGORY_CODE = "44X72"  # Retail Trade and Food Services
DATA_TYPE_CODE = "SM"    # Sales - Monthly, millions of dollars
SERIES_CODE = "US_RETAIL_SALES"
SOURCE = "CENSUS:MARTS/44X72/SM/SA/YOY"


def _number(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text.lower() in {"na", "n/a", "null", "none", "-", "(s)"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _month_date(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) < 7:
        return None
    try:
        return date.fromisoformat(text[:7] + "-01")
    except ValueError:
        return None


def _previous_year(observed: date) -> date:
    return date(observed.year - 1, observed.month, 1)


def parse_marts_payload(payload: Any) -> dict[date, float]:
    """Return seasonally-adjusted total retail-and-food-service monthly sales levels."""
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        raise RuntimeError("Census MARTS 응답 형식이 올바르지 않습니다.")
    header = [str(value) for value in payload[0]]
    required = {"cell_value", "category_code", "data_type_code", "seasonally_adj", "time"}
    missing = required.difference(header)
    if missing:
        raise RuntimeError("Census MARTS 필수 필드 누락: " + ", ".join(sorted(missing)))

    result: dict[date, float] = {}
    for raw in payload[1:]:
        if not isinstance(raw, list):
            continue
        row = {name: raw[index] if index < len(raw) else None for index, name in enumerate(header)}
        if str(row.get("category_code") or "") != CATEGORY_CODE:
            continue
        if str(row.get("data_type_code") or "") != DATA_TYPE_CODE:
            continue
        if str(row.get("seasonally_adj") or "").strip().lower() != "yes":
            continue
        observed = _month_date(row.get("time"))
        value = _number(row.get("cell_value"))
        if observed is not None and value is not None:
            result[observed] = value
    return dict(sorted(result.items()))


def yoy_values(values: dict[date, float], *, start: date | None = None, end: date | None = None) -> dict[date, float]:
    """Convert monthly sales levels to same-month year-over-year percentage changes."""
    result: dict[date, float] = {}
    for observed, value in sorted(values.items()):
        if start is not None and observed < start:
            continue
        if end is not None and observed > end:
            continue
        prior = values.get(_previous_year(observed))
        if prior is None or prior == 0:
            continue
        result[observed] = round((value / prior - 1.0) * 100.0, 6)
    return result


def fetch_census_retail_sales(
    start: date,
    end: date,
    *,
    api_key: str,
    session: requests.Session | None = None,
) -> dict[date, float]:
    """Fetch MARTS retail-sales YoY for ``start``..``end`` using one extra comparison year."""
    if start > end:
        return {}
    if not str(api_key or "").strip():
        raise RuntimeError("CENSUS_API_KEY가 필요합니다.")

    comparison_start = date(start.year - 1, start.month, 1)
    client = session or requests.Session()
    levels: dict[date, float] = {}
    for year in range(comparison_start.year, end.year + 1):
        params = {
            "get": "cell_value,time_slot_id,time_slot_date,error_data,category_code,seasonally_adj,data_type_code",
            "category_code": CATEGORY_CODE,
            "data_type_code": DATA_TYPE_CODE,
            "time": str(year),
            "key": api_key,
        }
        response = request_with_retry(lambda: client.get(BASE_URL, params=params, timeout=30))
        response.raise_for_status()
        levels.update(parse_marts_payload(response.json()))
    return yoy_values(levels, start=start, end=end)


def chart_rows(values: dict[date, float]) -> list[dict]:
    """Map Census YoY observations onto the existing economic_chart_points schema."""
    return [
        {
            "series_code": SERIES_CODE,
            "observation_date": observed.isoformat(),
            "value": value,
            "frequency": "M",
            "source": SOURCE,
        }
        for observed, value in sorted(values.items())
    ]
