"""Monthly business-credit and insolvency source adapters used by economic charts."""
from __future__ import annotations

from datetime import date
from html import unescape
from io import BytesIO
import re
from typing import Iterable
from urllib.parse import urljoin

import requests
from pypdf import PdfReader

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


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    serial = year * 12 + month - 1 + delta
    return serial // 12, serial % 12 + 1


def _equifax_urls(year: int, month: int) -> list[str]:
    full = list(MONTHS)[month - 1]
    low = full.lower()
    base = "https://assets.equifax.com/marketing/US/assets/"
    legacy = "https://assets.equifax.com/assets/usis/"
    return [
        f"{base}main-street-lending-report-{low}-{year}.pdf",
        f"{base}commercial-lending-trends-{low}-{year}.pdf",
        f"{base}Equifax.MainStreetLendingReport.{full}{year}.pdf",
        f"{base}Equifax.MonthlyStrategicInsights.{full}{year}.pdf",
        f"{base}equifax-small-business-indices-{low}-{year}.pdf",
        f"{legacy}equifax-small-business-indices-{low}-{year}.pdf",
    ]


def _extract_equifax_levels(text: str) -> tuple[float, float, float] | None:
    clean = re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-"))
    new = re.search(
        r"SBDI\s*31\s*-\s*90\s*Days.*?([0-9]+(?:\.[0-9]+)?)%\s*\(Level\).*?"
        r"SBDI\s*91\s*-\s*180\s*Days.*?([0-9]+(?:\.[0-9]+)?)%\s*\(Level\).*?"
        r"SBDFI.*?([0-9]+(?:\.[0-9]+)?)%\s*\(Level\)", clean, re.I,
    )
    if new:
        return tuple(map(float, new.groups()))
    short = re.search(
        r"SBDI\)?\s*31\s*-\s*90\s*Days\s*Past\s*Due.*?(?:to|at)\s*([0-9]+(?:\.[0-9]+)?)%", clean, re.I,
    )
    severe = re.search(
        r"SBDI\s*91\s*-\s*180\s*Days\s*Past\s*Due.*?(?:to|at)\s*([0-9]+(?:\.[0-9]+)?)%", clean, re.I,
    )
    default = re.search(r"Defaults?\b.{0,100}?(?:to|at)\s*([0-9]+(?:\.[0-9]+)?)%", clean, re.I)
    if not severe:
        severe = re.search(
            r"SBDI:\s*91\s*-\s*180\s*Days\s*Past\s*Due\s*SBDI:\s*31\s*-\s*90\s*Days\s*Past\s*Due\s*([0-9]+(?:\.[0-9]+)?)%",
            clean, re.I,
        )
    if short and severe and default:
        return float(short.group(1)), float(severe.group(1)), float(default.group(1))
    header = re.search(
        r"([0-9]+(?:\.[0-9]+)?)%.*?Delinquent Percentage.*?SBDI:\s*91\s*-\s*180.*?SBDI:\s*31\s*-\s*90.*?([0-9]+(?:\.[0-9]+)?)%",
        clean, re.I,
    )
    if header and default:
        return float(header.group(1)), float(header.group(2)), float(default.group(1))
    return None


def fetch_equifax_rows(start: date, end: date) -> dict[str, list[dict]]:
    """Fetch exact monthly public Equifax report levels. Missing report months are skipped."""
    result = {"US_SBDI_31_90": [], "US_SBDI_91_180": [], "US_SBDFI": []}
    report_y, report_m = _shift_month(start.year, start.month, 2)
    end_y, end_m = _shift_month(end.year, end.month, 2)
    while (report_y, report_m) <= (end_y, end_m):
        parsed = None
        used_url = None
        for url in _equifax_urls(report_y, report_m):
            try:
                response = requests.get(url, timeout=25, headers={"User-Agent": USER_AGENT})
                if response.status_code != 200 or "pdf" not in response.headers.get("content-type", "").lower():
                    continue
                text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
                parsed = _extract_equifax_levels(text)
                if parsed:
                    used_url = url
                    break
            except Exception:
                continue
        if parsed and used_url:
            obs_y, obs_m = _shift_month(report_y, report_m, -2)
            observed = date(obs_y, obs_m, 1)
            if date(start.year, start.month, 1) <= observed <= end:
                a, b, c = parsed
                result["US_SBDI_31_90"].append(_row("US_SBDI_31_90", obs_y, obs_m, a, f"Equifax:SBDI31-90:{used_url}"))
                result["US_SBDI_91_180"].append(_row("US_SBDI_91_180", obs_y, obs_m, b, f"Equifax:SBDI91-180:{used_url}"))
                result["US_SBDFI"].append(_row("US_SBDFI", obs_y, obs_m, c, f"Equifax:SBDFI:{used_url}"))
        report_y, report_m = _shift_month(report_y, report_m, 1)
    return result


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
    root = "https://www.epiqglobal.com/en-us/resource-center/news"
    links: set[str] = set()
    for page in range(1, max_pages + 1):
        url = root if page == 1 else f"{root}?page={page}"
        try:
            response = _request(url)
        except Exception:
            continue
        page_links = {
            urljoin(response.url, href)
            for href in re.findall(r'href=["\']([^"\']+)["\']', response.text, re.I)
            if "/resource-center/news/" in href
            and any(token in href.lower() for token in ("bankrupt", "chapter-11", "chapter-11s", "filing"))
        }
        links.update(page_links)
        if page > 1 and not page_links:
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


def fetch_korea_business_delinquency_rows(start: date, end: date) -> list[dict]:
    available = max(start, date(2019, 12, 1))
    return fetch_ecos_monthly_rows("KR_CORP_DELINQ", "141Y005", ("R4AB00", "X00", "0960"), available, end)


def fetch_korea_default_company_rows(start: date, end: date) -> list[dict]:
    return fetch_ecos_monthly_rows("KR_DEFAULT_COMPANIES", "801Y002", ("2100000",), start, end)
