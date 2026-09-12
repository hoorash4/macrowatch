"""Public Equifax/PayNet source for direct 31-180 delinquency and SBDFI levels.

Only values explicitly labelled as 31-180 delinquency are accepted. The 31-90 and
91-180 buckets are never added, combined, or used as a substitute.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO
import re

import requests
from pypdf import PdfReader

SERIES_DELINQUENCY = "US_SBDI_31_180"
SERIES_DEFAULT = "US_SBDFI"
USER_AGENT = "Mozilla/5.0 (compatible; MacroWatch/1.0)"
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
BASES = (
    "https://assets.equifax.com/marketing/US/assets/",
    "https://assets.equifax.com/assets/usis/",
)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    serial = year * 12 + month - 1 + delta
    return serial // 12, serial % 12 + 1


def _row(code: str, year: int, month: int, value: float, source: str) -> dict:
    return {
        "series_code": code,
        "observation_date": f"{year:04d}-{month:02d}-01",
        "value": float(value),
        "frequency": "M",
        "source": source,
    }


def _report_urls(year: int, month: int) -> list[str]:
    full = MONTHS[month - 1]
    low = full.lower()
    names = [
        f"main-street-lending-report-{low}-{year}.pdf",
        f"equifax-main-street-lending-report-{low}-{year}.pdf",
        f"Equifax.MainStreetLendingReport.{full}{year}.pdf",
        f"Equifax.MonthlyStrategicInsights.{full}{year}.pdf",
        f"EquifaxMonthlyStrategicInsights.{full}{year}.pdf",
        f"equifax-strategic-insights-{low}-{year}.pdf",
        f"equifax-small-business-indices-{low}-{year}.pdf",
        f"equifax-small-business-insights-{low}-{year}.pdf",
    ]
    return [base + name for base in BASES for name in names]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-"))


def _direct_31_180_level(text: str) -> float | None:
    """Read only an explicitly reported 31-180 delinquency level."""
    clean = _clean(text)
    patterns = (
        r"(?:SBDI|Small Business Delinquency Index)[^.%]{0,100}?31\s*-\s*180(?:\s*Days(?:\s*Past\s*Due)?)?[^.%]{0,120}?([0-9]+(?:\.[0-9]+)?)\s*%\s*\(Level\)",
        r"(?:SBDI|Small Business Delinquency Index)[^.!?]{0,140}?31\s*-\s*180(?:\s*Days(?:\s*Past\s*Due)?)?[^.!?]{0,140}?(?:to|at|is|was)\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        r"31\s*-\s*180(?:\s*Days(?:\s*Past\s*Due)?)?[^.!?]{0,120}?(?:SBDI[^.!?]{0,80}?)?([0-9]+(?:\.[0-9]+)?)\s*%\s*\(Level\)",
    )
    values: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, clean, re.I):
            value = float(match.group(1))
            if 0 <= value < 20:
                values.append(value)
    unique = sorted(set(values))
    if len(unique) > 1:
        raise RuntimeError(f"conflicting direct SBDI 31-180 levels in one report: {unique}")
    return unique[0] if unique else None


def _sbdfi_level(text: str) -> float | None:
    clean = _clean(text)
    patterns = (
        r"SBDFI\b[^.%]{0,120}?([0-9]+(?:\.[0-9]+)?)\s*%\s*\(Level\)",
        r"(?:Small Business Default Index\s*\(SBDFI\)|SBDFI\b|Defaults?\b)[^.!?]{0,160}?(?:to|at|is|was)\s*([0-9]+(?:\.[0-9]+)?)\s*%",
    )
    values: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, clean, re.I):
            value = float(match.group(1))
            if 0 <= value < 20:
                values.append(value)
    unique = sorted(set(values))
    if len(unique) > 1:
        raise RuntimeError(f"conflicting SBDFI levels in one report: {unique}")
    return unique[0] if unique else None


def extract_direct_levels(text: str) -> tuple[float | None, float | None]:
    """Return (direct 31-180 SBDI, SBDFI); never derive either value."""
    return _direct_31_180_level(text), _sbdfi_level(text)


def _fetch_report(report_year: int, report_month: int) -> tuple[float | None, float | None, str] | None:
    for url in _report_urls(report_year, report_month):
        try:
            response = requests.get(url, timeout=12, headers={"User-Agent": USER_AGENT})
            if response.status_code != 200 or "pdf" not in response.headers.get("content-type", "").lower():
                continue
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
            delinquency, default = extract_direct_levels(text)
            if delinquency is not None or default is not None:
                return delinquency, default, url
        except RuntimeError:
            raise
        except Exception:
            continue
    return None


def fetch_paynet_month(observed: date) -> dict[str, dict | None]:
    """Fetch one observation month from its public report; no split-bucket fallback."""
    month = date(observed.year, observed.month, 1)
    report_y, report_m = _shift_month(month.year, month.month, 2)
    found = _fetch_report(report_y, report_m)
    result: dict[str, dict | None] = {SERIES_DELINQUENCY: None, SERIES_DEFAULT: None}
    if found is None:
        return result
    delinquency, default, url = found
    if delinquency is not None:
        result[SERIES_DELINQUENCY] = _row(
            SERIES_DELINQUENCY, month.year, month.month, delinquency,
            f"Equifax-public:SBDI31-180:{url}",
        )
    if default is not None:
        result[SERIES_DEFAULT] = _row(
            SERIES_DEFAULT, month.year, month.month, default,
            f"Equifax-public:SBDFI:{url}",
        )
    return result


def fetch_paynet_rows(start: date, end: date) -> dict[str, list[dict]]:
    """Fetch direct public monthly levels, newest observation first."""
    first = date(start.year, start.month, 1)
    current = date(end.year, end.month, 1)
    result = {SERIES_DELINQUENCY: [], SERIES_DEFAULT: []}
    while current >= first:
        rows = fetch_paynet_month(current)
        for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
            if rows[code] is not None:
                result[code].append(rows[code])
        prev_y, prev_m = _shift_month(current.year, current.month, -1)
        current = date(prev_y, prev_m, 1)
    return result
