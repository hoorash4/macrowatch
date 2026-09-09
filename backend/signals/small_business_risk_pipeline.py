"""미국 중소기업 위험지수를 계산하고 월별 결과를 저장한다."""

from __future__ import annotations

import argparse
from datetime import date

from common import SupabaseRest, uncapped_score
from sources.small_business_risk import TIMEOUT_SECONDS, fetch_nfib_monthly


COMPONENT_WEIGHTS = {"borrowing_difficulty": 60.0, "sales_expectation": 40.0}
# 고정 기준을 써서 새 달이 추가되어도 과거 점수가 재작성되지 않게 한다.
COMPONENT_SCALES = {
    "sales_expectation": (20.0, -50.0),
    "borrowing_difficulty": (2.0, 15.0),
}


def component_score(value: float, component: str) -> float:
    floor, reference = COMPONENT_SCALES[component]
    return uncapped_score(value, floor, reference)


def build_rows(
    sales_expectations: dict[str, float],
    borrowing_difficulty: dict[str, float],
    optimism_index: dict[str, float],
    today: date,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    current_month = today.replace(day=1).isoformat()
    for month in sorted(sales_expectations.keys() & borrowing_difficulty.keys()):
        raw_values = {
            "borrowing_difficulty": borrowing_difficulty[month],
            "sales_expectation": sales_expectations[month],
        }
        scores = {key: component_score(value, key) for key, value in raw_values.items()}
        risk_index = sum(scores[key] * COMPONENT_WEIGHTS[key] for key in scores) / sum(COMPONENT_WEIGHTS.values())
        rows.append({
            "month": month,
            "risk_index": round(risk_index, 2),
            "sales_expectation_net": round(sales_expectations[month], 4),
            "borrowing_difficulty_pct": round(borrowing_difficulty[month], 4),
            "optimism_index": round(optimism_index[month], 4) if month in optimism_index else None,
            "is_provisional": month == current_month,
        })
    return rows


def existing_legacy_oas(database: SupabaseRest, start: date) -> dict[str, float]:
    """산식에서 제외한 기존 OAS 원자료를 백필 중에도 보존한다."""
    rows = database.request(
        "GET",
        "us_small_business_risk_monthly",
        params={
            "select": "month,high_yield_oas_pct",
            "month": f"gte.{start.replace(day=1).isoformat()}",
            "high_yield_oas_pct": "not.is.null",
            "order": "month.asc",
        },
    ) or []
    return {
        str(row["month"]): float(row["high_yield_oas_pct"])
        for row in rows
        if row.get("month") and row.get("high_yield_oas_pct") is not None
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--replace", action="store_true", help="대상 기간을 외부 원자료로 전체 교체")
    args = parser.parse_args()
    if args.years < 1 or args.years > 10:
        raise SystemExit("--years 값은 1~10 사이여야 합니다.")

    today = date.today()
    start = date(today.year - args.years, today.month, 1)
    end = today.replace(day=1)
    database = SupabaseRest(timeout=TIMEOUT_SECONDS)
    sales, borrowing, optimism = fetch_nfib_monthly(start, end)
    rows = build_rows(sales, borrowing, optimism, today)
    if not rows:
        raise RuntimeError("저장할 미국 중소기업 위험지수 데이터가 없습니다.")
    if args.replace:
        legacy_oas = existing_legacy_oas(database, start)
        for row in rows:
            month = str(row["month"])
            if month in legacy_oas:
                row["high_yield_oas_pct"] = round(legacy_oas[month], 4)
                row["includes_oas"] = True
        database.request(
            "DELETE",
            "us_small_business_risk_monthly",
            params={"month": f"gte.{start.isoformat()}", "and": f"(month.lte.{end.isoformat()})"},
            prefer="return=minimal",
        )
    database.upsert("us_small_business_risk_monthly", rows, conflict="month")
    print(
        f"upserted_months={len(rows)} range={rows[0]['month']}..{rows[-1]['month']} "
        f"replace={args.replace}"
    )


if __name__ == "__main__":
    main()
