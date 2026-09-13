"""한국 중소기업 위험지수를 계산하고 월별 결과를 저장한다."""

from __future__ import annotations

import argparse
from datetime import date, datetime

import requests

from common import (
    AUTOMATIC_MONTHLY_CONTEXT_PERIODS,
    AUTOMATIC_MONTHLY_PERIODS,
    SupabaseRest,
    month_start_months_ago,
    uncapped_score,
)
from signals.canonical_series import rows as canonical_rows, store as store_canonical
from sources.korea_small_business_risk import TIMEOUT_SECONDS, fetch_all


COMPONENT_WEIGHTS = {
    "funding_outlook": 35.0,
    "utilization_sa": 30.0,
    "delinquency": 35.0,
}
# 고정 기준은 새 관측치가 추가되어도 과거 점수를 바꾸지 않는다.
COMPONENT_SCALES = {
    "funding_outlook": (100.0, 60.0),
    "utilization_sa": (80.0, 60.0),
    "delinquency": (0.20, 1.20),
}


def component_score(value: float, component: str) -> float:
    floor, reference = COMPONENT_SCALES[component]
    return uncapped_score(value, floor, reference)


def _latest_at_or_before(values: dict[str, float], month: str) -> tuple[str, float] | None:
    available = [key for key in values if key <= month]
    if not available:
        return None
    source_month = max(available)
    return source_month, values[source_month]


def build_rows(raw: dict[str, dict[str, float]]) -> list[dict[str, object]]:
    """전망월별로 늦게 발표되는 구성값을 직전 관측치로 이어 계산한다."""
    rows: list[dict[str, object]] = []
    funding = raw.get("funding_outlook", {})
    utilization = raw.get("utilization_sa", {})
    delinquency = raw.get("delinquency", {})
    headline = raw.get("headline_outlook", {})
    for month in sorted(funding):
        utilization_point = _latest_at_or_before(utilization, month)
        delinquency_point = _latest_at_or_before(delinquency, month)
        if utilization_point is None or delinquency_point is None:
            continue
        utilization_month, utilization_value = utilization_point
        delinquency_month, delinquency_value = delinquency_point
        values = {
            "funding_outlook": funding[month],
            "utilization_sa": utilization_value,
            "delinquency": delinquency_value,
        }
        scores = {name: component_score(value, name) for name, value in values.items()}
        risk_index = sum(scores[name] * COMPONENT_WEIGHTS[name] for name in COMPONENT_WEIGHTS) / 100.0
        rows.append({
            "month": month,
            "risk_index": round(risk_index, 2),
            "funding_outlook_sbhi": round(funding[month], 4),
            "funding_source_month": month,
            "utilization_sa_pct": round(utilization_value, 4),
            "utilization_source_month": utilization_month,
            "sme_loan_delinquency_pct": round(delinquency_value, 4),
            "delinquency_source_month": delinquency_month,
            "headline_outlook_sbhi": round(headline[month], 4) if month in headline else None,
            "is_provisional": utilization_month < month or delinquency_month < month,
            "method_version": "kr-sme-risk-v1",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=AUTOMATIC_MONTHLY_PERIODS)
    args = parser.parse_args()
    if args.months < 1 or args.months > 12:
        raise SystemExit("--months 값은 1~12 사이여야 합니다.")
    today = date.today()
    database = SupabaseRest(timeout=TIMEOUT_SECONDS)
    start = month_start_months_ago(today, args.months - 1)
    end = today.replace(day=1)
    # 최초 표시월에도 발표가 느린 가동률·연체율의 직전 관측치가 필요하다.
    collection_start = month_start_months_ago(start, AUTOMATIC_MONTHLY_CONTEXT_PERIODS)
    try:
        raw = fetch_all(collection_start, end, include_historical=False)
    except requests.RequestException as error:
        print(
            "source_unavailable=true "
            f"source_error={type(error).__name__} "
            "existing_confirmed_data_preserved=true"
        )
        return
    rows = [row for row in build_rows(raw) if str(row["month"]) >= start.isoformat()]
    if not rows:
        raise RuntimeError("저장할 한국 중소기업 위험지수 데이터가 없습니다.")
    source_payload = []
    for name, code, source in (
        ("funding_outlook", "KR_SME_FUNDING_OUTLOOK", "KOSIS:DT_D10116"),
        ("utilization_sa", "KR_SME_UTILIZATION_SA", "KOSIS:DT_D10125"),
        ("delinquency", "KR_CORP_DELINQ", "ECOS:141Y005/R4AB00/X00/0960"),
        ("headline_outlook", "KR_SME_HEADLINE_OUTLOOK", "KOSIS:DT_D10102"),
    ):
        source_payload.extend(canonical_rows(
            code, {date.fromisoformat(day): value for day, value in raw.get(name, {}).items()},
            frequency="M", source=source,
        ))
    store_canonical(database, source_payload)
    derived_rows = [{key: row[key] for key in (
        "month", "risk_index", "funding_source_month", "utilization_source_month",
        "delinquency_source_month", "is_provisional", "method_version",
    )} for row in rows]
    writable = database.automatic_rows(
        "kr_small_business_risk_monthly", derived_rows, key="month", provisional="is_provisional",
        compare_fields=(
            "risk_index", "funding_source_month", "utilization_source_month",
            "delinquency_source_month", "method_version",
        ),
    )
    if writable:
        database.upsert("kr_small_business_risk_monthly", writable, conflict="month")
    print(
        f"calculated_months={len(rows)} stored_months={len(writable)} "
        f"range={rows[0]['month']}..{rows[-1]['month']} "
        f"provisional={sum(bool(row['is_provisional']) for row in rows)}"
    )


if __name__ == "__main__":
    main()
