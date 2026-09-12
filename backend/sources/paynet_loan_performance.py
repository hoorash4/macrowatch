"""Public Equifax source for unified small-business delinquency and SBDFI.

The stored delinquency series is calculated from the two published, non-overlapping
Equifax buckets for the same observation month: 31-90 Days Past Due + 91-180 Days
Past Due. Only the summed result is emitted; the two component series are never stored.
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
MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
BASES = (
    "https://assets.equifax.com/marketing/US/assets/",
    "https://assets.equifax.com/assets/usis/",
)
# Public-PDF gaps only. Values are the three published Equifax levels
# (31-90, 91-180, SBDFI), never the stored unified result.
VERIFIED_RECENT = {
    date(2026, 7, 1): (1.72, 0.72, 3.20, "verified-2026-07"),
    date(2026, 6, 1): (1.71, 0.73, 3.26, "Coleman-Equifax-Aug-2026"),
    date(2026, 5, 1): (1.71, 0.73, 3.27, "Coleman-Equifax-Aug-2026-prior-month"),
    date(2026, 4, 1): (1.69, 0.73, 3.31, "Coleman-Equifax-Jun-2026"),
}


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    serial = year * 12 + month - 1 + delta
    return serial // 12, serial % 12 + 1


def _row(code: str, year: int, month: int, value: float, source: str) -> dict:
    return {
        "series_code": code,
        "observation_date": f"{year:04d}-{month:02d}-01",
        "value": round(float(value), 6),
        "frequency": "M",
        "source": source,
    }


def _report_urls(year: int, month: int) -> list[str]:
    full = MONTHS[month - 1]
    low = full.lower()
    abbr = MONTH_ABBR[month - 1]
    abbr_low = abbr.lower()
    names = [
        f"main-street-lending-report-{low}-{year}.pdf",
        f"main-street-lending-report-{abbr_low}-{year}.pdf",
        f"main-street-lending-report-{abbr}-{year}.pdf",
        f"Main-Street-Lending-Report-{full}-{year}.pdf",
        f"Main-Street-Lending-Report-{full}{year}.pdf",
        f"main-street-lending-report-{full}-{year}.pdf",
        f"main-street-lending-report-{full}{year}.pdf",
        f"main-street-lending-report-{abbr}-{year}-report.pdf",
        f"main-street-lending-report-{abbr_low}-{year}-report.pdf",
        f"Main-Street-Lending-Report-{abbr}-{year}-report.pdf",
        f"equifax-main-street-lending-report-{low}-{year}.pdf",
        f"equifax-main-street-lending-report-{abbr_low}-{year}.pdf",
        f"Equifax-main-street-lending-report-{full}-{year}.pdf",
        f"Equifax-Main-Street-Lending-Report-{full}-{year}.pdf",
        f"Equifax-Main-Street-Lending-Report-{full}{year}.pdf",
        f"Equifax-main-street-lending-report-{abbr}-{year}-report.pdf",
        f"Equifax-Main-Street-Lending-Report-{abbr}-{year}-report.pdf",
        f"Equifax.MainStreetLendingReport.{full}{year}.pdf",
        f"Equifax.MonthlyStrategicInsights.{full}{year}.pdf",
        f"Equifax.MonthlyStrategicInsights.{full}{year}.V101.pdf",
        f"EquifaxMonthlyStrategicInsights.{full}{year}.pdf",
        f"commercial-lending-trends-{low}-{year}.pdf",
        f"commercial-lending-trends-{abbr_low}-{year}.pdf",
        f"equifax-commercial-lending-trends-{low}-{year}.pdf",
        f"equifax-small-business-indices-{low}-{year}.pdf",
        f"equifax-small-business-insights-{low}-{year}.pdf",
    ]
    return [base + name for base in BASES for name in names]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-"))


def _metric_token(label: str) -> str:
    normalized = re.sub(r"\s+", " ", label.upper())
    if normalized.startswith("SBDFI"):
        return "default"
    if re.search(r"SBDI\s*31\s*-\s*90", normalized):
        return "short"
    if re.search(r"SBDI\s*91\s*-\s*180", normalized):
        return "severe"
    raise ValueError(label)


def _modern_table_levels(clean: str) -> tuple[float, float, float] | None:
    header = re.compile(
        r"(?P<label>SBDFI\b|SBDI\s*31\s*-\s*90\s*Days(?:\s*Past\s*Due)?|SBDI\s*91\s*-\s*180\s*Days(?:\s*Past\s*Due)?)",
        re.I,
    )
    matches = list(header.finditer(clean))
    for i in range(len(matches) - 2):
        trio = matches[i:i + 3]
        tokens = [_metric_token(m.group("label")) for m in trio]
        if set(tokens) != {"short", "severe", "default"}:
            continue
        if trio[-1].end() - trio[0].start() > 320:
            continue
        tail = clean[trio[-1].end():trio[-1].end() + 900]
        levels = [float(v) for v in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*\(Level\)", tail, re.I)[:3]]
        if len(levels) != 3 or any(not (0 <= v < 10) for v in levels):
            continue
        mapped = dict(zip(tokens, levels))
        return mapped["short"], mapped["severe"], mapped["default"]
    return None


def _local_level(clean: str, label_pattern: str) -> float | None:
    for label in re.finditer(label_pattern, clean, re.I):
        next_metric = re.search(
            r"SBDFI\b|SBDI\s*31\s*-\s*90\s*Days(?:\s*Past\s*Due)?|SBDI\s*91\s*-\s*180\s*Days(?:\s*Past\s*Due)?|SBLI\b",
            clean[label.end():], re.I,
        )
        end = label.end() + (next_metric.start() if next_metric else 240)
        body = clean[label.end():min(len(clean), end)]
        level = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*\(Level\)", body, re.I)
        if level:
            value = float(level.group(1))
            if 0 <= value < 10:
                return value
        percentages = [float(v) for v in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", body)]
        if len(percentages) == 1 and 0 <= percentages[0] < 10:
            return percentages[0]
    return None


def extract_equifax_levels(text: str) -> tuple[float, float, float] | None:
    """Return exact published (31-90, 91-180, SBDFI) levels from one report."""
    clean = _clean(text)
    modern = _modern_table_levels(clean)
    if modern is not None:
        return modern

    short = _local_level(clean, r"SBDI\s*31\s*-\s*90\s*Days(?:\s*Past\s*Due)?")
    severe = _local_level(clean, r"SBDI\s*91\s*-\s*180\s*Days(?:\s*Past\s*Due)?")
    default = _local_level(clean, r"SBDFI\b")
    if short is not None and severe is not None and default is not None:
        return short, severe, default

    short_m = re.search(
        r"SBDI\)?\s*31\s*-\s*90\s*Days\s*Past\s*Due[^.]{0,180}?(?:to|at)\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        clean, re.I,
    )
    severe_m = re.search(
        r"SBDI\s*91\s*-\s*180\s*Days\s*Past\s*Due[^.]{0,180}?(?:to|at)\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        clean, re.I,
    )
    default_m = re.search(
        r"(?:Small Business Default Index\s*\(SBDFI\)|SBDFI\b|Defaults?\b)[^.]{0,180}?(?:to|at|is|was)\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        clean, re.I,
    )
    if short_m and severe_m and default_m:
        return float(short_m.group(1)), float(severe_m.group(1)), float(default_m.group(1))
    return None


def _fetch_report(report_year: int, report_month: int) -> tuple[float, float, float, str] | None:
    for url in _report_urls(report_year, report_month):
        try:
            response = requests.get(url, timeout=12, headers={"User-Agent": USER_AGENT})
            if response.status_code != 200 or "pdf" not in response.headers.get("content-type", "").lower():
                continue
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
            levels = extract_equifax_levels(text)
            if levels is not None:
                return levels[0], levels[1], levels[2], url
        except RuntimeError:
            raise
        except Exception:
            continue
    return None


def fetch_paynet_month(observed: date) -> dict[str, dict | None]:
    """Fetch one month, sum the two published delinquency buckets, and emit only final rows."""
    month = date(observed.year, observed.month, 1)
    report_y, report_m = _shift_month(month.year, month.month, 2)
    found = _fetch_report(report_y, report_m)
    result: dict[str, dict | None] = {SERIES_DELINQUENCY: None, SERIES_DEFAULT: None}

    if found is not None:
        short, severe, default, source = found
    elif month in VERIFIED_RECENT:
        short, severe, default, source = VERIFIED_RECENT[month]
    else:
        return result

    result[SERIES_DELINQUENCY] = _row(
        SERIES_DELINQUENCY, month.year, month.month, short + severe,
        f"Equifax-public:SBDI31-90+91-180:{source}",
    )
    result[SERIES_DEFAULT] = _row(
        SERIES_DEFAULT, month.year, month.month, default,
        f"Equifax-public:SBDFI:{source}",
    )
    return result


def fetch_paynet_rows(start: date, end: date) -> dict[str, list[dict]]:
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
