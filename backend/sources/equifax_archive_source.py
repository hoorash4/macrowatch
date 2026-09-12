"""Broader first-party Equifax PDF archive discovery for monthly small-business risk indices."""
from __future__ import annotations

from datetime import date
from io import BytesIO
import re

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


def _fallback_levels(text: str) -> tuple[float, float, float] | None:
    levels = _extract_equifax_levels(text)
    if levels:
        return levels
    clean = re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-"))

    def find(label: str) -> float | None:
        match = re.search(
            rf"{label}.{{0,220}}?(?:fell|declined|decreased|edged|rose|increased|climbed|held\s+steady|remained|was|is|stands?|stood)?[^.;:]{{0,100}}?(?:to|at|is|was|of)?\s*([0-9]+(?:\.[0-9]+)?)%",
            clean,
            re.I,
        )
        if match:
            return float(match.group(1))
        match = re.search(rf"{label}.{{0,120}}?([0-9]+(?:\.[0-9]+)?)%\s*\(Level\)", clean, re.I)
        return float(match.group(1)) if match else None

    short = find(r"SBDI(?:\)|:)?\s*31\s*-\s*90(?:\s*Days(?:\s*Past\s*Due)?)?")
    severe = find(r"SBDI(?:\)|:)?\s*91\s*-\s*180(?:\s*Days(?:\s*Past\s*Due)?)?")
    default = find(r"(?:Small\s+Business\s+Default\s+Index\s*\(SBDFI\)|SBDFI\b|Defaults?\b)")
    if short is None or severe is None or default is None:
        return None
    return short, severe, default


def fetch_equifax_archive_rows(start: date, end: date) -> dict[str, list[dict]]:
    """Recover exact reported levels from additional known first-party PDF naming schemes."""
    result = {"US_SBDI_31_90": [], "US_SBDI_91_180": [], "US_SBDFI": []}
    report_y, report_m = _shift_month(start.year, start.month, 2)
    end_y, end_m = _shift_month(end.year, end.month, 2)
    while (report_y, report_m) <= (end_y, end_m):
        levels = None
        used_url = None
        for url in _archive_urls(report_y, report_m):
            try:
                response = requests.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
                content_type = response.headers.get("content-type", "").lower()
                if response.status_code != 200 or "pdf" not in content_type:
                    continue
                text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
                levels = _fallback_levels(text)
                if levels:
                    used_url = url
                    break
            except Exception:
                continue
        if levels and used_url:
            obs_y, obs_m = _shift_month(report_y, report_m, -2)
            observed = date(obs_y, obs_m, 1)
            if date(start.year, start.month, 1) <= observed <= end:
                short, severe, default = levels
                result["US_SBDI_31_90"].append(_row("US_SBDI_31_90", obs_y, obs_m, short, f"Equifax:SBDI31-90:{used_url}"))
                result["US_SBDI_91_180"].append(_row("US_SBDI_91_180", obs_y, obs_m, severe, f"Equifax:SBDI91-180:{used_url}"))
                result["US_SBDFI"].append(_row("US_SBDFI", obs_y, obs_m, default, f"Equifax:SBDFI:{used_url}"))
        report_y, report_m = _shift_month(report_y, report_m, 1)
    return result
