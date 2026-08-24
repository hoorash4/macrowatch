"""Collect monthly U.S. credit-stress indicators without retaining source files."""

from __future__ import annotations

import argparse
import calendar
import io
import os
import re
from collections import defaultdict
from datetime import date

import openpyxl
import requests


FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
COURTS_URLS = (
    "https://www.uscourts.gov/sites/default/files/document/bf_f2.1_{period}.xlsx",
    "https://www.uscourts.gov/sites/default/files/data_tables/bf_f2.1_{period}.xlsx",
    "https://www.uscourts.gov/sites/default/files/{publication_year}-{publication_month:02d}/bf_f2.1_{period}.xlsx",
)
HIGH_YIELD_SERIES = "BAMLH0A0HYM2"
FINANCIAL_CONDITIONS_SERIES = "NFCICREDIT"
MONTH_PATTERN = re.compile(r"Ending\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})")
TIMEOUT_SECONDS = 45
INDEX_HISTORY_YEARS = 3
STRESS_COMPONENTS = (
    ("high_yield_oas_pct", 0.50),
    ("financial_conditions_credit_index", 0.30),
    ("business_bankruptcy_filings", 0.20),
)
# Fixed 0-to-100 reference ranges. These never roll with incoming data; values
# above the reference range deliberately remain above 100 to preserve stress
# severity during future extremes.
FIXED_COMPONENT_SCALES = {
    "high_yield_oas_pct": (2.0, 20.0),
    "financial_conditions_credit_index": (-0.5, 2.0),
    "business_bankruptcy_filings": (1000.0, 5000.0),
}


def month_start(value: date) -> str:
    return value.replace(day=1).isoformat()


