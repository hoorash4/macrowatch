"""미국 시장 스트레스 원천자료를 수집하고 월·주 지수를 갱신한다."""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from common import (
    AUTOMATIC_MONTHLY_CONTEXT_PERIODS,
    AUTOMATIC_MONTHLY_PERIODS,
    AUTOMATIC_WEEKLY_CONTEXT_WEEKS,
    AUTOMATIC_WEEKLY_WEEKS,
    SupabaseRest,
    carry_forward,
    month_start_months_ago,
    require_env,
    uncapped_score,
)
from sources.financial_stress import (
    TIMEOUT_SECONDS,
    fetch_cmdi_monthly,
    fetch_ebp_monthly,
    fetch_fred_month_end,
    fetch_fred_week_end,
)
from signals.canonical_series import rows as canonical_rows, store as store_canonical


HIGH_YIELD_SERIES = "BAMLH0A0HYM2"
FINANCIAL_CONDITIONS_SERIES = "NFCICREDIT"
FINANCIAL_RISK_SERIES = "NFCIRISK"
NONFINANCIAL_LEVERAGE_SERIES = "NFCINONFINLEVERAGE"
SP500_SERIES = "SP500"
COMMERCIAL_PAPER_SERIES = "DCPN3M"
THREE_MONTH_TREASURY_SERIES = "DGS3MO"
MONTHLY_STRESS_COMPONENT_WEIGHTS = {
    "excess_bond_premium": 1,
    "corporate_bond_market_distress_index": 1,
}
MONTHLY_STRESS_COMPONENTS = tuple(MONTHLY_STRESS_COMPONENT_WEIGHTS)
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


def fixed_stress_score(value: float, key: str) -> float:
    floor, reference = FIXED_COMPONENT_SCALES[key]
    return uncapped_score(value, floor, reference)


def positive_score(value: float, reference: float) -> float:
    return max(0.0, value / reference * 100.0)


def build_market_stress_index(
    rows: list[dict[str, object]],
    today: date,
    sp500_month_end: dict[str, float],
) -> list[dict[str, object]]:
    recent_rows = rows
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


def upsert_market_stress_index(rows: list[dict[str, object]], supabase_url: str, service_role_key: str) -> int:
    if not rows:
        return 0
    database = SupabaseRest(url=supabase_url, service_key=service_role_key, timeout=TIMEOUT_SECONDS)
    derived = [{key: row[key] for key in ("month", "stress_index", "is_provisional")} for row in rows]
    writable = database.automatic_rows(
        "us_market_stress_index_monthly", derived, key="month", provisional="is_provisional",
        compare_fields=("stress_index",),
    )
    if writable:
        database.upsert("us_market_stress_index_monthly", writable, conflict="month")
    return len(writable)


