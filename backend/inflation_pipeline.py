"""Collect, calculate, backfill, and publish the MacroWatch U.S. inflation model."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import quote

import numpy as np
import requests

try:
    from .common import SupabaseRest, fetch_fred_observations, require_env
    from .inflation_lead_model import (
        MODEL_VERSION, ProducerCalibration, align_producer_inflation,
        fisher_real_rate_pct, fit_ridge, predict_ridge,
        select_direction_ridge_alpha, select_ridge_alpha, shelter_adjusted_cpi_yoy,
    )
except ImportError:  # Direct script execution used by GitHub Actions.
    from common import SupabaseRest, fetch_fred_observations, require_env
    from inflation_lead_model import (
        MODEL_VERSION, ProducerCalibration, align_producer_inflation,
        fisher_real_rate_pct, fit_ridge, predict_ridge,
        select_direction_ridge_alpha, select_ridge_alpha, shelter_adjusted_cpi_yoy,
    )


TIMEOUT_SECONDS = 60
SOURCE_START = date(2009, 1, 1)
PUBLISH_START = date(2020, 1, 1)
CLEVELAND_MONTHLY_URL = (
    "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/"
    "nowcast_month.json?sc_lang=en"
)
BLS_TIMESERIES_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_CORE_CPI_EX_SHELTER = "CUSR0000SA0L12E"
YAHOO_CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)

FRED_SERIES = {
    "cpi": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "shelter": "CUSR0000SAH1",
    "cpi_ex_shelter": "CUSR0000SA0L2",
    "pce": "PCEPI",
    "core_pce": "PCEPILFE",
    "headline_ppi": "WPSFD49501",
    "core_ppi": "WPSFD49511",
    "dollar": "DTWEXBGS",
    "policy_rate": "DFEDTARU",
}
COMMODITY_GROUPS = {
    "energy": ("CL=F", "RB=F", "HO=F", "NG=F"),
    "food": ("ZC=F", "ZW=F", "ZS=F", "ZL=F", "ZM=F", "LE=F", "HE=F", "DC=F", "SB=F", "KC=F", "CC=F"),
    "industrial": ("HG=F", "ALI=F", "HRC=F", "CT=F"),
}
OFFICIAL_SHELTER_WEIGHTS = {"headline": 0.356, "core": 0.446}


@dataclass(frozen=True)
class NowcastPoint:
    observed_on: date
    cpi_mom_pct: float
    pce_mom_pct: float


@dataclass(frozen=True)
class ComponentRow:
    month: date
    pce: float
    cpi: float
    ppi: float
    target_lag1: float
    target_lag3: float


def month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def latest_on_or_before(values: dict[date, float], cutoff: date) -> float | None:
    candidates = [key for key in values if key <= cutoff]
    return values[max(candidates)] if candidates else None


def fetch_fred_series(api_key: str, today: date) -> dict[str, dict[date, float]]:
    output: dict[str, dict[date, float]] = {}
    for name, series_id in FRED_SERIES.items():
        rows = fetch_fred_observations(
            series_id,
            api_key,
            start=SOURCE_START.isoformat(),
            end=today.isoformat(),
            timeout=TIMEOUT_SECONDS,
        )
        values: dict[date, float] = {}
        for row in rows:
            raw_date, raw_value = row.get("date"), row.get("value")
            if not isinstance(raw_date, str) or raw_value in (None, "."):
                continue
            try:
                values[date.fromisoformat(raw_date)] = float(raw_value)
            except (TypeError, ValueError):
                continue
        if not values:
            raise RuntimeError(f"FRED {series_id} returned no usable observations")
        output[name] = values
    return output


def fetch_bls_series(series_id: str, today: date) -> dict[date, float]:
    """Fetch a long monthly BLS series in public-API-sized year blocks."""

    values: dict[date, float] = {}
    for start_year in range(SOURCE_START.year, today.year + 1, 10):
        end_year = min(start_year + 9, today.year)
        response = requests.post(
            BLS_TIMESERIES_URL,
            json={
                "seriesid": [series_id],
                "startyear": str(start_year),
                "endyear": str(end_year),
            },
            headers={"User-Agent": "MacroWatch inflation research/2.0"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS {series_id} request failed: {payload.get('message')}")
        series = payload.get("Results", {}).get("series") or []
        if not series:
            raise RuntimeError(f"BLS {series_id} returned no series")
        for row in series[0].get("data", []):
            period = str(row.get("period", ""))
            if not period.startswith("M") or period == "M13":
                continue
            try:
                observed_on = date(int(row["year"]), int(period[1:]), 1)
                values[observed_on] = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
    if not values:
        raise RuntimeError(f"BLS {series_id} returned no usable observations")
    return values


def fetch_yahoo_series(symbol: str, today: date) -> dict[date, float]:
    params = {
        "period1": int(datetime.combine(SOURCE_START, datetime.min.time(), tzinfo=timezone.utc).timestamp()),
        "period2": int(datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()),
        "interval": "1d",
        "events": "history",
    }
    response = None
    for attempt in range(6):
        url = YAHOO_CHART_URLS[attempt % len(YAHOO_CHART_URLS)].format(symbol=quote(symbol, safe=""))
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": "Mozilla/5.0 MacroWatch inflation research"},
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code not in (429, 500, 502, 503, 504):
            break
        if attempt < 5:
            import time
            time.sleep(min(2 ** attempt, 20))
    if response is None:
        raise RuntimeError(f"Yahoo {symbol} did not return a response")
    response.raise_for_status()
    result = (response.json().get("chart", {}).get("result") or [{}])[0]
    timestamps = result.get("timestamp") or []
    closes = ((result.get("indicators", {}).get("quote") or [{}])[0].get("close") or [])
    values: dict[date, float] = {}
    for stamp, close in zip(timestamps, closes):
        if close is None:
            continue
        try:
            values[datetime.fromtimestamp(int(stamp), tz=timezone.utc).date()] = float(close)
        except (TypeError, ValueError, OSError):
            continue
    if not values:
        raise RuntimeError(f"Yahoo {symbol} returned no usable observations")
    return values


def parse_chart_date(label: str, target: date) -> date | None:
    try:
        month, day = (int(part) for part in label.split("/"))
    except (AttributeError, TypeError, ValueError):
        return None
    year = target.year + (1 if month < target.month else 0)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def fetch_cleveland_nowcasts() -> dict[str, dict[date, list[NowcastPoint]]]:
    response = requests.get(
        CLEVELAND_MONTHLY_URL,
        headers={"User-Agent": "MacroWatch inflation research/2.0"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    output = {"headline": {}, "core": {}}
    names = {
        "headline": ("CPI Inflation", "PCE Inflation"),
        "core": ("Core CPI Inflation", "Core PCE Inflation"),
    }
    for item in payload if isinstance(payload, list) else []:
        try:
            target = datetime.strptime(item["chart"]["subcaption"], "%Y-%m").date().replace(day=1)
        except (KeyError, TypeError, ValueError):
            try:
                target = datetime.strptime(item["chart"]["subcaption"], "%Y-%m-%d").date().replace(day=1)
            except (KeyError, TypeError, ValueError):
                continue
        labels = item.get("categories", [{}])[0].get("category", [])
        datasets = {entry.get("seriesname"): entry.get("data", []) for entry in item.get("dataset", [])}
        for kind, (cpi_name, pce_name) in names.items():
            cpi_values, pce_values = datasets.get(cpi_name, []), datasets.get(pce_name, [])
            points = []
            for index, label in enumerate(labels):
                observed_on = parse_chart_date(label.get("label", ""), target)
                if observed_on is None or month_start(observed_on) != target:
                    continue
                try:
                    cpi = float(cpi_values[index].get("value"))
                    pce = float(pce_values[index].get("value"))
                except (IndexError, TypeError, ValueError):
                    continue
                points.append(NowcastPoint(observed_on, cpi, pce))
            if points:
                output[kind][target] = sorted(points, key=lambda point: point.observed_on)
    if not output["headline"] or not output["core"]:
        raise RuntimeError("Cleveland Fed nowcast history was empty or malformed")
    return output


def monthly_average(values: dict[date, float], cutoff: date | None = None) -> dict[date, float]:
    grouped: dict[date, list[float]] = {}
    for observed_on, value in values.items():
        if cutoff is not None and observed_on > cutoff:
            continue
        grouped.setdefault(month_start(observed_on), []).append(value)
    return {month: float(statistics.fmean(items)) for month, items in grouped.items() if items}


def yoy_levels(values: dict[date, float]) -> dict[date, float]:
    output = {}
    for month, value in values.items():
        prior = values.get(add_months(month, -12))
        if prior and value > 0:
            output[month] = 100.0 * (value / prior - 1.0)
    return output


def changes(values: dict[date, float]) -> dict[date, float]:
    return {
        month: value - values[add_months(month, -1)]
        for month, value in values.items()
        if add_months(month, -1) in values
    }


def adjusted_cpi_levels(fred: dict[str, dict[date, float]], kind: str) -> dict[date, float]:
    shelter = yoy_levels(fred["shelter"])
    ex_name = "cpi_ex_shelter" if kind == "headline" else "core_cpi_ex_shelter"
    ex_shelter = yoy_levels(fred[ex_name])
    return {
        month: shelter_adjusted_cpi_yoy(
            shelter_yoy_pct=shelter[month],
            ex_shelter_yoy_pct=ex_shelter[month],
            official_shelter_weight=OFFICIAL_SHELTER_WEIGHTS[kind],
        )
        for month in shelter.keys() & ex_shelter.keys()
    }


def integrated_levels(
    fred: dict[str, dict[date, float]], kind: str
) -> tuple[dict[date, float], dict[date, float], ProducerCalibration]:
    pce_name = "pce" if kind == "headline" else "core_pce"
    ppi_name = "headline_ppi" if kind == "headline" else "core_ppi"
    pce = yoy_levels(fred[pce_name])
    cpi = adjusted_cpi_levels(fred, kind)
    ppi = yoy_levels(fred[ppi_name])
    calibration_months = sorted(
        month for month in pce.keys() & cpi.keys() & ppi.keys() if month < PUBLISH_START
    )
    consumer = [(0.60 * pce[month] + 0.30 * cpi[month]) / 0.90 for month in calibration_months]
    producer = [ppi[month] for month in calibration_months]
    if len(consumer) < 24 or float(np.std(producer)) < 1e-9:
        raise RuntimeError(f"Insufficient pre-2020 {kind} PPI calibration history")
    calibration = ProducerCalibration(
        consumer_mean_pct=float(np.mean(consumer)),
        producer_mean_pct=float(np.mean(producer)),
        producer_to_consumer_scale=float(np.std(consumer) / np.std(producer)),
    )
    aligned_ppi = {month: align_producer_inflation(value, calibration) for month, value in ppi.items()}
    levels = {
        month: 0.60 * pce[month] + 0.30 * cpi[month] + 0.10 * aligned_ppi[month]
        for month in pce.keys() & cpi.keys() & aligned_ppi.keys()
    }
    return levels, aligned_ppi, calibration


class MarketFeatures:
    def __init__(self, prices: dict[str, dict[date, float]], dollar: dict[date, float]) -> None:
        self.prices = prices
        self.dollar = dollar
        self.price_monthly = {symbol: monthly_average(values) for symbol, values in prices.items()}
        self.dollar_monthly = monthly_average(dollar)

    @staticmethod
    def _return(current: float | None, previous: float | None) -> float:
        return 100.0 * (current / previous - 1.0) if current and previous else float("nan")

    def _series_return(self, values: dict[date, float], monthly: dict[date, float], target: date, cutoff: date) -> float:
        current_values = [value for day, value in values.items() if month_start(day) == target and day <= cutoff]
        previous = monthly.get(add_months(target, -1))
        return self._return(float(statistics.fmean(current_values)) if current_values else None, previous)

    def _historical_return(self, monthly: dict[date, float], target: date) -> float:
        return self._return(monthly.get(target), monthly.get(add_months(target, -1)))

    def row(self, kind: str, target: date, cutoff: date) -> list[float]:
        groups = ("energy", "food", "industrial") if kind == "headline" else ("industrial",)
        output: list[float] = []
        for group in groups:
            current = [
                self._series_return(self.prices[symbol], self.price_monthly[symbol], target, cutoff)
                for symbol in COMMODITY_GROUPS[group]
            ]
            current_value = float(np.nanmedian(current))
            prior = []
            for lag in (2, 1):
                values = [self._historical_return(self.price_monthly[symbol], add_months(target, -lag)) for symbol in COMMODITY_GROUPS[group]]
                prior.append(float(np.nanmedian(values)))
            output.extend([float(np.clip(current_value, -40.0, 40.0)), float(np.nanmean([*prior, current_value]))])
        dollar_current = self._series_return(self.dollar, self.dollar_monthly, target, cutoff)
        dollar_prior = [self._historical_return(self.dollar_monthly, add_months(target, -lag)) for lag in (2, 1)]
        output.extend([dollar_current, float(np.nanmean([*dollar_prior, dollar_current]))])
        return output


def yoy_delta_from_mom(index: dict[date, float], target: date, mom_pct: float) -> float:
    prior, year_ago = index.get(add_months(target, -1)), index.get(add_months(target, -12))
    prior_year_ago = index.get(add_months(target, -13))
    if not all(finite(value) and float(value) > 0 for value in (prior, year_ago, prior_year_ago)):
        return float("nan")
    prior_yoy = 100.0 * (float(prior) / float(prior_year_ago) - 1.0)
    predicted_yoy = 100.0 * (float(prior) * (1.0 + mom_pct / 100.0) / float(year_ago) - 1.0)
    return predicted_yoy - prior_yoy


def adjusted_cpi_nowcast_delta(
    fred: dict[str, dict[date, float]], kind: str, target: date, total_mom_pct: float
) -> float:
    total_name = "cpi" if kind == "headline" else "core_cpi"
    ex_name = "cpi_ex_shelter" if kind == "headline" else "core_cpi_ex_shelter"
    prior = add_months(target, -1)
    shelter_mom = []
    for lag in range(3):
        month = add_months(prior, -lag)
        previous = fred["shelter"].get(add_months(month, -1))
        current = fred["shelter"].get(month)
        if previous and current:
            shelter_mom.append(100.0 * (current / previous - 1.0))
    if not shelter_mom:
        return float("nan")
    shelter_estimate = float(statistics.fmean(shelter_mom))
    official = OFFICIAL_SHELTER_WEIGHTS[kind]
    ex_estimate = (total_mom_pct - official * shelter_estimate) / (1.0 - official)
    adjusted = official * 0.90
    shelter_delta = yoy_delta_from_mom(fred["shelter"], target, shelter_estimate)
    ex_delta = yoy_delta_from_mom(fred[ex_name], target, ex_estimate)
    # Ensure the total index history needed by the source definition exists.
    if add_months(target, -1) not in fred[total_name]:
        return float("nan")
    return adjusted * shelter_delta + (1.0 - adjusted) * ex_delta


def fit_model(features: list[list[float]], targets: list[float], *, direction: bool = False):
    matrix = np.asarray(features, dtype=float)
    outcome = np.asarray(targets, dtype=float)
    if len(outcome) < 36 or not np.isfinite(matrix).all() or not np.isfinite(outcome).all():
        return None
    alpha = select_direction_ridge_alpha(matrix, outcome) if direction else select_ridge_alpha(matrix, outcome)
    return fit_ridge(matrix, outcome, alpha=alpha)


def lag_values(target_change: dict[date, float], target: date) -> tuple[float, float]:
    previous = [target_change.get(add_months(target, -lag)) for lag in (1, 2, 3)]
    finite_values = [float(value) for value in previous if finite(value)]
    lag1 = float(previous[0]) if finite(previous[0]) else (finite_values[0] if finite_values else float("nan"))
    return lag1, float(statistics.fmean(finite_values)) if finite_values else float("nan")


def build_component_rows(
    kind: str,
    fred: dict[str, dict[date, float]],
    nowcasts: dict[date, list[NowcastPoint]],
    features: MarketFeatures,
    target_change: dict[date, float],
    ppi_change: dict[date, float],
) -> tuple[dict[date, ComponentRow], dict[date, object]]:
    rows: dict[date, ComponentRow] = {}
    ppi_models: dict[date, object] = {}
    pce_name = "pce" if kind == "headline" else "core_pce"
    feature_cache: dict[date, list[float]] = {}
    for target in sorted(nowcasts):
        cutoff = nowcasts[target][-1].observed_on
        feature_cache[target] = features.row(kind, target, cutoff)
        history_months = [
            month for month in sorted(feature_cache)
            if month < target and month in ppi_change and np.isfinite(feature_cache[month]).all()
        ]
        model = fit_model([feature_cache[month] for month in history_months], [ppi_change[month] for month in history_months])
        if model is None or not np.isfinite(feature_cache[target]).all():
            continue
        ppi_models[target] = model
        point = nowcasts[target][-1]
        pce_delta = yoy_delta_from_mom(fred[pce_name], target, point.pce_mom_pct)
        cpi_delta = adjusted_cpi_nowcast_delta(fred, kind, target, point.cpi_mom_pct)
        ppi_delta = predict_ridge(model, feature_cache[target])
        lag1, lag3 = lag_values(target_change, target)
        if all(finite(value) for value in (pce_delta, cpi_delta, ppi_delta, lag1, lag3)):
            rows[target] = ComponentRow(target, pce_delta, cpi_delta, ppi_delta, lag1, lag3)
    return rows, ppi_models


def headline_models(rows: dict[date, ComponentRow], target_change: dict[date, float]) -> dict[date, object]:
    models = {}
    for target in sorted(rows):
        history = [month for month in sorted(rows) if month < target and month in target_change]
        x = [[rows[month].pce, rows[month].cpi, rows[month].ppi, rows[month].target_lag1, rows[month].target_lag3] for month in history]
        model = fit_model(x, [target_change[month] for month in history], direction=True)
        if model is not None:
            models[target] = model
    return models


def core_residual_models(
    core_rows: dict[date, ComponentRow],
    headline_rows: dict[date, ComponentRow],
    core_target_change: dict[date, float],
) -> dict[date, object]:
    models = {}
    for target in sorted(core_rows.keys() & headline_rows.keys()):
        history = [
            month for month in sorted(core_rows.keys() & headline_rows.keys())
            if month < target and month in core_target_change
        ]
        x = [[headline_rows[month].pce, headline_rows[month].cpi, headline_rows[month].ppi] for month in history]
        residual = [
            core_target_change[month]
            - (0.60 * core_rows[month].pce + 0.30 * core_rows[month].cpi + 0.10 * core_rows[month].ppi)
            for month in history
        ]
        model = fit_model(x, residual)
        if model is not None:
            models[target] = model
    return models


def component_for_point(
    kind: str,
    fred: dict[str, dict[date, float]],
    features: MarketFeatures,
    target: date,
    point: NowcastPoint,
    ppi_model,
    target_change: dict[date, float],
) -> ComponentRow | None:
    pce_name = "pce" if kind == "headline" else "core_pce"
    market = features.row(kind, target, point.observed_on)
    pce = yoy_delta_from_mom(fred[pce_name], target, point.pce_mom_pct)
    cpi = adjusted_cpi_nowcast_delta(fred, kind, target, point.cpi_mom_pct)
    ppi = predict_ridge(ppi_model, market) if np.isfinite(market).all() else float("nan")
    lag1, lag3 = lag_values(target_change, target)
    if not all(finite(value) for value in (pce, cpi, ppi, lag1, lag3)):
        return None
    return ComponentRow(target, pce, cpi, ppi, lag1, lag3)


def previous_level(levels: dict[date, float], target: date) -> float | None:
    candidates = [month for month in levels if month < target]
    return levels[max(candidates)] if candidates else None


def build_output_rows(
    fred: dict[str, dict[date, float]],
    nowcasts: dict[str, dict[date, list[NowcastPoint]]],
    features: MarketFeatures,
    headline_levels: dict[date, float],
    core_levels: dict[date, float],
    headline_ppi: dict[date, float],
    core_ppi: dict[date, float],
    publish_start: date,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    headline_change, core_change = changes(headline_levels), changes(core_levels)
    headline_rows, headline_ppi_models = build_component_rows(
        "headline", fred, nowcasts["headline"], features, headline_change, changes(headline_ppi)
    )
    core_rows, core_ppi_models = build_component_rows(
        "core", fred, nowcasts["core"], features, core_change, changes(core_ppi)
    )
    head_models = headline_models(headline_rows, headline_change)
    core_models = core_residual_models(core_rows, headline_rows, core_change)
    daily: list[dict[str, object]] = []
    targets = sorted(nowcasts["headline"].keys() & nowcasts["core"].keys())
    for target in targets:
        if target not in head_models or target not in core_models or target not in headline_ppi_models or target not in core_ppi_models:
            continue
        headline_anchor, core_anchor = previous_level(headline_levels, target), previous_level(core_levels, target)
        if headline_anchor is None or core_anchor is None:
            continue
        core_points = {point.observed_on: point for point in nowcasts["core"][target]}
        for head_point in nowcasts["headline"][target]:
            if head_point.observed_on < publish_start or head_point.observed_on not in core_points:
                continue
            core_point = core_points[head_point.observed_on]
            head = component_for_point(
                "headline", fred, features, target, head_point, headline_ppi_models[target], headline_change
            )
            core = component_for_point(
                "core", fred, features, target, core_point, core_ppi_models[target], core_change
            )
            if head is None or core is None:
                continue
            headline_delta = predict_ridge(
                head_models[target], [head.pce, head.cpi, head.ppi, head.target_lag1, head.target_lag3]
            )
            core_base = 0.60 * core.pce + 0.30 * core.cpi + 0.10 * core.ppi
            core_correction = predict_ridge(core_models[target], [head.pce, head.cpi, head.ppi])
            headline_yoy, core_yoy = headline_anchor + headline_delta, core_anchor + core_base + core_correction
            policy = latest_on_or_before(fred["policy_rate"], head_point.observed_on)
            daily.append({
                "observed_on": head_point.observed_on.isoformat(),
                "target_month": target.isoformat(),
                "headline_leading_yoy_pct": round(headline_yoy, 4),
                "core_leading_yoy_pct": round(core_yoy, 4),
                "policy_rate_upper_pct": round(policy, 4) if policy is not None else None,
                "headline_real_rate_pct": round(fisher_real_rate_pct(policy, headline_yoy), 4) if policy is not None else None,
                "core_real_rate_pct": round(fisher_real_rate_pct(policy, core_yoy), 4) if policy is not None else None,
                "model_version": MODEL_VERSION,
            })
    daily.sort(key=lambda row: str(row["observed_on"]))
    # PCE is released after month-end. During that short gap the model still
    # targets the just-completed month, so carry its final within-month estimate
    # to the latest Cleveland business date instead of making the chart appear
    # stale or jumping ahead without a usable PCE starting level.
    latest_source_day = max(
        point.observed_on
        for kind_rows in nowcasts.values()
        for points in kind_rows.values()
        for point in points
    )
    if daily and date.fromisoformat(str(daily[-1]["observed_on"])) < latest_source_day:
        carried = dict(daily[-1])
        carried["observed_on"] = latest_source_day.isoformat()
        policy = latest_on_or_before(fred["policy_rate"], latest_source_day)
        carried["policy_rate_upper_pct"] = round(policy, 4) if policy is not None else None
        if policy is not None:
            carried["headline_real_rate_pct"] = round(
                fisher_real_rate_pct(policy, float(carried["headline_leading_yoy_pct"])), 4
            )
            carried["core_real_rate_pct"] = round(
                fisher_real_rate_pct(policy, float(carried["core_leading_yoy_pct"])), 4
            )
        daily.append(carried)

    monthly: list[dict[str, object]] = []
    final_months = sorted(month for month in headline_levels.keys() & core_levels.keys() if month >= publish_start)
    for month in final_months:
        cutoff = add_months(month, 1) - timedelta(days=1)
        policy = latest_on_or_before(fred["policy_rate"], cutoff)
        monthly.append({
            "month": month.isoformat(),
            "headline_yoy_pct": round(headline_levels[month], 4),
            "core_yoy_pct": round(core_levels[month], 4),
            "policy_rate_upper_pct": round(policy, 4) if policy is not None else None,
            "headline_real_rate_pct": round(fisher_real_rate_pct(policy, headline_levels[month]), 4) if policy is not None else None,
            "core_real_rate_pct": round(fisher_real_rate_pct(policy, core_levels[month]), 4) if policy is not None else None,
            "status": "final",
            "model_version": MODEL_VERSION,
            "data_as_of": cutoff.isoformat(),
        })
    if daily:
        latest = daily[-1]
        target = date.fromisoformat(str(latest["target_month"]))
        if target not in headline_levels or target not in core_levels:
            monthly.append({
                "month": target.isoformat(),
                "headline_yoy_pct": latest["headline_leading_yoy_pct"],
                "core_yoy_pct": latest["core_leading_yoy_pct"],
                "policy_rate_upper_pct": latest["policy_rate_upper_pct"],
                "headline_real_rate_pct": latest["headline_real_rate_pct"],
                "core_real_rate_pct": latest["core_real_rate_pct"],
                "status": "provisional",
                "model_version": MODEL_VERSION,
                "data_as_of": latest["observed_on"],
            })
    return monthly, daily


def batched(rows: list[dict[str, object]], size: int = 400) -> Iterable[list[dict[str, object]]]:
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def save_backfill(client: SupabaseRest, start: date, monthly: list[dict[str, object]], daily: list[dict[str, object]]) -> None:
    client.request("DELETE", "us_inflation_leading_daily", params={"observed_on": f"gte.{start.isoformat()}"}, prefer="return=minimal")
    client.request("DELETE", "us_inflation_monthly", params={"month": f"gte.{start.isoformat()}"}, prefer="return=minimal")
    for rows in batched(monthly):
        client.upsert("us_inflation_monthly", rows, conflict="month")
    for rows in batched(daily):
        client.upsert("us_inflation_leading_daily", rows, conflict="observed_on")


def save_automatic(client: SupabaseRest, monthly: list[dict[str, object]], daily: list[dict[str, object]]) -> None:
    existing = client.request("GET", "us_inflation_monthly", params={"select": "month,status", "limit": "500"}) or []
    status_by_month = {row["month"]: row["status"] for row in existing}
    selected_monthly = [
        row for row in monthly
        if row["status"] == "provisional"
        or row["month"] not in status_by_month
        or status_by_month[row["month"]] == "provisional"
    ]
    if selected_monthly:
        client.upsert("us_inflation_monthly", selected_monthly, conflict="month")
    if daily:
        client.upsert("us_inflation_leading_daily", daily[-1], conflict="observed_on")


def verify_saved(client: SupabaseRest, expected_month: str, expected_day: str) -> None:
    monthly = client.request(
        "GET", "us_inflation_monthly",
        params={"select": "month,status,model_version", "order": "month.desc", "limit": "1"},
    ) or []
    daily = client.request(
        "GET", "us_inflation_leading_daily",
        params={"select": "observed_on,target_month,model_version", "order": "observed_on.desc", "limit": "1"},
    ) or []
    if not monthly or monthly[0].get("month") != expected_month:
        raise RuntimeError("Monthly inflation verification did not return the expected latest row")
    if not daily or daily[0].get("observed_on") != expected_day:
        raise RuntimeError("Daily inflation verification did not return the expected latest row")
    if monthly[0].get("model_version") != MODEL_VERSION or daily[0].get("model_version") != MODEL_VERSION:
        raise RuntimeError("Inflation verification found an unexpected model version")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("automatic", "backfill"), default="automatic")
    parser.add_argument("--start", type=date.fromisoformat, default=PUBLISH_START)
    args = parser.parse_args()
    if args.start < PUBLISH_START:
        raise SystemExit("Backfill start cannot precede 2020-01-01")

    fred_key = require_env("FRED_API_KEY")
    today = date.today()
    fred = fetch_fred_series(fred_key, today)
    fred["core_cpi_ex_shelter"] = fetch_bls_series(BLS_CORE_CPI_EX_SHELTER, today)
    nowcasts = fetch_cleveland_nowcasts()
    prices = {
        symbol: fetch_yahoo_series(symbol, today)
        for symbols in COMMODITY_GROUPS.values()
        for symbol in symbols
    }
    features = MarketFeatures(prices, fred["dollar"])
    headline_levels, headline_ppi, _ = integrated_levels(fred, "headline")
    core_levels, core_ppi, _ = integrated_levels(fred, "core")
    monthly, daily = build_output_rows(
        fred, nowcasts, features, headline_levels, core_levels, headline_ppi, core_ppi, args.start
    )
    monthly = [row for row in monthly if str(row["month"]) >= args.start.isoformat()]
    daily = [row for row in daily if str(row["observed_on"]) >= args.start.isoformat()]
    if not monthly or not daily:
        raise RuntimeError("Inflation calculation produced no publishable rows")

    updated_at = datetime.now(timezone.utc).isoformat()
    for row in monthly:
        row["updated_at"] = updated_at
    for row in daily:
        row["updated_at"] = updated_at

    client = SupabaseRest(
        url=require_env("SUPABASE_URL"),
        service_key=require_env("SUPABASE_SERVICE_ROLE_KEY"),
        timeout=TIMEOUT_SECONDS,
    )
    if args.mode == "backfill":
        save_backfill(client, args.start, monthly, daily)
    else:
        save_automatic(client, monthly, daily)
    verify_saved(client, str(monthly[-1]["month"]), str(daily[-1]["observed_on"]))
    print(json.dumps({
        "mode": args.mode,
        "model_version": MODEL_VERSION,
        "monthly_rows_calculated": len(monthly),
        "daily_rows_calculated": len(daily),
        "latest_month": monthly[-1]["month"],
        "latest_day": daily[-1]["observed_on"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
