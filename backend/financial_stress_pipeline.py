"""미국 신용·시장 스트레스 원천자료를 수집하고 월·주 지수를 갱신한다."""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from common import SupabaseRest, carry_forward, month_start_months_ago, require_env, uncapped_score
from financial_stress_sources import (
    TIMEOUT_SECONDS,
    collect_business_filings,
    fetch_cmdi_monthly,
    fetch_ebp_monthly,
    fetch_fred_latest,
    fetch_fred_month_end,
    fetch_fred_monthly,
    fetch_fred_week_end,
    latest_completed_quarter_end,
    month_start,
)


HIGH_YIELD_SERIES = "BAMLH0A0HYM2"
FINANCIAL_CONDITIONS_SERIES = "NFCICREDIT"
FINANCIAL_RISK_SERIES = "NFCIRISK"
NONFINANCIAL_LEVERAGE_SERIES = "NFCINONFINLEVERAGE"
SP500_SERIES = "SP500"
COMMERCIAL_PAPER_SERIES = "DCPN3M"
THREE_MONTH_TREASURY_SERIES = "DGS3MO"
INDEX_HISTORY_YEARS = 3
RETENTION_MONTHS = 37
MONTHLY_STRESS_COMPONENTS = (
    "excess_bond_premium",
    "corporate_bond_market_distress_index",
)
MONTHLY_STRESS_COMPONENT_WEIGHTS = {
    "excess_bond_premium": 1,
    "corporate_bond_market_distress_index": 1,
}
WEEKLY_TENSION_COMPONENT_WEIGHTS = {
    "high_yield": 0.20,
    "financial_conditions_credit": 0.20,
    "financial_conditions_risk": 0.20,
    "short_term_funding_spread": 0.20,
    "nonfinancial_leverage": 0.20,
}
SHORT_TERM_FUNDING_FLOOR = 0.10
SHORT_TERM_FUNDING_REFERENCE = 0.60
# Fixed 0-to-100 reference ranges. These never roll with incoming data; values
# above the reference range deliberately remain above 100 to preserve stress
# severity during future extremes.
FIXED_COMPONENT_SCALES = {
    "high_yield_oas_pct": (2.0, 20.0),
    "financial_conditions_credit_index": (-0.5, 2.0),
    "financial_conditions_risk_index": (-0.5, 2.0),
    "corporate_bond_market_distress_index": (0.0, 1.0),
    "excess_bond_premium": (-1.0, 4.0),
    "nonfinancial_leverage_index": (-1.5, 2.0),
    "business_bankruptcy_filings": (1000.0, 5000.0),
}


def carry_forward_values(values: dict[str, float], periods: list[str]) -> dict[str, float]:
    """기존 공개 함수 이름을 유지하면서 공통 이월 계산을 사용한다."""
    return carry_forward(values, sorted(periods))


