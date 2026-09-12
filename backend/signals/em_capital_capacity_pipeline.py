"""미국 금융환경이 이머징 자금 이동을 허용하는 정도를 일간 지수로 계산한다."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from math import sqrt

from common import (
    AUTOMATIC_DAILY_CALENDAR_DAYS,
    AUTOMATIC_DAILY_VALUES,
    SupabaseRest,
    fetch_fred_observations,
    require_env,
)
from signals.automatic_source_cache import load as load_source_cache
from signals.automatic_source_cache import is_initialized, mark_initialized
from signals.automatic_source_cache import store as store_source_cache


SERIES = {
    "em_dollar_index": "DTWEXEMEGS",
    "real_yield_10y": "DFII10",
    "us_high_yield_oas": "BAMLH0A0HYM2",
    "nfci": "NFCI",
}
MINIMUM_HISTORY = 60
INITIALIZATION_HISTORY_YEARS = 3
UPSERT_BATCH_SIZE = 500
CACHE_COLLECTOR = "em_capital_capacity"


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
    # 실질금리와 HY OAS가 있는 최신 영업일까지 계산하고, 주간·지연 발표 자료는
    # 직전 값을 이어 쓴다. 모든 원자료가 그 날짜까지 도착하면 잠정치는 확정된다.
    dates = sorted(set(raw["real_yield_10y"]) & set(raw["us_high_yield_oas"]))
    carried = {key: carry_to_dates(values, dates) for key, values in raw.items()}
    complete_dates = [period for period in dates if all(period in carried[key] for key in SERIES)]
    if not complete_dates:
        return []
    aligned = {key: [carried[key][period] for period in complete_dates] for key in SERIES}
    standardized = {key: causal_z_scores(values) for key, values in aligned.items()}
    confirmed_through = min(max(values) for values in raw.values() if values)
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
            "is_provisional": period > confirmed_through,
            "updated_at": updated_at,
        })
    return rows


def fetch_sources(api_key: str, start: date, end: date) -> dict[str, dict[str, float]]:
    return {
        key: valid_values(fetch_fred_observations(
            series_id, api_key, start=start.isoformat(), end=end.isoformat(),
        ))
        for key, series_id in SERIES.items()
    }


def cache_points(raw: dict[str, dict[str, float]]) -> dict[str, dict[date, tuple[date, float]]]:
    return {
        key: {
            date.fromisoformat(period): (date.fromisoformat(period), value)
            for period, value in values.items()
        }
        for key, values in raw.items()
    }


def cached_values(cache: dict[str, dict[date, tuple[date, float]]]) -> dict[str, dict[str, float]]:
    return {
        key: {period.isoformat(): value for period, (_observed, value) in cache.get(key, {}).items()}
        for key in SERIES
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize-sources", action="store_true")
    args = parser.parse_args()
    today = date.today()
    api_key = require_env("FRED_API_KEY")
    database = SupabaseRest()
    cache = load_source_cache(database, CACHE_COLLECTOR)
    if args.initialize_sources:
        initialization_start = today - timedelta(days=INITIALIZATION_HISTORY_YEARS * 366)
        historical = fetch_sources(api_key, initialization_start, today)
        if any(len(historical.get(key, {})) < MINIMUM_HISTORY for key in SERIES):
            raise RuntimeError("EM capital source initialization returned insufficient history")
        store_source_cache(database, CACHE_COLLECTOR, cache_points(historical))
        mark_initialized(database, CACHE_COLLECTOR, today)
        cache = load_source_cache(database, CACHE_COLLECTOR)
    if not is_initialized(cache) or any(len(cache.get(key, {})) < MINIMUM_HISTORY for key in SERIES):
        raise RuntimeError("EM capital source cache is not initialized; run --initialize-sources explicitly")
    recent_start = today - timedelta(days=AUTOMATIC_DAILY_CALENDAR_DAYS)
    recent = fetch_sources(api_key, recent_start, today)
    store_source_cache(database, CACHE_COLLECTOR, cache_points(recent))
    for key, values in recent.items():
        cache.setdefault(key, {}).update(cache_points({key: values})[key])
    raw = cached_values(cache)
    rows = build_rows(raw)[-AUTOMATIC_DAILY_VALUES:]
    if not rows:
        raise RuntimeError("저장할 이머징 자금 유입 여건 데이터가 없습니다.")
    writable = database.automatic_rows(
        "em_capital_capacity_daily", rows,
        key="observation_date", provisional="is_provisional",
        compare_fields=tuple(SERIES) + ("capacity_index",),
    )
    for offset in range(0, len(writable), UPSERT_BATCH_SIZE):
        database.upsert("em_capital_capacity_daily", writable[offset:offset + UPSERT_BATCH_SIZE], conflict="observation_date")
    print(f"Calculated {len(rows)} and stored {len(writable)} EM capital-capacity observations through {rows[-1]['observation_date']}.")


if __name__ == "__main__":
    main()
