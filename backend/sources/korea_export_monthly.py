"""Monthly Korean export fallback for historical economic-chart coverage.

ECOS provides monthly customs-basis export amounts.  KOSIS/Statistics Korea explains the
working-day convention as weekday=1, Saturday=0.5, public holiday=0.  We use that convention
with the Korean public-holiday calendar to derive a monthly daily-average export value when
historical 10/20-day KCS snapshots are unavailable.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

import holidays
import requests

from common import request_with_retry, require_env

ECOS_TRADE_STAT_CODE = "901Y118"
ECOS_EXPORT_ITEM_CODE = "T002"


def monthly_workdays(year: int, month: int) -> float:
    """Return official-style Korean export working days for a calendar month."""
    kr_holidays = holidays.KR(years=[year])
    last_day = calendar.monthrange(year, month)[1]
    total = 0.0
    for day in range(1, last_day + 1):
        current = date(year, month, day)
        if current in kr_holidays or current.weekday() == 6:
            continue
        total += 0.5 if current.weekday() == 5 else 1.0
    return total


def fetch_monthly_export_rows(start_month: date, end_month: date) -> list[dict[str, Any]]:
    """Fetch ECOS monthly export amounts and derive workday-adjusted daily averages.

    The value is stored in hundred-million USD per working day, matching the intra-month
    economic chart series.  Rows are monthly fallback observations, not fabricated 10-day data.
    """
    key = require_env("ECOS_API_KEY")
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/"
        f"{ECOS_TRADE_STAT_CODE}/M/{start_month:%Y%m}/{end_month:%Y%m}/{ECOS_EXPORT_ITEM_CODE}"
    )
    response = request_with_retry(lambda: requests.get(url, timeout=45))
    response.raise_for_status()
    payload = response.json()
    source_rows = ((payload.get("StatisticSearch") or {}).get("row") or [])
    rows: list[dict[str, Any]] = []
    for item in source_rows:
        raw_month = str(item.get("TIME") or "").strip()
        raw_value = str(item.get("DATA_VALUE") or "").strip().replace(",", "")
        if len(raw_month) != 6 or not raw_month.isdigit():
            continue
        try:
            export_musd = float(raw_value)
        except ValueError:
            continue
        year, month = int(raw_month[:4]), int(raw_month[4:6])
        workdays = monthly_workdays(year, month)
        if workdays <= 0:
            continue
        last_day = calendar.monthrange(year, month)[1]
        rows.append({
            "series_code": "KR_EXPORT_DAILY_AVG",
            "observation_date": date(year, month, last_day).isoformat(),
            "value": round(export_musd / workdays / 100.0, 6),
            "frequency": "M",
            "source": f"ECOS:{ECOS_TRADE_STAT_CODE}/{ECOS_EXPORT_ITEM_CODE}+KR_WORKDAYS",
        })
    return rows


def months_with_intramonth_points(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("observation_date") or "")[:7] for row in rows if row.get("observation_date")}


def historical_month_end_cutoff(today: date) -> date:
    """Only backfill completed months; automatic intra-month collection owns the current month."""
    return today.replace(day=1) - timedelta(days=1)