def build_weekly_market_tension(
    high_yield: dict[str, float],
    credit_conditions: dict[str, float],
    risk_conditions: dict[str, float],
    funding: dict[str, float],
    leverage: dict[str, float],
    sp500: dict[str, float],
) -> list[dict[str, object]]:
    weeks = sorted(set(high_yield) | set(credit_conditions) | set(risk_conditions) | set(funding) | set(leverage))
    raw_sources = (high_yield, credit_conditions, risk_conditions, funding, leverage)
    latest_confirmed_week = min(max(values) for values in raw_sources if values)
    high_yield, credit_conditions, risk_conditions, funding, leverage = (
        carry_forward_values(values, weeks)
        for values in raw_sources
    )
    rows, previous_level = [], None
    for week in weeks:
        hy, credit_condition, risk_condition, spread, leverage_value = (
            high_yield.get(week),
            credit_conditions.get(week),
            risk_conditions.get(week),
            funding.get(week),
            leverage.get(week),
        )
        if not all(isinstance(value, (int, float)) for value in (hy, credit_condition, risk_condition, spread, leverage_value)):
            continue
        level = (
            fixed_stress_score(float(hy), "high_yield_oas_pct") * WEEKLY_TENSION_COMPONENT_WEIGHTS["high_yield"]
            + fixed_stress_score(float(credit_condition), "financial_conditions_credit_index") * WEEKLY_TENSION_COMPONENT_WEIGHTS["financial_conditions_credit"]
            + fixed_stress_score(float(risk_condition), "financial_conditions_risk_index") * WEEKLY_TENSION_COMPONENT_WEIGHTS["financial_conditions_risk"]
            + positive_score(float(spread) - SHORT_TERM_FUNDING_FLOOR, SHORT_TERM_FUNDING_REFERENCE) * WEEKLY_TENSION_COMPONENT_WEIGHTS["short_term_funding_spread"]
            + fixed_stress_score(float(leverage_value), "nonfinancial_leverage_index") * WEEKLY_TENSION_COMPONENT_WEIGHTS["nonfinancial_leverage"]
        )
        tension_index = round(level, 2)
        momentum = None if previous_level is None else tension_index - previous_level
        rows.append({
            "week": week,
            "tension_index": tension_index,
            "tension_momentum": None if momentum is None else round(momentum, 2),
            "high_yield_oas_pct": round(float(hy), 4),
            "financial_conditions_credit_index": round(float(credit_condition), 4),
            "financial_conditions_risk_index": round(float(risk_condition), 4),
            "nonfinancial_leverage_index": round(float(leverage_value), 4),
            "short_term_funding_spread": round(float(spread), 4),
            "sp500_friday_close": sp500.get(week),
            "is_provisional": week > latest_confirmed_week,
        })
        previous_level = tension_index
    return rows


def upsert_rows(rows: list[dict[str, object]], supabase_url: str, service_role_key: str) -> None:
    SupabaseRest(url=supabase_url, service_key=service_role_key, timeout=TIMEOUT_SECONDS).upsert(
        "us_credit_stress_monthly", rows, conflict="month"
    )


def fixed_stress_score(value: float, key: str) -> float:
    floor, reference = FIXED_COMPONENT_SCALES[key]
    return uncapped_score(value, floor, reference)


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


def build_market_stress_index(
    rows: list[dict[str, object]],
    today: date,
    sp500_month_end: dict[str, float],
) -> list[dict[str, object]]:
    index_start = date(today.year - INDEX_HISTORY_YEARS, today.month, 1).isoformat()
    recent_rows = [row for row in rows if str(row["month"]) >= index_start]
    months = [str(row["month"]) for row in recent_rows]
    raw_component_values: dict[str, dict[str, float]] = {}
    for key in MONTHLY_STRESS_COMPONENTS:
        raw_component_values[key] = {
            str(row["month"]): float(row[key])
            for row in recent_rows
            if isinstance(row.get(key), (int, float))
        }
    component_values = {
        key: carry_forward_values(values, months)
        for key, values in raw_component_values.items()
    }
    component_scores = {
        key: {month: fixed_stress_score(value, key) for month, value in values.items()}
        for key, values in component_values.items()
        if values
    }
    index_rows: list[dict[str, object]] = []
    for row in recent_rows:
        month = str(row["month"])
        if any(month not in component_scores.get(key, {}) for key in MONTHLY_STRESS_COMPONENTS):
            continue
        score = sum(
            component_scores[key][month] * MONTHLY_STRESS_COMPONENT_WEIGHTS[key]
            for key in MONTHLY_STRESS_COMPONENTS
        ) / sum(MONTHLY_STRESS_COMPONENT_WEIGHTS.values())
        index_rows.append(
            {
                "month": month,
                "stress_index": round(score, 2),
                "is_provisional": any(
                    month not in raw_component_values.get(key, {})
                    for key in MONTHLY_STRESS_COMPONENTS
                ),
                "sp500_month_end_close": sp500_month_end.get(month),
            }
        )
    return index_rows


