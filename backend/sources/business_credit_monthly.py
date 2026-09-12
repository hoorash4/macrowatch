"""Monthly business-credit and insolvency source adapters used by economic charts."""
from __future__ import annotations

from datetime import date
from html import unescape
import re
from typing import Iterable
from urllib.parse import urlencode, urljoin

import requests

from common import request_with_retry, require_env

USER_AGENT = "Mozilla/5.0 (compatible; MacroWatch/1.0; +https://hoorash4.github.io/macrowatch/)"
MONTHS = {name: i for i, name in enumerate((
    "January February March April May June July August September October November December"
).split(), 1)}
MONTH_PATTERN = "(?:" + "|".join(MONTHS) + ")"


def _request(url: str) -> requests.Response:
    response = request_with_retry(lambda: requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT}))
    response.raise_for_status()
    return response


def _row(code: str, year: int, month: int, value: float, source: str) -> dict:
    return {
        "series_code": code,
        "observation_date": f"{year:04d}-{month:02d}-01",
        "value": value,
        "frequency": "M",
        "source": source,
    }


def _plain_html(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _extract_epiq_ch11(text: str) -> list[tuple[int, int, int]]:
    """Extract explicit Commercial Chapter 11 month/count pairs without crossing sentences."""
    out: dict[tuple[int, int], int] = {}
    clean = text.replace("–", "-")
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    patterns = (
        (rf"([0-9][0-9,]*)\s+commercial\s+Chapter\s+11(?:\s+bankruptcy)?\s+filings\b[^.!?]*?\b(?:in|during|for|recorded\s+in)\s+({MONTH_PATTERN})\s+(20\d{{2}})", False),
        (rf"commercial\s+Chapter\s+11(?:\s+bankruptcy)?\s+filings\b[^.!?]*?(?:totaled|were|reached|increased[^.!?]*?to|decreased[^.!?]*?to)\s+([0-9][0-9,]*)\b[^.!?]*?\b(?:in|during|for)\s+({MONTH_PATTERN})\s+(20\d{{2}})", False),
        (rf"\b({MONTH_PATTERN})\s+(20\d{{2}})[^.!?]*?([0-9][0-9,]*)\s+commercial\s+Chapter\s+11(?:\s+bankruptcy)?\s+filings", True),
    )
    for sentence in sentences:
        if not re.search(r"commercial\s+chapter\s+11", sentence, re.I):
            continue
        for pattern, month_first in patterns:
            for match in re.finditer(pattern, sentence, re.I):
                if month_first:
                    month_name, year_s, count_s = match.groups()
                else:
                    count_s, month_name, year_s = match.groups()
                count = int(count_s.replace(",", ""))
                year = int(year_s)
                month = MONTHS[month_name.capitalize()]
                if 0 < count < 100000:
                    out.setdefault((year, month), count)
    return [(year, month, out[(year, month)]) for year, month in sorted(out)]


def fetch_epiq_ch11_rows(start: date, end: date, *, max_pages: int = 20) -> list[dict]:
    roots = [
        "https://www.epiqglobal.com/en-us/resource-center/news",
        "https://www.epiqglobal.com/en-hk/resource-center/news",
        "https://www.epiqglobal.com/en-gb/resource-center/news",
    ]
    links: set[str] = set()
    for root in roots:
        empty_pages = 0
        for page in range(1, max_pages + 1):
            url = root if page == 1 else f"{root}?page={page}"
            try:
                response = _request(url)
            except Exception:
                empty_pages += 1
                if empty_pages >= 3:
                    break
                continue
            page_links = {
                urljoin(response.url, href)
                for href in re.findall(r'href=["\']([^"\']+)["\']', response.text, re.I)
                if "/resource-center/news/" in href
                and any(token in href.lower() for token in ("bankrupt", "chapter-11", "chapter-11s", "filing"))
            }
            links.update(page_links)
            empty_pages = empty_pages + 1 if not page_links else 0
            if empty_pages >= 3:
                break
    values: dict[tuple[int, int], tuple[int, str]] = {}
    for url in sorted(links):
        try:
            text = _plain_html(_request(url).text)
        except Exception:
            continue
        if not re.search(r"commercial\s+chapter\s+11", text, re.I):
            continue
        for year, month, count in _extract_epiq_ch11(text):
            observed = date(year, month, 1)
            if date(start.year, start.month, 1) <= observed <= end:
                values.setdefault((year, month), (count, url))
    return [_row("US_COMMERCIAL_CH11", y, m, count, f"EpiqAACER:{url}") for (y, m), (count, url) in sorted(values.items())]


def fetch_ecos_monthly_rows(code: str, stat_code: str, item_codes: Iterable[str], start: date, end: date) -> list[dict]:
    key = require_env("ECOS_API_KEY")
    params = "/".join(item_codes)
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/"
        f"{stat_code}/M/{start:%Y%m}/{end:%Y%m}/{params}"
    )
    payload = _request(url).json()
    source_rows = ((payload.get("StatisticSearch") or {}).get("row") or [])
    rows = []
    for item in source_rows:
        raw = str(item.get("TIME") or "")
        try:
            value = float(str(item.get("DATA_VALUE") or "").replace(",", ""))
        except ValueError:
            continue
        if len(raw) == 6:
            rows.append(_row(code, int(raw[:4]), int(raw[4:]), value, f"ECOS:{stat_code}/{params}"))
    return rows


