"""Monthly Korean export source for historical economic-chart backfill.

Historical backfill uses only official KCS month-end export releases. Each completed month
becomes one month-end observation computed as total exports divided by total working days.
No 10-day or 20-day observations are fabricated here; the scheduled collector owns the
intra-month 1-10, 11-20 and 21-month-end segments separately.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import requests

from common import request_with_retry
from sources.korea_export_intramonth import (
    BBS_ID,
    DETAIL_URL,
    MENU_ID,
    USER_AGENT,
    ReleaseLink,
    fetch_release_links,
    parse_snapshot,
)

MONTHLY_SOURCE = "KCS:MONTHLY_EXPORT/daily_avg"


def _detail_markup(session: requests.Session, link: ReleaseLink) -> str:
    params = {"bbsId": BBS_ID, "mi": MENU_ID, "nttSn": link.ntt_sn}
    if link.ntt_url:
        params["nttSnUrl"] = link.ntt_url
    response = request_with_retry(lambda: session.get(DETAIL_URL, params=params, timeout=30))
    response.raise_for_status()
    return response.text


def monthly_row_from_snapshot(snapshot) -> dict[str, Any]:
    """Convert one official KCS month-end cumulative snapshot to one daily-average point."""
    if snapshot.stage != "month_end":
        raise ValueError("monthly backfill requires a month-end KCS snapshot")
    if snapshot.cumulative_workdays <= 0:
        raise ValueError("monthly KCS working days must be positive")
    return {
        "series_code": "KR_EXPORT_DAILY_AVG",
        "observation_date": snapshot.period_end.isoformat(),
        "value": round(snapshot.cumulative_export_musd / snapshot.cumulative_workdays / 100.0, 6),
        "frequency": "M",
        "source": MONTHLY_SOURCE,
    }


def fetch_monthly_export_rows(start_month: date, end_month: date) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch completed KCS month-end releases and return one observation per successful month.

    Detail failures are isolated per month so rows already fetched successfully remain usable.
    Board-level lookup errors are returned alongside detail errors for logging by the caller.
    """
    links, errors = fetch_release_links(start_month)
    monthly_links = [
        link for link in links
        if link.stage == "month_end" and start_month <= link.reference_month <= end_month
    ]
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    rows: list[dict[str, Any]] = []
    for index, link in enumerate(monthly_links):
        try:
            snapshot = parse_snapshot(_detail_markup(session, link), link)
            rows.append(monthly_row_from_snapshot(snapshot))
        except Exception as error:
            errors.append(
                f"{link.reference_month:%Y-%m} month-end: {error.__class__.__name__}: {error}"
            )
        if index + 1 < len(monthly_links):
            time.sleep(0.12)
    return rows, errors


def historical_month_end_cutoff(today: date) -> date:
    """Only backfill completed months; automatic intra-month collection owns the current month."""
    return today.replace(day=1) - timedelta(days=1)
