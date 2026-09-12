"""Temporary one-off Redbook historical backfill. Delete after verified run."""
from __future__ import annotations

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

    db = SupabaseRest()
    by_date: dict[str, dict] = {}
    request_errors: list[str] = []
    year_counts_fetched: dict[int, int] = {}
    # Crawl newest first so the useful 10-year window is secured before older best-effort history.
    for year in range(today.year, START_YEAR - 1, -1):
        start = date(year, 1, 1)
        end = today if year == today.year else date(year, 12, 31)
        try:
            year_rows = _fetch_range(session, start, end)
            year_counts_fetched[year] = len(year_rows)
            if year_rows:
                db.upsert("economic_chart_points", year_rows, conflict="series_code,observation_date")
                for row in year_rows:
                    by_date[row["observation_date"]] = row
        except Exception as error:
            request_errors.append(f"{year}: {error.__class__.__name__}: {error}")
        time.sleep(0.9)

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
    print(json.dumps({
        "stage": "redbook_backfill_temp",
        "request_error_count": len(request_errors),
        "request_errors": request_errors[:20],
        "fetched_year_counts": dict(sorted(year_counts_fetched.items())),
        "rows": len(rows),
        "min_date": dates[0].isoformat(),
        "max_date": dates[-1].isoformat(),
        "min_value": min(values),
        "max_value": max(values),
        "known_low_minus_12_6_found": any(abs(v + 12.6) < 1e-9 for v in values),
        "known_high_21_9_found": any(abs(v - 21.9) < 1e-9 for v in values),
        "year_counts": dict(sorted(year_counts.items())),
        "long_gaps_over_16_days": long_gaps[:30],
        "long_gap_count": len(long_gaps),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
