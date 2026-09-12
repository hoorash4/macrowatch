"""Market observations shared by the relative-value and attractiveness pipelines."""
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from common import request_with_retry

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def valid_fred_values(observations: list[dict[str, Any]]) -> dict[date, float]:
    values: dict[date, float] = {}
    for observation in observations:
        try:
            values[date.fromisoformat(str(observation["date"]))] = float(observation["value"])
        except (KeyError, TypeError, ValueError):
            continue
    return values


def fetch_yahoo_adjusted(symbol: str, start: date, end: date) -> dict[date, float]:
    """Fetch split- and distribution-adjusted closes from Yahoo's chart feed."""

    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    response = request_with_retry(lambda: requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        },
        headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"},
        timeout=45,
    ))
    response.raise_for_status()
    result = response.json().get("chart", {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo returned no chart result for {symbol}")
    chart = result[0]
    timestamps = chart.get("timestamp") or []
    adjusted_groups = chart.get("indicators", {}).get("adjclose") or []
    adjusted = adjusted_groups[0].get("adjclose", []) if adjusted_groups else []
    values: dict[date, float] = {}
    for timestamp, raw_value in zip(timestamps, adjusted):
        if raw_value is None:
            continue
        values[datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date()] = float(raw_value)
    if not values:
        raise RuntimeError(f"Yahoo returned no adjusted closes for {symbol}")
    return values
