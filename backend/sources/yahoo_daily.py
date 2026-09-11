"""Yahoo Finance daily market rows used only by scheduled incremental collection.

The long-history backfill contract remains unchanged.  This adapter is intentionally small and
provider-specific so switching the live source never rewrites historical economic-chart rows.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

from common import request_with_retry


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())


def parse_yahoo_daily_payload(
    payload: object,
    *,
    series_code: str,
    symbol: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"Yahoo {symbol} response is not an object.")
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise RuntimeError(f"Yahoo {symbol} response has no chart object.")
    if chart.get("error"):
        raise RuntimeError(f"Yahoo {symbol} returned an error: {chart.get('error')}")
    result = chart.get("result")
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        raise RuntimeError(f"Yahoo {symbol} response has no result rows.")
    series = result[0]
    timestamps = series.get("timestamp")
    indicators = series.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        raise RuntimeError(f"Yahoo {symbol} response is missing timestamps or indicators.")
    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        raise RuntimeError(f"Yahoo {symbol} response has no quote rows.")
    closes = quotes[0].get("close")
    if not isinstance(closes, list):
        raise RuntimeError(f"Yahoo {symbol} response has no close series.")

    rows: list[dict[str, Any]] = []
    for stamp, raw_close in zip(timestamps, closes):
        try:
            observed = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date()
            value = float(raw_close)
        except (TypeError, ValueError, OverflowError):
            continue
        if value != value or not (start <= observed <= end):
            continue
        rows.append({
            "series_code": series_code,
            "observation_date": observed.isoformat(),
            "value": value,
            "frequency": "D",
            "source": f"YAHOO:{symbol}",
        })
    unique = {str(row["observation_date"]): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def fetch_yahoo_daily_rows(series_code: str, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
    params = {
        "period1": str(_epoch(start)),
        "period2": str(_epoch(end + timedelta(days=1))),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    response = request_with_retry(lambda: requests.get(
        url,
        params=params,
        headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"},
        timeout=45,
    ))
    response.raise_for_status()
    rows = parse_yahoo_daily_payload(
        response.json(), series_code=series_code, symbol=symbol, start=start, end=end
    )
    if not rows:
        raise RuntimeError(f"Yahoo {symbol} returned no usable daily rows.")
    return rows
