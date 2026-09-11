"""Continuously rolled front-month WTI futures history.

Yahoo Finance's CL=F symbol follows the active/front WTI crude oil futures contract as one
continuous daily series. MacroWatch stores the provider's returned close directly; it does not
stitch, roll or back-adjust individual contract months itself.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

from common import request_with_retry

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/CL%3DF"
SOURCE = "YAHOO:CL=F"


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())


def parse_wti_futures_payload(payload: object, start: date, end: date) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise RuntimeError("Yahoo WTI response is not an object.")
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise RuntimeError("Yahoo WTI response has no chart object.")
    error = chart.get("error")
    if error:
        raise RuntimeError(f"Yahoo WTI returned an error: {error}")
    result = chart.get("result")
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        raise RuntimeError("Yahoo WTI response has no result rows.")
    series = result[0]
    timestamps = series.get("timestamp")
    indicators = series.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        raise RuntimeError("Yahoo WTI response is missing timestamps or indicators.")
    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        raise RuntimeError("Yahoo WTI response has no quote rows.")
    closes = quotes[0].get("close")
    if not isinstance(closes, list):
        raise RuntimeError("Yahoo WTI response has no close series.")

    rows: list[dict[str, Any]] = []
    for stamp, raw_close in zip(timestamps, closes):
        try:
            observed = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date()
            value = float(raw_close)
        except (TypeError, ValueError, OverflowError):
            continue
        if not (start <= observed <= end) or value != value:
            continue
        rows.append({
            "series_code": "WTI",
            "observation_date": observed.isoformat(),
            "value": value,
            "frequency": "D",
            "source": SOURCE,
        })
    unique = {str(row["observation_date"]): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def fetch_wti_futures_rows(start: date, end: date) -> list[dict[str, Any]]:
    # period2 is exclusive. Add one day so the requested end date is eligible.
    params = {
        "period1": str(_epoch(start)),
        "period2": str(_epoch(end + timedelta(days=1))),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    response = request_with_retry(lambda: requests.get(
        YAHOO_CHART_URL,
        params=params,
        headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"},
        timeout=45,
    ))
    response.raise_for_status()
    rows = parse_wti_futures_payload(response.json(), start, end)
    if not rows:
        raise RuntimeError("Yahoo CL=F returned no usable WTI futures rows.")
    return rows
