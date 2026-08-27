"""미국 금융환경이 이머징 자금 이동을 허용하는 정도를 일간 지수로 계산한다."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from math import sqrt

from common import SupabaseRest, fetch_fred_observations, require_env


SERIES = {
    "em_dollar_index": "DTWEXEMEGS",
    "real_yield_10y": "DFII10",
    "us_high_yield_oas": "BAMLH0A0HYM2",
    "nfci": "NFCI",
}
MINIMUM_HISTORY = 60
HISTORY_YEARS = 3
UPSERT_BATCH_SIZE = 500


def valid_values(observations: list[dict]) -> dict[str, float]:
    values: dict[str, float] = {}
    for observation in observations:
        try:
            values[str(observation["date"])] = float(observation["value"])
        except (KeyError, TypeError, ValueError):
            continue
    return values


def carry_to_dates(values: dict[str, float], dates: list[str]) -> dict[str, float]:
    """주간 NFCI와 휴일 결측값은 해당 날짜 이전의 최신 관측값으로 맞춘다."""
    observed_dates = sorted(values)
    carried: dict[str, float] = {}
    for period in dates:
        index = bisect_right(observed_dates, period) - 1
        if index >= 0:
            carried[period] = values[observed_dates[index]]
    return carried


def causal_z_scores(values: list[float]) -> list[float | None]:
    """미래값을 보지 않고 해당 날짜까지의 평균과 표준편차로 표준화한다."""
    scores: list[float | None] = []
    total = 0.0
    total_squares = 0.0
    for index, value in enumerate(values, start=1):
        total += value
        total_squares += value * value
        if index < MINIMUM_HISTORY:
            scores.append(None)
            continue
        mean = total / index
        variance = max(total_squares / index - mean * mean, 0.0)
        scores.append((value - mean) / sqrt(variance) if variance > 1e-12 else 0.0)
    return scores


def build_rows(raw: dict[str, dict[str, float]]) -> list[dict]:
    daily_keys = [key for key in SERIES if key != "nfci"]
    dates = sorted(set.intersection(*(set(raw[key]) for key in daily_keys)))
    carried = {key: carry_to_dates(values, dates) for key, values in raw.items()}
    complete_dates = [period for period in dates if all(period in carried[key] for key in SERIES)]
    if not complete_dates:
        return []
    aligned = {key: [carried[key][period] for period in complete_dates] for key in SERIES}
    standardized = {key: causal_z_scores(values) for key, values in aligned.items()}
    updated_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for index, period in enumerate(complete_dates):
        scores = [standardized[key][index] for key in SERIES]
        if any(score is None for score in scores):
            continue
        # 네 원자료는 모두 상승할수록 이머징 유입 여건에 불리하므로 부호를 반전한다.
        capacity_index = -sum(float(score) for score in scores) / len(scores)
        rows.append({
            "observation_date": period,
            **{key: round(aligned[key][index], 6) for key in SERIES},
            "capacity_index": round(capacity_index, 6),
            "updated_at": updated_at,
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=HISTORY_YEARS)
    args = parser.parse_args()
    today = date.today()
    start = today - timedelta(days=max(args.years, 1) * 366)
    api_key = require_env("FRED_API_KEY")
    raw = {
        key: valid_values(fetch_fred_observations(series_id, api_key, start=start.isoformat(), end=today.isoformat()))
        for key, series_id in SERIES.items()
    }
    rows = build_rows(raw)
    if not rows:
        raise RuntimeError("저장할 이머징 자금 유입 여건 데이터가 없습니다.")
    database = SupabaseRest()
    for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
        database.upsert("em_capital_capacity_daily", rows[offset:offset + UPSERT_BATCH_SIZE], conflict="observation_date")
    database.request(
        "DELETE", "em_capital_capacity_daily",
        params={"observation_date": f"lt.{start.isoformat()}"}, prefer="return=minimal",
    )
    print(f"Upserted {len(rows)} EM capital-capacity observations through {rows[-1]['observation_date']}.")


if __name__ == "__main__":
    main()