def _extract_kdi_corporate_delinquency(text: str) -> tuple[int, int, float] | None:
    clean = re.sub(r"\s+", " ", text)
    month_match = re.search(r"(?:[‘'′’]?([12]?\d)\s*[.년]\s*)?(1[0-2]|0?[1-9])월말", clean)
    if not month_match:
        month_match = re.search(r"(20\d{2})년\s*(1[0-2]|0?[1-9])월말", clean)
        if not month_match:
            return None
    raw_year = month_match.group(1)
    month = int(month_match.group(2))
    if raw_year is None:
        return None
    year = int(raw_year)
    if year < 100:
        year += 2000
    value_match = re.search(
        r"기업대출(?:\(원화\))?\s*연체율(?:은|는)?\s*(?:현재\s*)?(?:\()?([0-9]+(?:\.[0-9]+)?)%",
        clean,
    )
    if not value_match:
        value_match = re.search(r"기업대출(?:\(원화\))?\s*연체율\(([0-9]+(?:\.[0-9]+)?)%\)", clean)
    if not value_match:
        return None
    return year, month, float(value_match.group(1))


def _fetch_kdi_corporate_delinquency_rows(start: date, end: date, *, max_pages: int = 12) -> list[dict]:
    root = "https://eiec.kdi.re.kr/policy/materialList.do"
    links: set[str] = set()
    for page in range(1, max_pages + 1):
        query = urlencode({"search_txt": "국내은행의 원화대출 연체율", "pg": page, "pp": 100})
        try:
            response = _request(f"{root}?{query}")
        except Exception:
            continue
        page_links = {
            urljoin(response.url, unescape(href))
            for href in re.findall(r'href=["\']([^"\']*materialView\.do\?[^"\']+)["\']', response.text, re.I)
            if "num=" in href
        }
        before = len(links)
        links.update(page_links)
        if page > 1 and len(links) == before:
            break
    values: dict[tuple[int, int], tuple[float, str]] = {}
    for url in sorted(links):
        try:
            text = _plain_html(_request(url).text)
        except Exception:
            continue
        parsed = _extract_kdi_corporate_delinquency(text)
        if not parsed:
            continue
        year, month, value = parsed
        observed = date(year, month, 1)
        if date(start.year, start.month, 1) <= observed <= end:
            values[(year, month)] = (value, url)
    return [_row("KR_CORP_DELINQ", y, m, value, f"FSS-KDI:{url}") for (y, m), (value, url) in sorted(values.items())]


def fetch_korea_business_delinquency_rows(start: date, end: date) -> list[dict]:
    rows: dict[tuple[int, int], dict] = {}
    if start < date(2019, 12, 1):
        for row in _fetch_kdi_corporate_delinquency_rows(start, min(end, date(2019, 11, 30))):
            year, month = map(int, row["observation_date"][:7].split("-"))
            rows[(year, month)] = row
    if end >= date(2019, 12, 1):
        available = max(start, date(2019, 12, 1))
        for row in fetch_ecos_monthly_rows("KR_CORP_DELINQ", "141Y005", ("R4AB00", "X00", "0960"), available, end):
            year, month = map(int, row["observation_date"][:7].split("-"))
            rows[(year, month)] = row
    return [rows[key] for key in sorted(rows)]


def fetch_korea_default_company_rows(start: date, end: date) -> list[dict]:
    return fetch_ecos_monthly_rows("KR_DEFAULT_COMPANIES", "801Y002", ("2100000",), start, end)
