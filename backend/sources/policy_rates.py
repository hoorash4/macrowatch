"""Official Korean policy-rate and U.S. FOMC decision-rate adapters."""
from __future__ import annotations

from datetime import date
from html import unescape
import re
from typing import Any
from urllib.parse import urljoin

import requests

from common import request_with_retry, require_env


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


FED_FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED_FOMC_HISTORICAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
_FED_ORIGIN = "https://www.federalreserve.gov"
_FED_STATEMENT_LINK = re.compile(r"href\s*=\s*['\"](?P<href>[^'\"]*monetary(?P<date>\d{8})a\.htm)[^'\"]*['\"]", re.IGNORECASE)
_FED_HISTORICAL_STATEMENT_LINK = re.compile(
    r"<a\b[^>]*href\s*=\s*['\"](?P<href>[^'\"]*monetary(?P<date>\d{8})a\.htm)[^'\"]*['\"][^>]*>(?P<label>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_FED_TARGET_RANGE = re.compile(
    r"target\s+range\s+for\s+(?:the\s+)?federal\s+funds\s+rate\s+(?:at|to)\s+"
    r"(?P<lower>\d+(?:(?:[-‑–]\d+)?/\d+)?)\s+(?:to|[-‑–])\s+"
    r"(?P<upper>\d+(?:(?:[-‑–]\d+)?/\d+)?)\s+percent",
    re.IGNORECASE,
)


def _fed_statement_links(html: str) -> dict[date, str]:
    links: dict[date, str] = {}
    for match in _FED_STATEMENT_LINK.finditer(html):
        raw = match.group("date")
        try:
            observed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            continue
        links[observed] = urljoin(_FED_ORIGIN, match.group("href"))
    return links


def _fed_historical_statement_links(html: str) -> dict[date, str]:
    """Keep only the FOMC page's links labelled Statement, excluding other Fed releases."""
    links: dict[date, str] = {}
    for match in _FED_HISTORICAL_STATEMENT_LINK.finditer(html):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(match.group("label")))).strip().lower()
        if label not in {"statement", "fomc statement"}:
            continue
        raw = match.group("date")
        try:
            observed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            continue
        links[observed] = urljoin(_FED_ORIGIN, match.group("href"))
    return links


def _fetch_fed_page(url: str) -> str:
    response = request_with_retry(lambda: requests.get(url, timeout=45))
    response.raise_for_status()
    return response.text


def _fed_decision_links(start: date, end: date) -> dict[date, str]:
    """Return official FOMC statement URLs to verify DB coverage by decision date."""
    links = _fed_statement_links(_fetch_fed_page(FED_FOMC_CALENDAR_URL))
    # The calendar page has a rolling set of recent years.  Complete older
    # years directly from each official historical archive instead of inferring
    # meetings from daily rate values.
    for year in range(start.year, end.year + 1):
        if not any(observed.year == year for observed in links):
            links.update(_fed_historical_statement_links(_fetch_fed_page(FED_FOMC_HISTORICAL_URL.format(year=year))))
    result = {observed: href for observed, href in links.items() if start <= observed <= end}
    if not result:
        raise RuntimeError("Federal Reserve FOMC calendar returned no decision dates")
    return result


def _fed_percent(value: str) -> float:
    normalized = value.replace("‑", "-").replace("–", "-")
    if "/" in normalized and "-" not in normalized:
        numerator, denominator = normalized.split("/", 1)
        return float(numerator) / float(denominator)
    if "-" not in normalized:
        return float(normalized)
    whole, fraction = normalized.split("-", 1)
    numerator, denominator = fraction.split("/", 1)
    return float(whole) + float(numerator) / float(denominator)


def _fed_target_range(statement_html: str) -> tuple[float, float]:
    text = re.sub(r"<[^>]+>", " ", unescape(statement_html))
    text = re.sub(r"\s+", " ", text).strip()
    match = _FED_TARGET_RANGE.search(text)
    if not match:
        raise RuntimeError("Federal Reserve statement did not contain a target-rate range")
    lower, upper = _fed_percent(match.group("lower")), _fed_percent(match.group("upper"))
    if lower > upper:
        raise RuntimeError("Federal Reserve statement produced an inverted target-rate range")
    return lower, upper


def fetch_us_policy_rate_chart_rows(
    db: Any,
    start: date,
    end: date,
    *,
    fill_missing_from_fed: bool = False,
) -> list[dict[str, object]]:
    """Read analyzed FOMC events; explicit backfill alone fills missing records from the Fed."""
    saved = db.request("GET", "central_bank_policy_events", params={
        "select": "meeting_date,target_range_lower,target_range_upper",
        "central_bank": "eq.fed",
        "analysis_status": "eq.completed",
        "meeting_date": f"gte.{start.isoformat()}",
        "and": f"(meeting_date.lte.{end.isoformat()})",
        "order": "meeting_date.asc",
        "limit": "10000",
    }) or []
    stored: dict[date, tuple[float, float]] = {}
    for row in saved:
        try:
            observed = date.fromisoformat(str(row.get("meeting_date") or ""))
        except ValueError:
            continue
        lower, upper = _number(row.get("target_range_lower")), _number(row.get("target_range_upper"))
        if lower is not None and upper is not None and lower <= upper:
            stored[observed] = (lower, upper)

    if not fill_missing_from_fed:
        return [{
            "series_code": "US_POLICY_RATE_MID",
            "observation_date": observed.isoformat(),
            "value": round((lower + upper) / 2, 4),
            "frequency": "E",
            "source": "DB:central_bank_policy_events",
        } for observed, (lower, upper) in sorted(stored.items())]

    links = _fed_decision_links(start, end)
    rows: list[dict[str, object]] = []
    for decision, href in sorted(links.items()):
        if decision in stored:
            lower, upper = stored[decision]
            source = "DB:central_bank_policy_events"
        else:
            lower, upper = _fed_target_range(_fetch_fed_page(href))
            source = "FED:FOMC-statement"
        rows.append({
            "series_code": "US_POLICY_RATE_MID",
            "observation_date": decision.isoformat(),
            "value": round((lower + upper) / 2, 4),
            "frequency": "E",
            "source": source,
        })
    if not rows:
        raise RuntimeError("U.S. policy-rate collection produced no decision-date rows")
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
