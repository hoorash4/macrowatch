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


def _fetch_window(session: requests.Session, start: date, end: date) -> list[dict]:
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
    quarter = int(os.environ["REDBOOK_QUARTER"])
    if quarter not in {1, 2, 3, 4}:
        raise RuntimeError(f"Bad quarter: {quarter}")
    today = date.today()
    first_month = 1 + (quarter - 1) * 3
    quarter_start = date(year, first_month, 1)
    if quarter_start > today:
        print(json.dumps({"stage": "redbook_backfill_quarter", "year": year, "quarter": quarter, "rows": 0, "skipped": "future"}))
        return

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
    window_counts: dict[str, int] = {}
    for month in range(first_month, first_month + 3):
        if month > 12:
            break
        last_day = calendar.monthrange(year, month)[1]
        for start_day, end_day in ((1, 15), (16, last_day)):
            start = date(year, month, start_day)
            if start > today:
                continue
            end = min(date(year, month, end_day), today)
            key = f"{month:02d}-{start_day:02d}:{end.day:02d}"
            try:
                window_rows = _fetch_window(session, start, end)
                window_counts[key] = len(window_rows)
                for row in window_rows:
                    by_date[row["observation_date"]] = row
            except Exception as error:
                errors.append(f"{start}..{end}: {error.__class__.__name__}: {error}")
            time.sleep(0.7)

    rows = [by_date[key] for key in sorted(by_date)]
    if rows:
        SupabaseRest().upsert("economic_chart_points", rows, conflict="series_code,observation_date")
    print(json.dumps({
        "stage": "redbook_backfill_quarter",
        "year": year,
        "quarter": quarter,
        "rows": len(rows),
        "min_date": rows[0]["observation_date"] if rows else None,
        "max_date": rows[-1]["observation_date"] if rows else None,
        "window_counts": window_counts,
        "error_count": len(errors),
        "errors": errors[:8],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
