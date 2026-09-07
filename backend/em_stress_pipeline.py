"""이머징 시장 원천자료를 주간으로 맞춰 EM-MSI와 비교선을 갱신한다."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone

import requests

from common import SupabaseRest, carry_forward as carry_forward_periods
from common import fetch_fred_observations, require_env, uncapped_score


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/EEM"
TIMEOUT = 45
HISTORY_YEARS = 3

# All values are oriented so that a higher level means greater EM stress.
SERIES = {
    "high_yield_oas": "BAMLEMHYHYLCRPIUSOAS",
    "em_dollar_index": "DTWEXEMEGS",
    "tail_risk_oas": "BAMLEM4BRRBLCRPIOAS",
    "em_equity_volatility": "VXEEMCLS",
}
WEIGHTS = {
    # VXEEM은 본지수에 넣지 않고 별도 보조자료의 4주 평균 계산에만 사용한다.
    "em_dollar_index": 0.45,
    "high_yield_oas": 0.275,
    "tail_risk_oas": 0.275,
}
# Fixed absolute reference bands. Scores are deliberately not capped at 100:
# a future credit event must be able to register above the reference extreme.
SCALES = {
    "high_yield_oas": (2.0, 20.0),
    "em_dollar_index": (100.0, 160.0),
    "tail_risk_oas": (3.0, 20.0),
    "em_equity_volatility": (10.0, 80.0),
}


def required_env(name: str) -> str:
    """기존 호출부를 유지하는 공통 환경변수 함수 어댑터."""
    return require_env(name)


def fetch_fred_week_end(series_id: str, api_key: str, start: date, end: date) -> dict[str, float]:
    weeks: dict[str, tuple[str, float]] = {}
    for observation in fetch_fred_observations(
        series_id, api_key, start=start.isoformat(), end=end.isoformat(), timeout=TIMEOUT
    ):
        raw_value, observed_on = observation.get("value"), observation.get("date")
        if raw_value in (None, ".") or not isinstance(observed_on, str):
            continue
        try:
            observed_date, value = date.fromisoformat(observed_on), float(raw_value)
        except (TypeError, ValueError):
            continue
        week = (observed_date + timedelta(days=4 - observed_date.weekday())).isoformat()
        if week not in weeks or observed_on > weeks[week][0]:
            weeks[week] = (observed_on, value)
    return {week: value for week, (_observed_on, value) in weeks.items()}


def fetch_eem_week_end(start: date, end: date) -> dict[str, float]:
    """Fetch EEM closes for personal-use comparison, then retain each week's last close."""
    response = requests.get(
        YAHOO_CHART_URL,
        params={
            "period1": int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp()),
            "period2": int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()),
            "interval": "1d",
            "events": "history",
        },
        headers={"User-Agent": "MacroWatch personal research dashboard/1.0"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    result = (response.json().get("chart", {}).get("result") or [{}])[0]
    timestamps = result.get("timestamp") or []
    closes = ((result.get("indicators", {}).get("quote") or [{}])[0].get("close") or [])
    weeks: dict[str, tuple[date, float]] = {}
    for raw_timestamp, raw_close in zip(timestamps, closes):
        if raw_close is None:
            continue
        try:
            observed_on, close = datetime.fromtimestamp(raw_timestamp, tz=timezone.utc).date(), float(raw_close)
        except (TypeError, ValueError, OSError):
            continue
        week = (observed_on + timedelta(days=4 - observed_on.weekday())).isoformat()
        if week not in weeks or observed_on > weeks[week][0]:
            weeks[week] = (observed_on, close)
    return {week: close for week, (_observed_on, close) in weeks.items()}


def carry_forward(values: dict[str, float], weeks: list[str]) -> dict[str, float]:
    """기존 호출부를 유지하는 주간 최근값 이월 어댑터."""
    return carry_forward_periods(values, weeks)


def score(value: float, floor: float, reference: float) -> float:
    return uncapped_score(value, floor, reference)


def trailing_average(values: list[float], length: int = 4) -> float:
    window = values[-length:]
    return sum(window) / len(window)


def build_rows(raw: dict[str, dict[str, float]], today: date, eem_values: dict[str, float]) -> list[dict[str, object]]:
    weeks = sorted(set().union(*(set(values) for values in raw.values())))
    if not weeks:
        return []
    carried = {key: carry_forward(values, weeks) for key, values in raw.items()}
    carried_eem = carry_forward(eem_values, weeks)
    last_completed_friday = today - timedelta(days=(today.weekday() - 4) % 7)
    source_last_weeks = {key: max(values) for key, values in raw.items() if values}
    hy_history: list[float] = []
    tail_history: list[float] = []
    volatility_history: list[float] = []
    rows: list[dict[str, object]] = []
    for week in weeks:
        values = {key: carried[key].get(week) for key in SERIES}
        # 보조지표(VXEEM)가 늦거나 비어도 본지수의 세 구성요소가 모두 있으면
        # EM-MSI 행은 생성한다. 지수에 포함되지 않는 자료가 본지수를 막아서는 안 된다.
        if not all(isinstance(values.get(key), (int, float)) for key in WEIGHTS):
            continue
        high_yield = float(values["high_yield_oas"])
        tail_risk = float(values["tail_risk_oas"])
        equity_volatility = values.get("em_equity_volatility")
        hy_history.append(high_yield)
        tail_history.append(tail_risk)
        if isinstance(equity_volatility, (int, float)):
            volatility_history.append(float(equity_volatility))
        stress_index = sum(
            score(float(values[key]), *SCALES[key]) * WEIGHTS[key]
            for key in WEIGHTS
        )
        week_date = date.fromisoformat(week)
        missing_actual = any(week not in raw[key] for key in WEIGHTS)
        required_last_weeks = [source_last_weeks[key] for key in WEIGHTS if key in source_last_weeks]
        is_provisional = missing_actual or week_date > last_completed_friday or any(week > latest for latest in required_last_weeks)
        hy_average = trailing_average(hy_history)
        tail_average = trailing_average(tail_history)
        volatility_average = trailing_average(volatility_history) if volatility_history else None
        rows.append({
            "week": week,
            "stress_index": round(stress_index, 2),
            "high_yield_4w_average": round(hy_average, 4),
            "tail_risk_4w_average": round(tail_average, 4),
            "blended_4w_average": round((hy_average + tail_average) / 2, 4),
            "vxeem_4w_average": round(volatility_average, 4) if volatility_average is not None else None,
            "eem_weekly_close": round(carried_eem[week], 2) if week in carried_eem else None,
            "is_provisional": is_provisional,
        })
    return rows


def upsert(rows: list[dict[str, object]], url: str, service_key: str) -> None:
    SupabaseRest(url=url, service_key=service_key, timeout=TIMEOUT).upsert(
        "em_market_stress_weekly", rows, conflict="week"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=HISTORY_YEARS)
    args = parser.parse_args()
    fred_key = required_env("FRED_API_KEY")
    supabase_url = required_env("SUPABASE_URL")
    service_key = required_env("SUPABASE_SERVICE_ROLE_KEY")
    today = date.today()
    start = today.replace(year=today.year - args.years)
    raw = {key: fetch_fred_week_end(series_id, fred_key, start, today) for key, series_id in SERIES.items()}
    eem_values = fetch_eem_week_end(start, today)
    rows = build_rows(raw, today, eem_values)
    if not rows:
        raise RuntimeError("저장할 이머징 스트레스 데이터가 없습니다.")
    upsert(rows, supabase_url, service_key)
    print("upserted_weeks={} eem={} ".format(len(rows), len(eem_values)) + " ".join(f"{key}={len(values)}" for key, values in raw.items()))


if __name__ == "__main__":
    main()
