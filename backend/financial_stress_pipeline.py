"""Collect monthly U.S. credit-stress indicators without retaining source files."""

from __future__ import annotations

import argparse
import calendar
import io
import os
import re
from collections import defaultdict
from datetime import date, timedelta

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
NONFINANCIAL_LEVERAGE_SERIES = "NFCINONFINLEVERAGE"
SP500_SERIES = "SP500"
COMMERCIAL_PAPER_SERIES = "DCPN3M"
THREE_MONTH_TREASURY_SERIES = "DGS3MO"
MONTH_PATTERN = re.compile(r"Ending\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})")
TIMEOUT_SECONDS = 45
INDEX_HISTORY_YEARS = 3
STRESS_COMPONENTS = (
    ("high_yield_oas_pct", 0.50),
    ("financial_conditions_credit_index", 0.30),
    ("business_bankruptcy_filings", 0.20),
)
LEAD_COMPONENT_WEIGHTS = {
    "high_yield": 1 / 3,
    "financial_conditions": 1 / 3,
    "short_term_funding_spread": 1 / 3,
}
SHORT_TERM_FUNDING_FLOOR = 0.10
SHORT_TERM_FUNDING_REFERENCE = 0.60
SHORT_TERM_FUNDING_CHANGE_REFERENCE = 0.30
# Fixed 0-to-100 reference ranges. These never roll with incoming data; values
# above the reference range deliberately remain above 100 to preserve stress
# severity during future extremes.
FIXED_COMPONENT_SCALES = {
    "high_yield_oas_pct": (2.0, 20.0),
    "financial_conditions_credit_index": (-0.5, 2.0),
    "nonfinancial_leverage_index": (-1.5, 2.0),
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


def fetch_fred_month_end(series_id: str, api_key: str, start: date, end: date) -> dict[str, float]:
    """Return the final available daily observation for each calendar month."""
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
    month_end: dict[str, tuple[str, float]] = {}
    for observation in response.json().get("observations", []):
        raw_value = observation.get("value")
        observed_on = observation.get("date")
        if raw_value in (None, ".") or not isinstance(observed_on, str):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        month = observed_on[:7] + "-01"
        if month not in month_end or observed_on > month_end[month][0]:
            month_end[month] = (observed_on, value)
    return {month: value for month, (_observed_on, value) in month_end.items()}


def fetch_fred_latest(series_id: str, api_key: str, start: date, end: date) -> tuple[date, float] | None:
    response = requests.get(
        FRED_URL,
        params={"series_id": series_id, "api_key": api_key, "file_type": "json", "observation_start": start.isoformat(), "observation_end": end.isoformat(), "limit": "100000"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    latest: tuple[date, float] | None = None
    for observation in response.json().get("observations", []):
        raw_value, observed_on = observation.get("value"), observation.get("date")
        if raw_value in (None, ".") or not isinstance(observed_on, str):
            continue
        try:
            candidate = (date.fromisoformat(observed_on), float(raw_value))
        except (TypeError, ValueError):
            continue
        if latest is None or candidate[0] > latest[0]:
            latest = candidate
    return latest


def fetch_fred_week_end(series_id: str, api_key: str, start: date, end: date) -> dict[str, float]:
    """Return the final available observation for each Friday-ended week."""
    response = requests.get(FRED_URL, params={"series_id": series_id, "api_key": api_key, "file_type": "json", "observation_start": start.isoformat(), "observation_end": end.isoformat(), "limit": "100000"}, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    values: dict[str, tuple[str, float]] = {}
    for observation in response.json().get("observations", []):
        raw_value, observed_on = observation.get("value"), observation.get("date")
        if raw_value in (None, ".") or not isinstance(observed_on, str): continue
        try: observed_date, value = date.fromisoformat(observed_on), float(raw_value)
        except (TypeError, ValueError): continue
        week = (observed_date + timedelta(days=4 - observed_date.weekday())).isoformat()
        if week not in values or observed_on > values[week][0]: values[week] = (observed_on, value)
    return {week: value for week, (_observed_on, value) in values.items()}


def carry_forward_monthly(values: dict[str, float], months: list[str]) -> dict[str, float]:
    """Keep a continuous monthly series when a source omits an otherwise ordinary month."""
    carried: dict[str, float] = {}
    previous: float | None = None
    for month in sorted(months):
        current = values.get(month)
        if current is not None:
            previous = current
        if previous is not None:
            carried[month] = previous
    return carried


def build_weekly_lead(
    high_yield: dict[str, float],
    conditions: dict[str, float],
    funding: dict[str, float],
    leverage: dict[str, float],
) -> list[dict[str, object]]:
    weeks = sorted(set(high_yield) | set(conditions) | set(funding) | set(leverage))
    high_yield, conditions, funding, leverage = (
        carry_forward_monthly(values, weeks)
        for values in (high_yield, conditions, funding, leverage)
    )
    rows, previous = [], None
    for week in weeks:
        hy, condition, spread, leverage_value = (
            high_yield.get(week),
            conditions.get(week),
            funding.get(week),
            leverage.get(week),
        )
        if not all(isinstance(value, (int, float)) for value in (hy, condition, spread, leverage_value)):
            continue
        level = (
            fixed_stress_score(float(hy), "high_yield_oas_pct") * LEAD_COMPONENT_WEIGHTS["high_yield"]
            + fixed_stress_score(float(condition), "financial_conditions_credit_index") * LEAD_COMPONENT_WEIGHTS["financial_conditions"]
            + positive_score(float(spread) - SHORT_TERM_FUNDING_FLOOR, SHORT_TERM_FUNDING_REFERENCE) * LEAD_COMPONENT_WEIGHTS["short_term_funding_spread"]
        )
        momentum = None if previous is None else (
            signed_score(float(hy) - previous[0], 1.0) * LEAD_COMPONENT_WEIGHTS["high_yield"]
            + signed_score(float(condition) - previous[1], 0.5) * LEAD_COMPONENT_WEIGHTS["financial_conditions"]
            + signed_score(float(spread) - previous[2], SHORT_TERM_FUNDING_CHANGE_REFERENCE) * LEAD_COMPONENT_WEIGHTS["short_term_funding_spread"]
        )
        rows.append({
            "week": week,
            "lead_index": round(level, 2),
            "lead_momentum": None if momentum is None else round(momentum, 2),
            "leverage_signal": round(fixed_stress_score(float(leverage_value), "nonfinancial_leverage_index"), 2),
        })
        previous = (float(hy), float(condition), float(spread))
    return rows


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


def positive_score(value: float, reference: float) -> float:
    return max(0.0, value / reference * 100.0)


def signed_score(value: float, reference: float) -> float:
    return value / reference * 100.0


def build_market_stress_lead(
    rows: list[dict[str, object]],
    short_term_funding_spread: dict[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Return independent level and month-over-month market-stress signals."""
    ordered_rows = sorted(rows, key=lambda row: str(row["month"]))
    lead_scores: dict[str, float] = {}
    momentum_scores: dict[str, float] = {}
    for index, row in enumerate(ordered_rows):
        month = str(row["month"])
        try:
            funding_spread = float(short_term_funding_spread[month])
            component_scores = {
                "high_yield": fixed_stress_score(float(row["high_yield_oas_pct"]), "high_yield_oas_pct"),
                "financial_conditions": fixed_stress_score(float(row["financial_conditions_credit_index"]), "financial_conditions_credit_index"),
                "short_term_funding_spread": positive_score(
                    funding_spread - SHORT_TERM_FUNDING_FLOOR,
                    SHORT_TERM_FUNDING_REFERENCE,
                ),
            }
        except (KeyError, TypeError, ValueError):
            continue
        lead_scores[month] = round(
            sum(component_scores[key] * weight for key, weight in LEAD_COMPONENT_WEIGHTS.items()),
            2,
        )
        if index == 0:
            continue
        previous = ordered_rows[index - 1]
        try:
            momentum_components = {
                "high_yield": signed_score(
                    float(row["high_yield_oas_pct"]) - float(previous["high_yield_oas_pct"]),
                    1.0,
                ),
                "financial_conditions": signed_score(
                    float(row["financial_conditions_credit_index"]) - float(previous["financial_conditions_credit_index"]),
                    0.5,
                ),
                "short_term_funding_spread": signed_score(
                    funding_spread - float(short_term_funding_spread[str(previous["month"])]),
                    SHORT_TERM_FUNDING_CHANGE_REFERENCE,
                ),
            }
        except (KeyError, TypeError, ValueError):
            continue
        momentum_scores[month] = round(
            sum(momentum_components[key] * weight for key, weight in LEAD_COMPONENT_WEIGHTS.items()),
            2,
        )
    return lead_scores, momentum_scores


def build_market_stress_index(
    rows: list[dict[str, object]],
    today: date,
    sp500_month_end: dict[str, float],
    short_term_funding_spread: dict[str, float],
) -> list[dict[str, object]]:
    index_start = date(today.year - INDEX_HISTORY_YEARS, today.month, 1).isoformat()
    recent_rows = [row for row in rows if str(row["month"]) >= index_start]
    filings, confirmed_filings = smoothed_filings(recent_rows)
    lead_scores, momentum_scores = build_market_stress_lead(recent_rows, short_term_funding_spread)
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
                "sp500_month_end_close": sp500_month_end.get(month),
                "lead_index": lead_scores.get(month),
                "lead_momentum": momentum_scores.get(month),
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


def upsert_weekly_lead(rows: list[dict[str, object]], supabase_url: str, service_role_key: str) -> None:
    response = requests.post(f"{supabase_url.rstrip('/')}/rest/v1/us_market_stress_lead_weekly?on_conflict=week", headers={"apikey": service_role_key, "Authorization": f"Bearer {service_role_key}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}, json=rows, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()


def upsert_latest_credit_stress(row: dict[str, object], supabase_url: str, service_role_key: str) -> None:
    response = requests.post(
        f"{supabase_url.rstrip('/')}/rest/v1/us_credit_stress_latest?on_conflict=singleton",
        headers={"apikey": service_role_key, "Authorization": f"Bearer {service_role_key}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=row,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
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
    sp500_month_end = fetch_fred_month_end(SP500_SERIES, fred_api_key, start, end)
    commercial_paper = fetch_fred_monthly(COMMERCIAL_PAPER_SERIES, fred_api_key, start, end)
    three_month_treasury = fetch_fred_monthly(THREE_MONTH_TREASURY_SERIES, fred_api_key, start, end)
    short_term_funding_spread = {
        month: commercial_paper[month] - three_month_treasury[month]
        for month in commercial_paper.keys() & three_month_treasury.keys()
    }
    business_filings = collect_business_filings(start, latest_completed_quarter_end(today))
    months = sorted(set(high_yield) | set(financial_conditions) | set(business_filings))
    short_term_funding_spread = carry_forward_monthly(short_term_funding_spread, months)
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
    index_rows = build_market_stress_index(rows, today, sp500_month_end, short_term_funding_spread)
    upsert_market_stress_index(index_rows, supabase_url, service_role_key)
    weekly_high_yield = fetch_fred_week_end(HIGH_YIELD_SERIES, fred_api_key, start, today)
    weekly_conditions = fetch_fred_week_end(FINANCIAL_CONDITIONS_SERIES, fred_api_key, start, today)
    weekly_leverage = fetch_fred_week_end(NONFINANCIAL_LEVERAGE_SERIES, fred_api_key, start, today)
    weekly_cp = fetch_fred_week_end(COMMERCIAL_PAPER_SERIES, fred_api_key, start, today)
    weekly_treasury = fetch_fred_week_end(THREE_MONTH_TREASURY_SERIES, fred_api_key, start, today)
    completed_week = (today - timedelta(days=((today.weekday() - 4) % 7 or 7))).isoformat()
    weekly_funding = {week: weekly_cp[week] - weekly_treasury[week] for week in weekly_cp.keys() & weekly_treasury.keys() if week <= completed_week}
    weekly_rows = build_weekly_lead(
        {week: value for week, value in weekly_high_yield.items() if week <= completed_week},
        {week: value for week, value in weekly_conditions.items() if week <= completed_week},
        weekly_funding,
        {week: value for week, value in weekly_leverage.items() if week <= completed_week},
    )
    upsert_weekly_lead(weekly_rows, supabase_url, service_role_key)
    latest_start = today - timedelta(days=60)
    latest_high_yield = fetch_fred_latest(HIGH_YIELD_SERIES, fred_api_key, latest_start, today)
    latest_conditions = fetch_fred_latest(FINANCIAL_CONDITIONS_SERIES, fred_api_key, latest_start, today)
    latest_dates = [source[0] for source in (latest_high_yield, latest_conditions) if source is not None]
    if latest_dates:
        upsert_latest_credit_stress({
            "singleton": True,
            "as_of": max(latest_dates).isoformat(),
            "high_yield_oas_pct": latest_high_yield[1] if latest_high_yield else None,
            "financial_conditions_credit_index": latest_conditions[1] if latest_conditions else None,
        }, supabase_url, service_role_key)
    print(
        f"upserted_months={len(rows)} market_stress_index={len(index_rows)} "
        f"business_filings={len(business_filings)} high_yield={len(high_yield)} "
        f"nfci={len(financial_conditions)} sp500={len(sp500_month_end)} "
        f"short_funding_spread={len(short_term_funding_spread)}"
    )


if __name__ == "__main__":
    main()