def upsert_weekly_market_tension(rows: list[dict[str, object]], supabase_url: str, service_role_key: str) -> int:
    database = SupabaseRest(url=supabase_url, service_key=service_role_key, timeout=TIMEOUT_SECONDS)
    derived = [{key: row[key] for key in ("week", "tension_index", "tension_momentum", "is_provisional")} for row in rows]
    writable = database.automatic_rows(
        "us_market_tension_weekly", derived, key="week", provisional="is_provisional",
        compare_fields=("tension_index", "tension_momentum"),
    )
    if writable:
        database.upsert("us_market_tension_weekly", writable, conflict="week")
    return len(writable)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=AUTOMATIC_MONTHLY_PERIODS)
    args = parser.parse_args()
    if args.months < 1 or args.months > 12:
        raise SystemExit("--months 값은 1~12 사이여야 합니다.")

    fred_api_key = require_env("FRED_API_KEY")
    supabase_url = require_env("SUPABASE_URL")
    service_role_key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    today = date.today()
    monthly_start = month_start_months_ago(
        today, args.months - 1 + AUTOMATIC_MONTHLY_CONTEXT_PERIODS,
    )
    weekly_start = today - timedelta(
        weeks=AUTOMATIC_WEEKLY_WEEKS + AUTOMATIC_WEEKLY_CONTEXT_WEEKS,
    )
    end = date(today.year, today.month, 1)

    excess_bond_premium = fetch_ebp_monthly(monthly_start, end)
    cmdi = fetch_cmdi_monthly(monthly_start, end)
    sp500_month_end = fetch_fred_month_end(SP500_SERIES, fred_api_key, monthly_start, end)
    # Keep the current month in the MSI timeline even before either monthly
    # component is published. build_market_stress_index then carries the last
    # confirmed component values forward and marks that month provisional.
    index_months = sorted(set(excess_bond_premium) | set(cmdi) | {end.isoformat()})
    index_source_rows = [
        {
            "month": month,
            "excess_bond_premium": excess_bond_premium.get(month),
            "corporate_bond_market_distress_index": cmdi.get(month),
        }
        for month in index_months
    ]
    if not index_source_rows:
        raise RuntimeError("저장할 미국 시장 스트레스 데이터가 없습니다.")
    index_rows = build_market_stress_index(
        index_source_rows,
        today,
        sp500_month_end,
    )[-args.months:]
    stored_index = upsert_market_stress_index(index_rows, supabase_url, service_role_key)
    weekly_high_yield = fetch_fred_week_end(HIGH_YIELD_SERIES, fred_api_key, weekly_start, today)
    weekly_credit_conditions = fetch_fred_week_end(FINANCIAL_CONDITIONS_SERIES, fred_api_key, weekly_start, today)
    weekly_risk_conditions = fetch_fred_week_end(FINANCIAL_RISK_SERIES, fred_api_key, weekly_start, today)
    weekly_leverage = fetch_fred_week_end(NONFINANCIAL_LEVERAGE_SERIES, fred_api_key, weekly_start, today)
    weekly_cp = fetch_fred_week_end(COMMERCIAL_PAPER_SERIES, fred_api_key, weekly_start, today)
    weekly_treasury = fetch_fred_week_end(THREE_MONTH_TREASURY_SERIES, fred_api_key, weekly_start, today)
    weekly_sp500 = fetch_fred_week_end(SP500_SERIES, fred_api_key, weekly_start, today)
    weekly_funding = {week: weekly_cp[week] - weekly_treasury[week] for week in weekly_cp.keys() & weekly_treasury.keys()}
    weekly_rows = build_weekly_market_tension(
        weekly_high_yield,
        weekly_credit_conditions,
        weekly_risk_conditions,
        weekly_funding,
        weekly_leverage,
        weekly_sp500,
    )[-AUTOMATIC_WEEKLY_WEEKS:]
    canonical = SupabaseRest(url=supabase_url, service_key=service_role_key, timeout=TIMEOUT_SECONDS)
    source_payload = []
    for code, values, frequency, source in (
        ("US_EBP", excess_bond_premium, "M", "FEDERAL_RESERVE:EBP"),
        ("US_CMDI", cmdi, "M", "NYFED:CMDI"),
        ("SP500_MONTH_END", sp500_month_end, "M", "FRED:SP500"),
        ("HY_OAS_WEEKLY", weekly_high_yield, "W", f"FRED:{HIGH_YIELD_SERIES}"),
        ("NFCI_CREDIT", weekly_credit_conditions, "W", f"FRED:{FINANCIAL_CONDITIONS_SERIES}"),
        ("NFCI_RISK", weekly_risk_conditions, "W", f"FRED:{FINANCIAL_RISK_SERIES}"),
        ("NFCI_NONFIN_LEVERAGE", weekly_leverage, "W", f"FRED:{NONFINANCIAL_LEVERAGE_SERIES}"),
        ("US_SHORT_FUNDING_SPREAD", weekly_funding, "W", "DERIVED:DCPN3M-DGS3MO"),
        ("SP500_WEEKLY_CLOSE", weekly_sp500, "W", "FRED:SP500"),
    ):
        source_payload.extend(canonical_rows(
            code, {date.fromisoformat(day): value for day, value in values.items()},
            frequency=frequency, source=source,
        ))
    store_canonical(canonical, source_payload)
    stored_weeks = upsert_weekly_market_tension(weekly_rows, supabase_url, service_role_key)
    print(
        f"calculated_market_stress_index={len(index_rows)} stored_market_stress_index={stored_index} "
        f"calculated_weeks={len(weekly_rows)} stored_weeks={stored_weeks} "
        f"ebp_months={len(excess_bond_premium)} cmdi_months={len(cmdi)} "
        f"sp500={len(sp500_month_end)} "
        f"weekly_nfci_credit={len(weekly_credit_conditions)} "
        f"weekly_nfci_risk={len(weekly_risk_conditions)} "
        f"weekly_leverage={len(weekly_leverage)}"
    )


if __name__ == "__main__":
    main()
