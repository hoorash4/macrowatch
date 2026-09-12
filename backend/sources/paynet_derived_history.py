"""Derived Equifax/PayNet history fallback for gaps in direct monthly reports."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import unescape
from io import BytesIO
import re
from urllib.parse import urljoin

import requests
from pypdf import PdfReader

from sources.paynet_loan_performance import (
    SERIES_DEFAULT,
    SERIES_DELINQUENCY,
    USER_AGENT,
    _clean,
    _report_urls,
    _row,
    _shift_month,
    extract_equifax_levels,
)

_METRICS = (
    ("short", r"SBDI\s*31\s*-\s*90\s*Days(?:\s*Past\s*Due)?"),
    ("severe", r"SBDI\s*91\s*-\s*180\s*Days(?:\s*Past\s*Due)?"),
    ("default", r"SBDFI\b|Small\s+Business\s+Default\s+Index"),
)
_MONTHS = {
    name: i for i, name in enumerate(
        "January February March April May June July August September October November December".split(), 1
    )
}
_EFA_BASE = "https://www.equipmentfa.com"
_EFA_INDEXES = (
    "https://www.equipmentfa.com/industry-data/source/729/paynet-inc",
    "https://www.equipmentfa.com/industry-data/category/145/Small%2BBusiness%2BLending%2BReports",
)
_ARCHIVE_CACHE: dict[tuple[int, int], list[str]] | None = None


def _pdf_text(url: str, timeout: float = 6) -> tuple[tuple[float, float, float] | None, str, str] | None:
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200 or "pdf" not in response.headers.get("content-type", "").lower():
            return None
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
        clean = _clean(text)
        levels = extract_equifax_levels(text)
        if levels is None and not re.search(r"SBDI|SBDFI|Delinquency|Default", clean, re.I):
            return None
        return levels, clean, url
    except Exception:
        return None


def _fetch_one(url: str) -> tuple[tuple[float, float, float] | None, str, str] | None:
    return _pdf_text(url, 4)


def _archive_links() -> dict[tuple[int, int], list[str]]:
    """Discover old PayNet chart/release PDFs mirrored by Equipment Finance Advisor."""
    global _ARCHIVE_CACHE
    if _ARCHIVE_CACHE is not None:
        return _ARCHIVE_CACHE

    details: dict[tuple[int, int], set[str]] = {}
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    for index_url in _EFA_INDEXES:
        try:
            html = session.get(index_url, timeout=12).text
        except Exception:
            continue
        # Detail URLs include the observation month in their visible title/slug.
        for href, label in re.findall(r'href=["\']([^"\']*?/industry-data/\d+/[^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
            text = re.sub(r"<[^>]+>", " ", unescape(label))
            text = re.sub(r"\s+", " ", text).strip()
            m = re.search(r"\b(" + "|".join(_MONTHS) + r")\s+(20\d{2})\b", text, re.I)
            if not m:
                m = re.search(r"-(" + "|".join(_MONTHS) + r")-(20\d{2})(?:\b|-)", href, re.I)
            if not m:
                continue
            month_name, year_s = m.group(1).capitalize(), m.group(2)
            details.setdefault((int(year_s), _MONTHS[month_name]), set()).add(urljoin(_EFA_BASE, href))

    out: dict[tuple[int, int], list[str]] = {}
    for key, urls in details.items():
        pdfs: list[str] = []
        for detail_url in urls:
            try:
                html = session.get(detail_url, timeout=10).text
            except Exception:
                continue
            for href in re.findall(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', html, re.I):
                full = urljoin(detail_url, unescape(href))
                if "IndustryData" in full or "paynet" in full.lower():
                    pdfs.append(full)
        if pdfs:
            # Charts before press releases because charts consistently include both SBDI buckets.
            pdfs.sort(key=lambda u: ("graph" not in u.lower() and "chart" not in u.lower(), u))
            out[key] = list(dict.fromkeys(pdfs))
    _ARCHIVE_CACHE = out
    return out


def _fetch_archive_report(observed_year: int, observed_month: int) -> tuple[tuple[float, float, float] | None, str, str] | None:
    urls = _archive_links().get((observed_year, observed_month), [])
    for url in urls:
        found = _pdf_text(url, 8)
        if found is not None:
            return found
    return None


def _fetch_report_text(report_year: int, report_month: int, *, observed_year: int | None = None, observed_month: int | None = None) -> tuple[tuple[float, float, float] | None, str, str] | None:
    urls = _report_urls(report_year, report_month)
    with ThreadPoolExecutor(max_workers=18) as pool:
        futures = [pool.submit(_fetch_one, url) for url in urls]
        for future in as_completed(futures):
            found = future.result()
            if found is not None:
                for pending in futures:
                    pending.cancel()
                return found
    if observed_year is not None and observed_month is not None:
        return _fetch_archive_report(observed_year, observed_month)
    return None


def _to_pp(symbol: str | None, number: str, unit: str) -> float:
    value = float(number)
    if unit.lower().startswith("bp"):
        value /= 100.0
    if symbol in ("▼", "-"):
        return -value
    return value


def _directional_delta(body: str, period: str) -> float | None:
    aliases = r"(?:Y\s*/\s*Y|YoY|Y-O-Y|year[- ]over[- ]year|over\s+the\s+last\s+12\s+months?|over\s+the\s+past\s+year)" if period.upper() == "Y/Y" else r"(?:M\s*/\s*M|MoM|M-O-M|month[- ]over[- ]month)"
    patterns = (
        rf"([▲▼+\-])\s*([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?)\s*\(?\s*{aliases}\s*\)?",
        rf"\(?\s*{aliases}\s*\)?\s*([▲▼+\-])\s*([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?)",
    )
    for pattern in patterns:
        match = re.search(pattern, body, re.I)
        if match:
            return _to_pp(match.group(1), match.group(2), match.group(3))

    if period.upper() == "Y/Y":
        narrative = re.search(
            r"(?:up|increased|rose|higher)\s+(?:by\s+)?([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?).{0,70}?(?:year|12\s+months)|"
            r"(?:down|decreased|fell|lower)\s+(?:by\s+)?([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?).{0,70}?(?:year|12\s+months)",
            body, re.I,
        )
        if narrative:
            if narrative.group(1):
                return _to_pp("+", narrative.group(1), narrative.group(2))
            return _to_pp("-", narrative.group(3), narrative.group(4))
    return None


def _metric_level_and_delta(clean: str, token: str, period: str) -> tuple[float, float] | None:
    if token == "short":
        pat = r"SBDI(?:\)|:)?\s*31\s*[-–]\s*90\s*(?:Days(?:\s*Past\s*Due)?)?"
    elif token == "severe":
        pat = r"SBDI(?:\)|:)?\s*91\s*[-–]\s*180\s*(?:Days(?:\s*Past\s*Due)?)?"
    else:
        pat = r"SBDFI\b|Small\s+Business\s+Default\s+Index"
    for m in re.finditer(pat, clean, re.I):
        body = clean[m.end():m.end() + 550]
        level_m = re.search(r"(?:to|at|is|was|of)?\s*([0-9]+(?:\.[0-9]+)?)\s*%", body, re.I)
        delta = _directional_delta(body, period)
        if level_m and delta is not None:
            level = float(level_m.group(1))
            if 0 <= level < 10:
                return level, delta
    return None


def _derive_from_report(target: date, *, lag_months: int, period: str) -> dict[str, dict | None]:
    observed_y, observed_m = _shift_month(target.year, target.month, lag_months)
    report_y, report_m = _shift_month(observed_y, observed_m, 2)
    fetched = _fetch_report_text(report_y, report_m, observed_year=observed_y, observed_month=observed_m)
    result: dict[str, dict | None] = {SERIES_DELINQUENCY: None, SERIES_DEFAULT: None}
    if fetched is None:
        return result
    levels, clean, url = fetched

    # Prefer narrative/box current level + published YoY/MoM delta. This works on
    # both old PayNet Strategic Insights charts and newer Equifax reports.
    short_pair = _metric_level_and_delta(clean, "short", period)
    severe_pair = _metric_level_and_delta(clean, "severe", period)
    default_pair = _metric_level_and_delta(clean, "default", period)
    if short_pair and severe_pair:
        short = round(short_pair[0] - short_pair[1], 6)
        severe = round(severe_pair[0] - severe_pair[1], 6)
        provenance = f"PayNet-public:derived-{period.replace('/', '')}:{url}"
        result[SERIES_DELINQUENCY] = _row(
            SERIES_DELINQUENCY, target.year, target.month, short + severe,
            f"{provenance}:SBDI31-90={short:.2f}+SBDI91-180={severe:.2f}",
        )
    if default_pair:
        default = round(default_pair[0] - default_pair[1], 6)
        result[SERIES_DEFAULT] = _row(
            SERIES_DEFAULT, target.year, target.month, default,
            f"PayNet-public:derived-{period.replace('/', '')}:{url}:SBDFI={default:.2f}",
        )
    if result[SERIES_DELINQUENCY] is not None and result[SERIES_DEFAULT] is not None:
        return result

    # Newer reports sometimes parse cleanly as a three-level overview; use deltas
    # nearby when available.
    if levels is not None:
        short_now, severe_now, default_now = levels
        pairs = {
            "short": _metric_level_and_delta(clean, "short", period),
            "severe": _metric_level_and_delta(clean, "severe", period),
            "default": _metric_level_and_delta(clean, "default", period),
        }
        if pairs["short"] and pairs["severe"] and result[SERIES_DELINQUENCY] is None:
            short = short_now - pairs["short"][1]
            severe = severe_now - pairs["severe"][1]
            result[SERIES_DELINQUENCY] = _row(SERIES_DELINQUENCY, target.year, target.month, short + severe, f"Equifax-public:derived-{period.replace('/', '')}:{url}")
        if pairs["default"] and result[SERIES_DEFAULT] is None:
            default = default_now - pairs["default"][1]
            result[SERIES_DEFAULT] = _row(SERIES_DEFAULT, target.year, target.month, default, f"Equifax-public:derived-{period.replace('/', '')}:{url}")
    return result


def fetch_paynet_derived_month(observed: date) -> dict[str, dict | None]:
    target = date(observed.year, observed.month, 1)
    yoy = _derive_from_report(target, lag_months=12, period="Y/Y")
    if yoy[SERIES_DELINQUENCY] is not None and yoy[SERIES_DEFAULT] is not None:
        return yoy
    mom = _derive_from_report(target, lag_months=1, period="M/M")
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        if yoy[code] is None:
            yoy[code] = mom[code]
    return yoy
