"""Official U.S. and Korean policy-rate source adapters.

FRED provides daily target-range observations.  The policy-rate chart stores
one observation per official FOMC decision date, including decisions to hold.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import date, timedelta
import re
from typing import Any

import requests

from common import fetch_fred_observations, request_with_retry, require_env


US_POLICY_LOWER_SERIES = "DFEDTARL"
US_POLICY_UPPER_SERIES = "DFEDTARU"
KR_POLICY_STAT_CODE = "722Y001"
KR_POLICY_ITEM_CODE = "0101000"


def _number(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {".", "-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fred_values(series_id: str, start: date, end: date, api_key: str) -> dict[date, float]:
    values: dict[date, float] = {}
    for row in fetch_fred_observations(
        series_id,
        api_key,
        start=start.isoformat(),
        end=end.isoformat(),
    ):
        raw_date = row.get("date")
        value = _number(row.get("value"))
        if not isinstance(raw_date, str) or value is None:
            continue
        try:
            values[date.fromisoformat(raw_date)] = value
        except ValueError:
            continue
    if not values:
        raise RuntimeError(f"FRED {series_id} returned no usable policy-rate values")
    return values


def fetch_us_policy_rate_rows(
    start: date,
    end: date,
    api_key: str | None = None,
) -> list[dict[str, object]]:
    """Return every published U.S. target-range observation with its midpoint."""
    key = api_key or require_env("FRED_API_KEY")
    lower = _fred_values(US_POLICY_LOWER_SERIES, start, end, key)
    upper = _fred_values(US_POLICY_UPPER_SERIES, start, end, key)
    shared = sorted(lower.keys() & upper.keys())
    rows = [
        {
            "observed_on": observed.isoformat(),
            "target_lower_pct": round(lower[observed], 4),
            "target_upper_pct": round(upper[observed], 4),
            "target_mid_pct": round((lower[observed] + upper[observed]) / 2, 4),
            "source": f"FRED:{US_POLICY_LOWER_SERIES},{US_POLICY_UPPER_SERIES}",
        }
        for observed in shared
    ]
    if not rows:
        raise RuntimeError("FRED target-range bounds do not have overlapping observation dates")
    return rows


FED_FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED_FOMC_HISTORICAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
_FED_STATEMENT_DATE = re.compile(r"monetary(\d{8})a\.htm", re.IGNORECASE)


def _fed_statement_dates(html: str) -> set[date]:
    dates: set[date] = set()
    for raw in _FED_STATEMENT_DATE.findall(html):
        try:
            dates.add(date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])))
        except ValueError:
            continue
    return dates


def _fetch_fed_page(url: str) -> str:
    response = request_with_retry(lambda: requests.get(url, timeout=45))
    response.raise_for_status()
    return response.text


def fetch_fed_decision_dates(start: date, end: date) -> list[date]:
    """Return official FOMC statement dates directly from the Federal Reserve.

    The calendar page covers 2010 onward; the separate historical page provides
    2009.  A statement is the official decision record, so holds are retained.
    """
    dates = _fed_statement_dates(_fetch_fed_page(FED_FOMC_CALENDAR_URL))
    # The calendar page has a rolling set of recent years.  Complete older
    # years directly from each official historical archive instead of inferring
    # meetings from daily rate values.
    for year in range(start.year, end.year + 1):
        if not any(observed.year == year for observed in dates):
            dates.update(_fed_statement_dates(_fetch_fed_page(FED_FOMC_HISTORICAL_URL.format(year=year))))
    result = sorted(observed for observed in dates if start <= observed <= end)
    if not result:
        raise RuntimeError("Federal Reserve FOMC calendar returned no decision dates")
    return result


def us_policy_event_chart_rows(
    source_rows: list[dict[str, object]],
    decision_dates: list[date],
    *,
    start: date | None = None,
) -> list[dict[str, object]]:
    """Match official FOMC decisions to the first published FRED target range."""
    source_by_date = {
        date.fromisoformat(str(row["observed_on"])): row
        for row in source_rows
    }
    observed_dates = sorted(source_by_date)
    rows: list[dict[str, object]] = []
    for decision in sorted(set(decision_dates)):
        if start is not None and decision < start:
            continue
        position = bisect_left(observed_dates, decision)
        if position == len(observed_dates) or observed_dates[position] > decision + timedelta(days=7):
            raise RuntimeError(f"FRED policy-rate value missing after FOMC decision {decision.isoformat()}")
        source = source_by_date[observed_dates[position]]
        rows.append({
            "series_code": "US_POLICY_RATE_MID",
            "observation_date": decision.isoformat(),
            "value": float(source["target_mid_pct"]),
            "frequency": "D",
            "source": "DERIVED:FED:FOMC-statement+FRED:DFEDTARL,DFEDTARU:midpoint",
        })
    if not rows:
        raise RuntimeError("U.S. policy-rate decision projection produced no rows")
    return rows


def fetch_korea_policy_rate_rows(
    start: date,
    end: date,
    api_key: str | None = None,
) -> list[dict[str, object]]:
    """Return the monthly Bank of Korea base rate series from ECOS."""
    key = api_key or require_env("ECOS_API_KEY")
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/"
        f"{KR_POLICY_STAT_CODE}/M/{start:%Y%m}/{end:%Y%m}/{KR_POLICY_ITEM_CODE}"
    )
    response = request_with_retry(lambda: requests.get(url, timeout=45))
    response.raise_for_status()
    payload = response.json()
    source_rows = ((payload.get("StatisticSearch") or {}).get("row") or [])
    rows: list[dict[str, object]] = []
    for source in source_rows:
        raw_time = str(source.get("TIME") or "")
        value = _number(source.get("DATA_VALUE"))
        if len(raw_time) != 6 or value is None:
            continue
        try:
            observed = date(int(raw_time[:4]), int(raw_time[4:6]), 1)
        except ValueError:
            continue
        rows.append({
            "series_code": "KR_POLICY_RATE",
            "observation_date": observed.isoformat(),
            "value": round(value, 4),
            "frequency": "M",
            "source": f"ECOS:{KR_POLICY_STAT_CODE}/{KR_POLICY_ITEM_CODE}",
        })
    if not rows:
        raise RuntimeError("ECOS Korean policy-rate series returned no usable monthly values")
    return rows
