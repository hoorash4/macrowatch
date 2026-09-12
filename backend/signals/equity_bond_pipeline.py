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

from common import AUTOMATIC_MONTHLY_PERIODS, SupabaseRest, fetch_fred_observations, month_start_months_ago, require_env
from signals.automatic_source_cache import load as load_source_cache
from signals.automatic_source_cache import is_initialized, mark_initialized
from signals.automatic_source_cache import store as store_source_cache
from signals.equity_bond_model import MODEL_VERSION, MonthlyInputs, build_feature_rows, walk_forward_forecasts
from sources.market import fetch_yahoo_adjusted, valid_fred_values


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
CACHE_COLLECTOR = "equity_bond_relative"
CACHE_SERIES = {"real_yield_10y": "DFII10", "nfci_level": "NFCI"}
# DFII10 starts on 2003-01-02. January 2003 is the earliest overlap
# available to every model source.
REQUIRED_HISTORY_THROUGH = date(2003, 1, 31)


def first_of_month(value: date) -> date:
    return value.replace(day=1)


def previous_completed_month(today: date) -> date:
    return first_of_month(today) - timedelta(days=1)


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


def load_retained_sources(database: SupabaseRest) -> dict[str, dict[date, float]]:
    code_to_key = {series_code: key for key, (series_code, _source) in SOURCE_CODES.items()}
    raw = {key: {} for key in SOURCE_CODES}
    offset = 0
    while True:
        rows = database.request(
            "GET", "equity_bond_source_monthly",
            params={
                "select": "series_code,observation_date,value",
                "order": "series_code.asc,month.asc", "offset": str(offset), "limit": "1000",
            },
        ) or []
        for row in rows:
            key = code_to_key.get(str(row.get("series_code")))
            if key:
                observed = date.fromisoformat(str(row["observation_date"])[:10])
                raw[key][observed] = float(row["value"])
        if len(rows) < 1000:
            break
        offset += len(rows)
    return raw


def source_cache_points(raw: dict[str, dict[date, float]]) -> dict[str, dict[date, tuple[date, float]]]:
    return {
        CACHE_SERIES[key]: {period: (period, value) for period, value in raw[key].items()}
        for key in CACHE_SERIES
    }


def cached_series_values(cache: dict[str, dict[date, tuple[date, float]]]) -> dict[str, dict[date, float]]:
    return {
        key: {period: value for period, (_observed, value) in cache.get(series, {}).items()}
        for key, series in CACHE_SERIES.items()
    }


def has_required_history(series: dict[str, dict[date, float]]) -> bool:
    return all(values and min(values) <= REQUIRED_HISTORY_THROUGH for values in series.values())


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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--initialize-sources", action="store_true")
    args = parser.parse_args()
    if args.dry_run and args.initialize_sources:
        raise SystemExit("--initialize-sources cannot be combined with --dry-run")
    today = date.today()
    end = previous_completed_month(today)
    calibration_start = date(2002, 1, 1)
    source_start = calibration_start if args.initialize_sources else month_start_months_ago(
        end, AUTOMATIC_MONTHLY_PERIODS - 1,
    )
    fred_api_key = require_env("FRED_API_KEY")
    database = SupabaseRest()
    cache = load_source_cache(database, CACHE_COLLECTOR)
    cached = cached_series_values(cache)
    retained = load_retained_sources(database)
    if not args.initialize_sources and (
        not is_initialized(cache)
        or not has_required_history(cached)
        or not has_required_history(retained)
    ):
        raise RuntimeError("Equity-bond model source cache is not initialized; run --initialize-sources explicitly")

    recent = {
        "spy_adjusted_close": fetch_yahoo_adjusted("SPY", source_start, end),
        "tlt_adjusted_close": fetch_yahoo_adjusted("TLT", source_start, end),
    }
    for key in FRED_SERIES:
        recent[key] = valid_fred_values(fetch_fred_observations(
            FRED_SERIES[key],
            fred_api_key,
            start=source_start.isoformat(),
            end=end.isoformat(),
        ))

    updated_at = datetime.now(timezone.utc).isoformat()
    recent_source_rows = source_rows(recent, updated_at)
    if not args.dry_run:
        upsert_batches(database, "equity_bond_source_monthly", recent_source_rows, "series_code,month")
        store_source_cache(database, CACHE_COLLECTOR, source_cache_points(recent))
        if args.initialize_sources:
            mark_initialized(database, CACHE_COLLECTOR, today)
            cache = load_source_cache(database, CACHE_COLLECTOR)

    if args.initialize_sources:
        retained = load_retained_sources(database)
    for key in SOURCE_CODES:
        retained[key].update(recent[key])
    for series, values in source_cache_points(recent).items():
        cache.setdefault(series, {}).update(values)
    cached = cached_series_values(cache)
    if not is_initialized(cache) or not has_required_history(cached):
        raise RuntimeError("Equity-bond model source cache is not initialized; run --initialize-sources explicitly")
    raw = {**retained, **cached}

    inputs = build_monthly_inputs(raw, start=calibration_start, end=end)
    features = build_feature_rows(inputs)
    forecasts = walk_forward_forecasts(features)
    if not forecasts:
        raise RuntimeError("No equity-bond forecasts were produced; verify source history and overlap.")
    retained_sources = recent_source_rows
    retained_forecasts = forecast_rows(forecasts, updated_at)
    if not args.dry_run:
        existing_forecasts = database.request(
            "GET", "equity_bond_relative_forecasts",
            params={"select": "forecast_month,outcome_status", "limit": "10000"},
        ) or []
        forecast_state = {str(row["forecast_month"]): str(row.get("outcome_status") or "") for row in existing_forecasts}
        retained_forecasts = [
            row for row in retained_forecasts
            if str(row["forecast_month"]) not in forecast_state
            or forecast_state[str(row["forecast_month"])] == "pending"
        ]
        upsert_batches(database, "equity_bond_relative_forecasts", retained_forecasts, "forecast_month")
    print(
        f"Equity-bond V1: source_start={source_start} initialized={args.initialize_sources} "
        f"inputs={len(inputs)} sources={len(retained_sources)} "
        f"forecasts={len(retained_forecasts)} latest={retained_forecasts[-1]['forecast_month']} "
        f"verdict={retained_forecasts[-1]['verdict']} dry_run={args.dry_run}"
    )


if __name__ == "__main__":
    main()
