"""Collect minimal monthly sources and persist V1 equity-bond forecasts.

Only source series absent from the existing MacroWatch database are retained:
SPY/TLT adjusted closes, T10Y2Y and BAA10Y.  DFII10 is reused from the EM
capital-capacity table where available, while historical DFII10 and weekly NFCI
are read from FRED without creating another duplicate raw-data table.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from common import SupabaseRest, fetch_fred_observations, require_env
from equity_bond_model import MODEL_VERSION, MonthlyInputs, build_feature_rows, walk_forward_forecasts


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
FRED_SERIES = {
    "real_yield_10y": "DFII10",
    "yield_curve_10y_2y": "T10Y2Y",
    "baa_spread": "BAA10Y",
    "nfci_level": "NFCI",
}
SOURCE_CODES = {
    "spy_adjusted_close": ("SPY_ADJUSTED_CLOSE", "yahoo_finance"),
    "tlt_adjusted_close": ("TLT_ADJUSTED_CLOSE", "yahoo_finance"),
    "yield_curve_10y_2y": ("T10Y2Y", "fred"),
    "baa_spread": ("BAA10Y", "fred"),
}
NFCI_PUBLICATION_LAG_DAYS = 7
UPSERT_BATCH_SIZE = 500


def first_of_month(value: date) -> date:
    return value.replace(day=1)


def previous_completed_month(today: date) -> date:
    return first_of_month(today) - timedelta(days=1)


def valid_fred_values(observations: list[dict[str, Any]]) -> dict[date, float]:
    values: dict[date, float] = {}
    for observation in observations:
        try:
            values[date.fromisoformat(str(observation["date"]))] = float(observation["value"])
        except (KeyError, TypeError, ValueError):
            continue
    return values


def fetch_yahoo_adjusted(symbol: str, start: date, end: date) -> dict[date, float]:
    """Fetch split- and distribution-adjusted closes from Yahoo's chart feed."""

    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    response = requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        },
        headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"},
        timeout=45,
    )
    response.raise_for_status()
    result = response.json().get("chart", {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo returned no chart result for {symbol}")
    chart = result[0]
    timestamps = chart.get("timestamp") or []
    adjusted_groups = chart.get("indicators", {}).get("adjclose") or []
    adjusted = adjusted_groups[0].get("adjclose", []) if adjusted_groups else []
    values: dict[date, float] = {}
    for timestamp, raw_value in zip(timestamps, adjusted):
        if raw_value is None:
            continue
        values[datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date()] = float(raw_value)
    if not values:
        raise RuntimeError(f"Yahoo returned no adjusted closes for {symbol}")
    return values


def month_end_values(values: dict[date, float]) -> dict[date, tuple[date, float]]:
    """Keep the final actual observation in each calendar month."""

    monthly: dict[date, tuple[date, float]] = {}
    for observation_date, value in sorted(values.items()):
        monthly[first_of_month(observation_date)] = (observation_date, value)
    return monthly


def lagged_month_values(
    values: dict[date, float],
    months: list[date],
    *,
    publication_lag_days: int,
) -> dict[date, tuple[date, float]]:
    """Align a delayed series without making future releases visible early."""

    dates = sorted(values)
    aligned: dict[date, tuple[date, float]] = {}
    for month in months:
        next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        cutoff = next_month - timedelta(days=1 + publication_lag_days)
        position = bisect_right(dates, cutoff) - 1
        if position >= 0:
            observed = dates[position]
            aligned[month] = (observed, values[observed])
    return aligned


def reused_dfii10_values(
    database: SupabaseRest,
    fred_api_key: str,
    start: date,
    end: date,
) -> dict[date, float]:
    """Reuse the retained EM DFII10 rows and fetch only their missing history."""

    existing: dict[date, float] = {}
    try:
        rows = database.request(
            "GET",
            "em_capital_capacity_daily",
            params={
                "select": "observation_date,real_yield_10y",
                "observation_date": f"gte.{start.isoformat()}",
                "order": "observation_date.asc",
                "limit": "5000",
            },
        ) or []
        for row in rows:
            existing[date.fromisoformat(str(row["observation_date"]))] = float(row["real_yield_10y"])
    except (KeyError, TypeError, ValueError, RuntimeError):
        # A first deployment or temporary PostgREST failure must not corrupt the
        # calculation; FRED remains the authoritative fallback.
        existing = {}
    missing_end = min(existing) - timedelta(days=1) if existing else end
    historical: dict[date, float] = {}
    if missing_end >= start:
        historical = valid_fred_values(fetch_fred_observations(
            FRED_SERIES["real_yield_10y"],
            fred_api_key,
            start=start.isoformat(),
            end=missing_end.isoformat(),
        ))
    historical.update(existing)
    return historical


def build_monthly_inputs(
    raw: dict[str, dict[date, float]],
    *,
    start: date,
    end: date,
) -> list[MonthlyInputs]:
    monthly = {
        key: month_end_values(values)
        for key, values in raw.items()
        if key != "nfci_level"
    }
    candidate_months = sorted(
        set(monthly["spy_adjusted_close"])
        & set(monthly["tlt_adjusted_close"])
        & set(monthly["real_yield_10y"])
        & set(monthly["yield_curve_10y_2y"])
        & set(monthly["baa_spread"])
    )
    candidate_months = [month for month in candidate_months if month >= first_of_month(start) and month <= first_of_month(end)]
    nfci = lagged_month_values(
        raw["nfci_level"],
        candidate_months,
        publication_lag_days=NFCI_PUBLICATION_LAG_DAYS,
    )
    results: list[MonthlyInputs] = []
    for month in candidate_months:
        if month not in nfci:
            continue
        observations = {key: values[month] for key, values in monthly.items()}
        results.append(MonthlyInputs(
            month=month,
            spy_adjusted_close=observations["spy_adjusted_close"][1],
            tlt_adjusted_close=observations["tlt_adjusted_close"][1],
            real_yield_10y=observations["real_yield_10y"][1],
            yield_curve_10y_2y=observations["yield_curve_10y_2y"][1],
            baa_spread=observations["baa_spread"][1],
            nfci_level=nfci[month][1],
            source_through_date=max(
                *(observation[0] for observation in observations.values()),
                nfci[month][0],
            ),
        ))
    return results


def source_rows(raw: dict[str, dict[date, float]], updated_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, (series_code, source) in SOURCE_CODES.items():
        for month, (observation_date, value) in month_end_values(raw[key]).items():
            rows.append({
                "series_code": series_code,
                "month": month.isoformat(),
                "observation_date": observation_date.isoformat(),
                "value": round(value, 8),
                "source": source,
                "updated_at": updated_at,
            })
    return rows


def forecast_rows(forecasts, updated_at: str) -> list[dict[str, Any]]:
    rows = []
    for forecast in forecasts:
        actual = forecast.actual_relative_return_pct
        rows.append({
            "forecast_month": forecast.month.isoformat(),
            "model_version": MODEL_VERSION,
            "source_through_date": forecast.source_through_date.isoformat(),
            "relative_momentum_6m": round(forecast.features[0], 8),
            "real_yield_expanding_percentile": round(forecast.features[1], 8),
            "yield_curve_10y_2y": round(forecast.features[2], 6),
            "baa_spread_change_3m": round(forecast.features[3], 6),
            "nfci_level": round(forecast.features[4], 8),
            "stock_outperformance_probability": round(forecast.stock_probability, 8),
            "bond_outperformance_probability": round(1.0 - forecast.stock_probability, 8),
            "expected_relative_return_pct": round(forecast.expected_relative_return_pct, 8),
            "downside_q25_relative_return_pct": round(forecast.downside_q25_pct, 8),
            "verdict": forecast.verdict,
            "training_start_month": forecast.training_start_month.isoformat(),
            "training_end_month": forecast.training_end_month.isoformat(),
            "training_sample_count": forecast.training_sample_count,
            "actual_relative_return_pct": round(actual, 8) if actual is not None else None,
            "outcome_status": "complete" if actual is not None else "pending",
            "validation": dict(forecast.validation),
            "updated_at": updated_at,
        })
    return rows


def upsert_batches(database: SupabaseRest, table: str, rows: list[dict[str, Any]], conflict: str) -> None:
    for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
        database.upsert(table, rows[offset:offset + UPSERT_BATCH_SIZE], conflict=conflict)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2002)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    today = date.today()
    end = previous_completed_month(today)
    start = date(max(args.start_year, 2002), 1, 1)
    fred_api_key = require_env("FRED_API_KEY")
    database = SupabaseRest()

    raw = {
        "spy_adjusted_close": fetch_yahoo_adjusted("SPY", start, end),
        "tlt_adjusted_close": fetch_yahoo_adjusted("TLT", start, end),
        "real_yield_10y": reused_dfii10_values(database, fred_api_key, start, end),
    }
    for key in ("yield_curve_10y_2y", "baa_spread", "nfci_level"):
        raw[key] = valid_fred_values(fetch_fred_observations(
            FRED_SERIES[key],
            fred_api_key,
            start=start.isoformat(),
            end=end.isoformat(),
        ))

    inputs = build_monthly_inputs(raw, start=start, end=end)
    features = build_feature_rows(inputs)
    forecasts = walk_forward_forecasts(features)
    if not forecasts:
        raise RuntimeError("No equity-bond forecasts were produced; verify source history and overlap.")
    updated_at = datetime.now(timezone.utc).isoformat()
    retained_sources = source_rows(raw, updated_at)
    retained_forecasts = forecast_rows(forecasts, updated_at)
    if not args.dry_run:
        upsert_batches(database, "equity_bond_source_monthly", retained_sources, "series_code,month")
        upsert_batches(database, "equity_bond_relative_forecasts", retained_forecasts, "forecast_month")
    print(
        f"Equity-bond V1: inputs={len(inputs)} sources={len(retained_sources)} "
        f"forecasts={len(retained_forecasts)} latest={retained_forecasts[-1]['forecast_month']} "
        f"verdict={retained_forecasts[-1]['verdict']} dry_run={args.dry_run}"
    )


if __name__ == "__main__":
    main()