def upsert_market_stress_index(rows: list[dict[str, object]], supabase_url: str, service_role_key: str) -> None:
    if not rows:
        return
    SupabaseRest(url=supabase_url, service_key=service_role_key, timeout=TIMEOUT_SECONDS).upsert(
        "us_market_stress_index_monthly", rows, conflict="month"
    )


def upsert_weekly_market_tension(rows: list[dict[str, object]], supabase_url: str, service_role_key: str) -> None:
    SupabaseRest(url=supabase_url, service_key=service_role_key, timeout=TIMEOUT_SECONDS).upsert(
        "us_market_tension_weekly", rows, conflict="week"
    )


def upsert_latest_credit_stress(row: dict[str, object], supabase_url: str, service_role_key: str) -> None:
    SupabaseRest(url=supabase_url, service_key=service_role_key, timeout=TIMEOUT_SECONDS).upsert(
        "us_credit_stress_latest", row, conflict="singleton"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()
    if args.years < 1 or args.years > 10:
        raise SystemExit("--years 값은 1~10 사이여야 합니다.")

    fred_api_key = require_env("FRED_API_KEY")
    supabase_url = require_env("SUPABASE_URL")
    service_role_key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    today = date.today()
    start = date(today.year - args.years, today.month, 1)
    end = date(today.year, today.month, 1)

    high_yield = fetch_fred_monthly(HIGH_YIELD_SERIES, fred_api_key, start, end)
    financial_conditions = fetch_fred_monthly(FINANCIAL_CONDITIONS_SERIES, fred_api_key, start, end)
    financial_risk = fetch_fred_monthly(FINANCIAL_RISK_SERIES, fred_api_key, start, end)
    excess_bond_premium = fetch_ebp_monthly(start, end)
    cmdi = fetch_cmdi_monthly(start, end)
    sp500_month_end = fetch_fred_month_end(SP500_SERIES, fred_api_key, start, end)
    commercial_paper = fetch_fred_monthly(COMMERCIAL_PAPER_SERIES, fred_api_key, start, end)
    three_month_treasury = fetch_fred_monthly(THREE_MONTH_TREASURY_SERIES, fred_api_key, start, end)
    short_term_funding_spread = {
        month: commercial_paper[month] - three_month_treasury[month]
        for month in commercial_paper.keys() & three_month_treasury.keys()
    }
    business_filings = collect_business_filings(start, latest_completed_quarter_end(today))
    months = sorted(
        set(high_yield) | set(financial_conditions) | set(financial_risk)
        | set(excess_bond_premium) | set(cmdi) | set(business_filings)
    )
    short_term_funding_spread = carry_forward_values(short_term_funding_spread, months)
    rows = [
        {
            "month": month,
            "high_yield_oas_pct": high_yield.get(month),
            "financial_conditions_credit_index": financial_conditions.get(month),
            "financial_conditions_risk_index": financial_risk.get(month),
            "excess_bond_premium": excess_bond_premium.get(month),
            "corporate_bond_market_distress_index": cmdi.get(month),
            "business_bankruptcy_filings": business_filings.get(month),
        }
        for month in months
    ]
    if not rows:
        raise RuntimeError("저장할 월별 신용 스트레스 데이터가 없습니다.")
    latest_start = today - timedelta(days=60)
    latest_high_yield = fetch_fred_latest(HIGH_YIELD_SERIES, fred_api_key, latest_start, today)
    latest_conditions = fetch_fred_latest(FINANCIAL_CONDITIONS_SERIES, fred_api_key, latest_start, today)
    latest_risk = fetch_fred_latest(FINANCIAL_RISK_SERIES, fred_api_key, latest_start, today)
    latest_dates = [source[0] for source in (latest_high_yield, latest_conditions, latest_risk) if source is not None]
    latest_month = month_start(max(latest_dates)) if latest_dates else None
    if latest_month and latest_high_yield and latest_conditions and latest_risk:
        latest_values = {
            "high_yield_oas_pct": latest_high_yield[1],
            "financial_conditions_credit_index": latest_conditions[1],
            "financial_conditions_risk_index": latest_risk[1],
            "excess_bond_premium": excess_bond_premium.get(latest_month),
            "corporate_bond_market_distress_index": cmdi.get(latest_month),
        }
        matching_row = next((row for row in rows if row["month"] == latest_month), None)
        if matching_row is None:
            rows.append({
                "month": latest_month,
                **latest_values,
                "business_bankruptcy_filings": None,
            })
        else:
            matching_row.update(latest_values)
    index_rows_input = rows
    upsert_rows(rows, supabase_url, service_role_key)
    index_rows = build_market_stress_index(
        index_rows_input,
        today,
        sp500_month_end,
    )
    upsert_market_stress_index(index_rows, supabase_url, service_role_key)
    weekly_high_yield = fetch_fred_week_end(HIGH_YIELD_SERIES, fred_api_key, start, today)
    weekly_credit_conditions = fetch_fred_week_end(FINANCIAL_CONDITIONS_SERIES, fred_api_key, start, today)
    weekly_risk_conditions = fetch_fred_week_end(FINANCIAL_RISK_SERIES, fred_api_key, start, today)
    weekly_leverage = fetch_fred_week_end(NONFINANCIAL_LEVERAGE_SERIES, fred_api_key, start, today)
    weekly_cp = fetch_fred_week_end(COMMERCIAL_PAPER_SERIES, fred_api_key, start, today)
    weekly_treasury = fetch_fred_week_end(THREE_MONTH_TREASURY_SERIES, fred_api_key, start, today)
    weekly_sp500 = fetch_fred_week_end(SP500_SERIES, fred_api_key, start, today)
    weekly_funding = {week: weekly_cp[week] - weekly_treasury[week] for week in weekly_cp.keys() & weekly_treasury.keys()}
    weekly_rows = build_weekly_market_tension(
        weekly_high_yield,
        weekly_credit_conditions,
        weekly_risk_conditions,
        weekly_funding,
        weekly_leverage,
        weekly_sp500,
    )
    upsert_weekly_market_tension(weekly_rows, supabase_url, service_role_key)
    if latest_dates:
        upsert_latest_credit_stress({
            "singleton": True,
            "as_of": max(latest_dates).isoformat(),
            "high_yield_oas_pct": latest_high_yield[1] if latest_high_yield else None,
            "financial_conditions_credit_index": latest_conditions[1] if latest_conditions else None,
        }, supabase_url, service_role_key)
    # 모든 저장이 성공한 뒤에만 표시 범위보다 한 달 여유를 둔 원천 시계열을 정리한다.
    cutoff = month_start_months_ago(today, RETENTION_MONTHS).isoformat()
    database = SupabaseRest(url=supabase_url, service_key=service_role_key, timeout=TIMEOUT_SECONDS)
    database.delete_before("us_credit_stress_monthly", "month", cutoff)
    database.delete_before("us_market_stress_index_monthly", "month", cutoff)
    database.delete_before("us_market_tension_weekly", "week", cutoff)
    print(
        f"upserted_months={len(rows)} market_stress_index={len(index_rows)} "
        f"business_filings={len(business_filings)} high_yield={len(high_yield)} "
        f"nfci_credit={len(financial_conditions)} nfci_risk={len(financial_risk)} "
        f"ebp_months={len(excess_bond_premium)} cmdi_months={len(cmdi)} "
        f"sp500={len(sp500_month_end)} "
        f"short_funding_spread={len(short_term_funding_spread)} "
        f"weekly_leverage={len(weekly_leverage)}"
    )


if __name__ == "__main__":
    main()