def quarter_ends(start_year: int, start_month: int, end_year: int, end_month: int):
    cursor_year, cursor_month = start_year, ((start_month - 1) // 3 + 1) * 3
    while (cursor_year, cursor_month) <= (end_year, end_month):
        yield cursor_year, cursor_month, calendar.monthrange(cursor_year, cursor_month)[1]
        cursor_month += 3
        if cursor_month > 12:
            cursor_year += 1
            cursor_month = 3


def latest_completed_quarter_end(today: date) -> date:
    quarter_start_month = ((today.month - 1) // 3) * 3 + 1
    if quarter_start_month == 1:
        return date(today.year - 1, 12, 31)
    previous_month = quarter_start_month - 1
    return date(today.year, previous_month, calendar.monthrange(today.year, previous_month)[1])


def fetch_fred_monthly(series_id: str, api_key: str, start: date, end: date) -> dict[str, float]:
    response = requests.get(
        FRED_URL,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
            "limit": "100000",
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    values: dict[str, list[float]] = defaultdict(list)
    for observation in response.json().get("observations", []):
        raw_value = observation.get("value")
        if raw_value in (None, "."):
            continue
        try:
            values[observation["date"][:7] + "-01"].append(float(raw_value))
        except (KeyError, TypeError, ValueError):
            continue
    return {month: sum(rows) / len(rows) for month, rows in values.items() if rows}


def fetch_court_workbook(year: int, month: int, day: int) -> bytes:
    period = f"{month:02d}{day:02d}.{year}"
    publication_year, publication_month = year, month + 1
    if publication_month == 13:
        publication_year, publication_month = year + 1, 1
    for template in COURTS_URLS:
        response = requests.get(
            template.format(
                period=period,
                publication_year=publication_year,
                publication_month=publication_month,
            ),
            timeout=TIMEOUT_SECONDS,
        )
        if response.ok:
            return response.content
    raise RuntimeError(f"법원 F-2 월간 XLSX를 찾지 못했습니다: {year}-{month:02d}")


def parse_business_filings(workbook_bytes: bytes) -> dict[str, int]:
    workbook = openpyxl.load_workbook(io.BytesIO(workbook_bytes), read_only=True, data_only=True)
    filings: dict[str, int] = {}
    for sheet in workbook.worksheets:
        # F-2 also contains chapter-detail sheets named "(9, 12, 15)".
        # The monthly total is only present in the base F-2 sheet.
        if "(" in sheet.title:
            continue
        title = str(sheet.cell(2, 1).value or "")
        match = MONTH_PATTERN.search(title)
        if not match:
            continue
        total_row = next(
            (row for row in range(1, sheet.max_row + 1) if str(sheet.cell(row, 1).value or "").strip() == "Total"),
            None,
        )
        if total_row is None:
            raise RuntimeError(f"법원 F-2 표에서 Total 행을 찾지 못했습니다: {sheet.title}")
        value = sheet.cell(total_row, 7).value  # Predominant Nature of Debt: Business, All Chapters
        try:
            filing_count = int(str(value).replace(",", ""))
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"법원 F-2 사업체 파산보호 건수가 올바르지 않습니다: {sheet.title}") from error
        month_number = list(calendar.month_name).index(match.group(1))
        filings[f"{match.group(3)}-{month_number:02d}-01"] = filing_count
    if not filings:
        raise RuntimeError("법원 F-2 XLSX에서 월별 사업체 파산보호 신청 건수를 찾지 못했습니다.")
    return filings


def collect_business_filings(start: date, end: date) -> dict[str, int]:
    filings: dict[str, int] = {}
    for year, month, day in quarter_ends(start.year, start.month, end.year, end.month):
        try:
            workbook = fetch_court_workbook(year, month, day)
        except RuntimeError as error:
            # The court publishes each three-month report after its period ends.
            # Keep the available history intact while a newly expected report is pending.
            print(f"skipped_court_report={year}-{month:02d} reason={error}")
            continue
        for month_key, value in parse_business_filings(workbook).items():
            if start.isoformat()[:7] <= month_key[:7] <= end.isoformat()[:7]:
                filings[month_key] = value
    return filings


def upsert_rows(rows: list[dict[str, object]], supabase_url: str, service_role_key: str) -> None:
    response = requests.post(
        f"{supabase_url.rstrip('/')}/rest/v1/us_credit_stress_monthly?on_conflict=month",
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=rows,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def fixed_stress_score(value: float, key: str) -> float:
    floor, reference = FIXED_COMPONENT_SCALES[key]
    return max(0.0, ((value - floor) / (reference - floor)) * 100.0)


def smoothed_filings(rows: list[dict[str, object]]) -> tuple[dict[str, float], set[str]]:
    """Return a trailing filing average and the months backed by published reports."""
    observed: list[float] = []
    values: dict[str, float] = {}
    confirmed: set[str] = set()
    for row in sorted(rows, key=lambda item: str(item["month"])):
        raw_value = row.get("business_bankruptcy_filings")
        if isinstance(raw_value, (int, float)) and raw_value > 0:
            observed.append(float(raw_value))
            values[str(row["month"])] = sum(observed[-3:]) / min(3, len(observed))
            confirmed.add(str(row["month"]))
        elif observed:
            # A court report is not yet available. Preserve the latest published
            # trend instead of treating an unreported month as zero or changing
            # component weights.
            values[str(row["month"])] = sum(observed[-3:]) / min(3, len(observed))
    return values, confirmed


def build_market_stress_index(rows: list[dict[str, object]], today: date) -> list[dict[str, object]]:
    index_start = date(today.year - INDEX_HISTORY_YEARS, today.month, 1).isoformat()
    recent_rows = [row for row in rows if str(row["month"]) >= index_start]
    filings, confirmed_filings = smoothed_filings(recent_rows)
    component_values: dict[str, dict[str, float]] = {
        "business_bankruptcy_filings": filings,
    }
    for key, _weight in STRESS_COMPONENTS:
        if key == "business_bankruptcy_filings":
            continue
        component_values[key] = {
            str(row["month"]): float(row[key])
            for row in recent_rows
            if isinstance(row.get(key), (int, float))
        }
    component_scores = {
        key: {month: fixed_stress_score(value, key) for month, value in values.items()}
        for key, values in component_values.items()
        if values
    }
    index_rows: list[dict[str, object]] = []
    for row in recent_rows:
        month = str(row["month"])
        if any(month not in component_scores.get(key, {}) for key, _weight in STRESS_COMPONENTS):
            continue
        score = sum(component_scores[key][month] * weight for key, weight in STRESS_COMPONENTS)
        index_rows.append(
            {
                "month": month,
                "stress_index": round(score, 2),
                "is_provisional": month not in confirmed_filings,
            }
        )
    return index_rows


def upsert_market_stress_index(rows: list[dict[str, object]], supabase_url: str, service_role_key: str) -> None:
    if not rows:
        return
    response = requests.post(
        f"{supabase_url.rstrip('/')}/rest/v1/us_market_stress_index_monthly?on_conflict=month",
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=rows,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=5)
    args = parser.parse_args()
    if args.years < 1 or args.years > 10:
        raise SystemExit("--years 값은 1~10 사이여야 합니다.")

    fred_api_key = os.environ["FRED_API_KEY"]
    supabase_url = os.environ["SUPABASE_URL"]
    service_role_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    today = date.today()
    start = date(today.year - args.years, today.month, 1)
    end = date(today.year, today.month, 1)

    high_yield = fetch_fred_monthly(HIGH_YIELD_SERIES, fred_api_key, start, end)
    financial_conditions = fetch_fred_monthly(FINANCIAL_CONDITIONS_SERIES, fred_api_key, start, end)
    business_filings = collect_business_filings(start, latest_completed_quarter_end(today))
    months = sorted(set(high_yield) | set(financial_conditions) | set(business_filings))
    rows = [
        {
            "month": month,
            "high_yield_oas_pct": high_yield.get(month),
            "financial_conditions_credit_index": financial_conditions.get(month),
            "business_bankruptcy_filings": business_filings.get(month),
        }
        for month in months
    ]
    if not rows:
        raise RuntimeError("저장할 월별 신용 스트레스 데이터가 없습니다.")
    upsert_rows(rows, supabase_url, service_role_key)
    index_rows = build_market_stress_index(rows, today)
    upsert_market_stress_index(index_rows, supabase_url, service_role_key)
    print(
        f"upserted_months={len(rows)} market_stress_index={len(index_rows)} "
        f"business_filings={len(business_filings)} high_yield={len(high_yield)} nfci={len(financial_conditions)}"
    )


if __name__ == "__main__":
    main()
