"""Broader first-party Equifax PDF archive discovery for monthly small-business risk indices."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from io import BytesIO

import requests
from pypdf import PdfReader

from sources.business_credit_monthly import MONTHS, USER_AGENT, _extract_equifax_levels, _row, _shift_month

BASES = (
    "https://assets.equifax.com/marketing/US/assets/",
    "https://assets.equifax.com/assets/usis/",
)
MONTH_ABBR = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _archive_urls(year: int, month: int) -> list[str]:
    full = list(MONTHS)[month - 1]
    low = full.lower()
    abbr = MONTH_ABBR[month - 1]
    names = [
        f"main-street-lending-report-{low}-{year}.pdf",
        f"equifax-main-street-lending-report-{low}-{year}.pdf",
        f"commercial-lending-trends-{low}-{year}.pdf",
        f"equifax-commercial-lending-trends-{low}-{year}.pdf",
        f"Equifax.MainStreetLendingReport.{full}{year}.pdf",
        f"Equifax.CommercialLendingTrends.{full}{year}.pdf",
        f"Equifax.MonthlyStrategicInsights.{full}{year}.pdf",
        f"Equifax.MonthlyStrategicInsights.{full}{year}.V101.pdf",
        f"equifax-small-business-indices-{low}-{year}.pdf",
        f"monthly-small-business-indices-{low}-{year}.pdf",
        f"equifax-strategic-insights-{low}-{year}.pdf",
        f"strategic-insights-{low}-{year}.pdf",
        f"small-business-indices-deck-{low}-{year}.pdf",
        f"market-pulse-webinar-deck-{abbr}-{year}.pdf",
        f"market-pulse-webinar-deck-{low}-{year}.pdf",
    ]
    return [base + name for base in BASES for name in names]


def _fetch_report(report_y: int, report_m: int) -> tuple[int, int, tuple[float, float, float], str] | None:
    for url in _archive_urls(report_y, report_m):
        try:
            response = requests.get(url, timeout=8, headers={"User-Agent": USER_AGENT})
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code != 200 or "pdf" not in content_type:
                continue
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
            levels = _extract_equifax_levels(text)
            if levels:
                return report_y, report_m, levels, url
        except Exception:
            continue
    return None


def fetch_equifax_archive_rows(start: date, end: date) -> dict[str, list[dict]]:
    """Recover only levels accepted by the canonical strict Equifax parser."""
    result = {"US_SBDI_31_90": [], "US_SBDI_91_180": [], "US_SBDFI": []}
    report_y, report_m = _shift_month(start.year, start.month, 2)
    end_y, end_m = _shift_month(end.year, end.month, 2)
    reports: list[tuple[int, int]] = []
    while (report_y, report_m) <= (end_y, end_m):
        reports.append((report_y, report_m))
        report_y, report_m = _shift_month(report_y, report_m, 1)

    discoveries = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(_fetch_report, year, month): (year, month) for year, month in reports}
        for future in as_completed(futures):
            found = future.result()
            if found is not None:
                discoveries.append(found)

    for report_y, report_m, levels, used_url in sorted(discoveries):
        obs_y, obs_m = _shift_month(report_y, report_m, -2)
        observed = date(obs_y, obs_m, 1)
        if date(start.year, start.month, 1) <= observed <= end:
            short, severe, default = levels
            result["US_SBDI_31_90"].append(_row("US_SBDI_31_90", obs_y, obs_m, short, f"Equifax:SBDI31-90:{used_url}"))
            result["US_SBDI_91_180"].append(_row("US_SBDI_91_180", obs_y, obs_m, severe, f"Equifax:SBDI91-180:{used_url}"))
            result["US_SBDFI"].append(_row("US_SBDFI", obs_y, obs_m, default, f"Equifax:SBDFI:{used_url}"))
    return result
