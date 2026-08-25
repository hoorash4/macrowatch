"""Collect the published weekly Emerging Market Stress Index without storing source files."""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests


FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
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
    "em_dollar_index": 0.30,
    "em_equity_volatility": 0.30,
    "high_yield_oas": 0.20,
    "tail_risk_oas": 0.20,
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
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def fetch_fred_week_end(series_id: str, api_key: str, start: date, end: date) -> dict[str, float]:
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
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    weeks: dict[str, tuple[str, float]] = {}
    for observation in response.json().get("observations", []):
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
    carried: dict[str, float] = {}
    previous: float | None = None
    for week in weeks:
        if week in values:
            previous = values[week]
        if previous is not None:
            carried[week] = previous
    return carried


def score(value: float, floor: float, reference: float) -> float:
    return max(0.0, (value - floor) / (reference - floor) * 100.0)


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
        if not all(isinstance(value, (int, float)) for value in values.values()):
            continue
        high_yield = float(values["high_yield_oas"])
        tail_risk = float(values["tail_risk_oas"])
        equity_volatility = float(values["em_equity_volatility"])
        hy_history.append(high_yield)
        tail_history.append(tail_risk)
        volatility_history.append(equity_volatility)
        # Retain VXEEM's risk-aversion signal while avoiding a single week's option-market noise.
        scored_values = {**values, "em_equity_volatility": trailing_average(volatility_history)}
        stress_index = sum(
            score(float(scored_values[key]), *SCALES[key]) * WEIGHTS[key]
            for key in SERIES
        )
        week_date = date.fromisoformat(week)
        missing_actual = any(week not in raw[key] for key in SERIES)
        is_provisional = missing_actual or week_date > last_completed_friday or any(week > latest for latest in source_last_weeks.values())
        hy_average = trailing_average(hy_history)
        tail_average = trailing_average(tail_history)
        volatility_average = trailing_average(volatility_history)
        rows.append({
            "week": week,
            "stress_index": round(stress_index, 2),
            "high_yield_4w_average": round(hy_average, 4),
            "tail_risk_4w_average": round(tail_average, 4),
            "blended_4w_average": round((hy_average + tail_average) / 2, 4),
            "vxeem_4w_average": round(volatility_average, 4),
            "eem_weekly_close": round(carried_eem[week], 2) if week in carried_eem else None,
            "is_provisional": is_provisional,
        })
    return rows


def upsert(rows: list[dict[str, object]], url: str, service_key: str) -> None:
    response = requests.post(
        f"{url.rstrip('/')}/rest/v1/em_market_stress_weekly?on_conflict=week",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=rows,
        timeout=TIMEOUT,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase EM stress upsert failed: {response.status_code} {response.text[:1000]}")


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
