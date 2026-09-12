"""Temporary one-off Redbook historical backfill. Delete after verified run."""
from __future__ import annotations

import calendar
import html
import json
import os
import re
import time
from datetime import date, datetime

import requests

from common import SupabaseRest

URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
SERIES = "REDBOOK"
SOURCE = "INVESTING_ARCHIVE:REDBOOK/REDBOOK_RESEARCH"

ROW_RE = re.compile(
    r'<tr[^>]*event_attr_ID="911"[^>]*data-event-datetime="(?P<dt>[^"]+)"[^>]*>(?P<body>.*?)</tr>',
    re.I | re.S,
)
ACTUAL_RE = re.compile(r'id="eventActual_[^"]+"[^>]*>(?P<value>.*?)</td>', re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(value: str) -> str:
    return html.unescape(TAG_RE.sub("", value)).replace("\xa0", " ").strip()


def _actual(value: str) -> float | None:
    text = _clean_text(value).replace("%", "").replace(",", "").strip()
    if not text or text in {"-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fetch_month(session: requests.Session, start: date, end: date) -> list[dict]:
    payload = {
        "country[]": "5",
        "dateFrom": start.isoformat(),
        "dateTo": end.isoformat(),
        "timeZone": "8",
        "timeFilter": "timeRemain",
        "currentTab": "custom",
        "submitFilters": "1",
        "limit_from": "0",
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.post(URL, data=payload, timeout=90)
            response.raise_for_status()
            data = response.json()
            body = str(data.get("data") or "") if isinstance(data, dict) else ""
            rows: list[dict] = []
            for match in ROW_RE.finditer(body):
                actual_match = ACTUAL_RE.search(match.group("body"))
                if not actual_match:
                    continue
                value = _actual(actual_match.group("value"))
                if value is None:
                    continue
                try:
                    observed = datetime.strptime(match.group("dt")[:10], "%Y/%m/%d").date()
                except ValueError:
                    continue
                rows.append({
                    "series_code": SERIES,
                    "observation_date": observed.isoformat(),
                    "value": value,
                    "frequency": "W",
                    "source": SOURCE,
                })
            return rows
        except Exception as error:
            last_error = error
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"archive request failed after retries: {last_error}")


def main() -> None:
    year = int(os.environ["REDBOOK_YEAR"])
    today = date.today()
    if year > today.year:
        raise RuntimeError(f"Future year requested: {year}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Referer": "https://www.investing.com/economic-calendar/",
        "Origin": "https://www.investing.com",
    }
    session = requests.Session()
    session.headers.update(headers)
    session.get("https://www.investing.com/economic-calendar/", timeout=45).raise_for_status()

    by_date: dict[str, dict] = {}
    errors: list[str] = []
    monthly_counts: dict[int, int] = {}
    last_month = today.month if year == today.year else 12
    for month in range(1, last_month + 1):
        start = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end = min(date(year, month, last_day), today)
        try:
            month_rows = _fetch_month(session, start, end)
            monthly_counts[month] = len(month_rows)
            for row in month_rows:
                by_date[row["observation_date"]] = row
        except Exception as error:
            errors.append(f"{year}-{month:02d}: {error.__class__.__name__}: {error}")
        time.sleep(0.55)

    rows = [by_date[key] for key in sorted(by_date)]
    if rows:
        SupabaseRest().upsert("economic_chart_points", rows, conflict="series_code,observation_date")
    print(json.dumps({
        "stage": "redbook_backfill_year",
        "year": year,
        "rows": len(rows),
        "min_date": rows[0]["observation_date"] if rows else None,
        "max_date": rows[-1]["observation_date"] if rows else None,
        "monthly_counts": monthly_counts,
        "error_count": len(errors),
        "errors": errors[:12],
    }, ensure_ascii=False, sort_keys=True))
    if year >= 2015 and len(rows) < 35:
        raise RuntimeError(f"Too few Redbook observations for {year}: {len(rows)}")


if __name__ == "__main__":
    main()
