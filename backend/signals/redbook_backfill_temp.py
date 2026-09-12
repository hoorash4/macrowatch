"""Temporary one-off Redbook historical backfill. Delete after verified run."""
from __future__ import annotations

import calendar
import html
import json
import re
import time
from collections import Counter
from datetime import date, datetime

import requests

from common import SupabaseRest

URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
START_YEAR = 2005
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


def _quarter_ranges(year: int, end: date):
    for month in (1, 4, 7, 10):
        start = date(year, month, 1)
        if start > end:
            break
        end_month = min(month + 2, 12)
        last_day = calendar.monthrange(year, end_month)[1]
        finish = min(date(year, end_month, last_day), end)
        yield start, finish


def _fetch_range(session: requests.Session, start: date, end: date) -> list[dict]:
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
    response = session.post(URL, data=payload, timeout=60)
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


def main() -> None:
    today = date.today()
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
    request_errors: list[str] = []
    request_count = 0
    for year in range(START_YEAR, today.year + 1):
        for start, end in _quarter_ranges(year, today):
            request_count += 1
            try:
                for row in _fetch_range(session, start, end):
                    by_date[row["observation_date"]] = row
            except Exception as error:
                request_errors.append(f"{start}..{end}: {error.__class__.__name__}: {error}")
            time.sleep(0.15)

    rows = [by_date[key] for key in sorted(by_date)]
    if not rows:
        raise RuntimeError(f"No Redbook history recovered; errors={request_errors[:10]}")

    dates = [date.fromisoformat(row["observation_date"]) for row in rows]
    values = [float(row["value"]) for row in rows]
    year_counts = Counter(d.year for d in dates)
    long_gaps = [
        (a.isoformat(), b.isoformat(), (b - a).days)
        for a, b in zip(dates, dates[1:]) if (b - a).days > 16
    ]

    # External cross-checks documented by Trading Economics / TradingView.
    has_known_low = any(abs(v - (-12.6)) < 1e-9 for v in values)
    has_known_high = any(abs(v - 21.9) < 1e-9 for v in values)
    if dates[-1].year < today.year - 1:
        raise RuntimeError(f"Redbook archive is stale: latest={dates[-1]}")

    db = SupabaseRest()
    db.upsert("economic_chart_points", rows, conflict="series_code,observation_date")

    print(json.dumps({
        "stage": "redbook_backfill_temp",
        "requests": request_count,
        "request_errors": request_errors[:20],
        "request_error_count": len(request_errors),
        "rows": len(rows),
        "min_date": dates[0].isoformat(),
        "max_date": dates[-1].isoformat(),
        "min_value": min(values),
        "max_value": max(values),
        "known_low_minus_12_6_found": has_known_low,
        "known_high_21_9_found": has_known_high,
        "year_counts": dict(sorted(year_counts.items())),
        "long_gaps_over_16_days": long_gaps[:30],
        "long_gap_count": len(long_gaps),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
