"""미국 중소기업 위험지수를 계산하고 월별 결과를 저장한다."""

from __future__ import annotations

import argparse
from datetime import date

from common import AUTOMATIC_MONTHLY_PERIODS, SupabaseRest, month_start_months_ago, uncapped_score
from sources.small_business_risk import TIMEOUT_SECONDS, fetch_nfib_monthly


COMPONENT_WEIGHTS = {
    "borrowing_difficulty": 35.0,
    "delinquency": 35.0,
    "sales_expectation": 30.0,
}
# 고정 기준을 써서 새 달이 추가되어도 과거 점수가 재작성되지 않게 한다.
COMPONENT_SCALES = {
    "sales_expectation": (20.0, -50.0),
    "borrowing_difficulty": (2.0, 15.0),
    "delinquency": (1.5, 4.5),
}
MAX_DELINQUENCY_LAG_MONTHS = 4


def component_score(value: float, component: str) -> float:
    floor, reference = COMPONENT_SCALES[component]
    return uncapped_score(value, floor, reference)


def build_rows(
    sales_expectations: dict[str, float],
    borrowing_difficulty: dict[str, float],
    delinquency: dict[str, float],
    optimism_index: dict[str, float],
    today: date,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    current_month = today.replace(day=1).isoformat()
    delinquency_months = sorted(delinquency)
    for month in sorted(sales_expectations.keys() & borrowing_difficulty.keys()):
        available = [source_month for source_month in delinquency_months if source_month <= month]
        if not available:
            continue
        delinquency_month = available[-1]
        observed = date.fromisoformat(month)
        source_observed = date.fromisoformat(delinquency_month)
        lag_months = (observed.year - source_observed.year) * 12 + observed.month - source_observed.month
        if lag_months > MAX_DELINQUENCY_LAG_MONTHS:
            continue
        raw_values = {
            "borrowing_difficulty": borrowing_difficulty[month],
            "delinquency": delinquency[delinquency_month],
            "sales_expectation": sales_expectations[month],
        }
        scores = {key: component_score(value, key) for key, value in raw_values.items()}
        risk_index = sum(scores[key] * COMPONENT_WEIGHTS[key] for key in scores) / sum(COMPONENT_WEIGHTS.values())
        rows.append({
            "month": month,
            "risk_index": round(risk_index, 2),
            "sales_expectation_net": round(sales_expectations[month], 4),
            "borrowing_difficulty_pct": round(borrowing_difficulty[month], 4),
            "small_business_delinquency_pct": round(delinquency[delinquency_month], 4),
            "delinquency_source_month": delinquency_month,
            "optimism_index": round(optimism_index[month], 4) if month in optimism_index else None,
            "is_provisional": month == current_month or delinquency_month < month,
        })
    return rows


def fetch_stored_delinquency(database: SupabaseRest, start: date, end: date) -> dict[str, float]:
    lookup_start = month_start_months_ago(start, MAX_DELINQUENCY_LAG_MONTHS)
    saved = database.request("GET", "economic_chart_points", params={
        "select": "observation_date,value",
        "series_code": "eq.US_SBDI_31_180",
        "observation_date": f"gte.{lookup_start.isoformat()}",
        "and": f"(observation_date.lte.{end.isoformat()})",
        "order": "observation_date.asc",
        "limit": "10000",
    }) or []
    return {
        str(row["observation_date"]): float(row["value"])
        for row in saved
        if row.get("observation_date") is not None and row.get("value") is not None
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=AUTOMATIC_MONTHLY_PERIODS)
    args = parser.parse_args()
    if args.months < 1 or args.months > 12:
        raise SystemExit("--months 값은 1~12 사이여야 합니다.")

    today = date.today()
    start = month_start_months_ago(today, args.months - 1)
    end = today.replace(day=1)
    database = SupabaseRest(timeout=TIMEOUT_SECONDS)
    sales, borrowing, optimism = fetch_nfib_monthly(start, end)
    delinquency = fetch_stored_delinquency(database, start, end)
    rows = build_rows(sales, borrowing, delinquency, optimism, today)
    if not rows:
        raise RuntimeError("저장할 미국 중소기업 위험지수 데이터가 없습니다.")
    writable = database.automatic_rows(
        "us_small_business_risk_monthly", rows, key="month", provisional="is_provisional",
        compare_fields=(
            "risk_index", "sales_expectation_net", "borrowing_difficulty_pct",
            "small_business_delinquency_pct", "delinquency_source_month", "optimism_index",
        ),
    )
    if writable:
        database.upsert("us_small_business_risk_monthly", writable, conflict="month")
    print(
        f"calculated_months={len(rows)} stored_months={len(writable)} "
        f"range={rows[0]['month']}..{rows[-1]['month']}"
    )


if __name__ == "__main__":
    main()
