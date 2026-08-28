"""Pure V1 equity-versus-long-Treasury model calculations.

The target is the following 12-month SPY adjusted return minus the following
12-month TLT adjusted return.  This module performs no HTTP or database I/O so
the feature contract and walk-forward boundary can be tested independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import exp, log, sqrt
from typing import Iterable, Mapping, Sequence

import numpy as np


MODEL_VERSION = "equity_bond_relative_v1"
FEATURE_NAMES = (
    "relative_momentum_6m",
    "real_yield_expanding_percentile",
    "yield_curve_10y_2y",
    "baa_spread_change_3m",
    "nfci_level",
)
MIN_PERCENTILE_HISTORY = 36
MIN_TRAINING_SAMPLES = 84
L2_PENALTY = 0.10


@dataclass(frozen=True)
class MonthlyInputs:
    month: date
    spy_adjusted_close: float
    tlt_adjusted_close: float
    real_yield_10y: float
    yield_curve_10y_2y: float
    baa_spread: float
    nfci_level: float
    source_through_date: date


@dataclass(frozen=True)
class FeatureRow:
    month: date
    source_through_date: date
    features: tuple[float, ...]
    target_end_month: date | None
    future_relative_return_pct: float | None


@dataclass(frozen=True)
class Forecast:
    month: date
    source_through_date: date
    features: tuple[float, ...]
    stock_probability: float
    expected_relative_return_pct: float
    downside_q25_pct: float
    verdict: str
    training_start_month: date
    training_end_month: date
    training_sample_count: int
    actual_relative_return_pct: float | None
    validation: Mapping[str, float | int | str]


def shift_month(month: date, offset: int) -> date:
    """Shift a first-of-month date without depending on a date library."""

    ordinal = month.year * 12 + month.month - 1 + offset
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def expanding_percentile(values: Sequence[float], index: int) -> float:
    """Return a causal mid-rank percentile using only values through index."""

    history = values[: index + 1]
    current = history[-1]
    below = sum(value < current for value in history)
    equal = sum(value == current for value in history)
    return (below + 0.5 * equal) / len(history)


def build_feature_rows(inputs: Iterable[MonthlyInputs]) -> list[FeatureRow]:
    """Build fixed V1 features and non-overlapping-calendar 12-month labels."""

    ordered = sorted(inputs, key=lambda item: item.month)
    by_month = {item.month: item for item in ordered}
    real_yields = [item.real_yield_10y for item in ordered]
    rows: list[FeatureRow] = []
    for index, item in enumerate(ordered):
        if index + 1 < MIN_PERCENTILE_HISTORY:
            continue
        six_month_prior = by_month.get(shift_month(item.month, -6))
        three_month_prior = by_month.get(shift_month(item.month, -3))
        if six_month_prior is None or three_month_prior is None:
            continue
        relative_momentum = 100.0 * log(
            (item.spy_adjusted_close / six_month_prior.spy_adjusted_close)
            / (item.tlt_adjusted_close / six_month_prior.tlt_adjusted_close)
        )
        target_month = shift_month(item.month, 12)
        target = by_month.get(target_month)
        future_relative_return = None
        if target is not None:
            stock_return = target.spy_adjusted_close / item.spy_adjusted_close - 1.0
            bond_return = target.tlt_adjusted_close / item.tlt_adjusted_close - 1.0
            future_relative_return = 100.0 * (stock_return - bond_return)
        rows.append(FeatureRow(
            month=item.month,
            source_through_date=item.source_through_date,
            features=(
                relative_momentum,
                expanding_percentile(real_yields, index),
                item.yield_curve_10y_2y,
                item.baa_spread - three_month_prior.baa_spread,
                item.nfci_level,
            ),
            target_end_month=target_month if target is not None else None,
            future_relative_return_pct=future_relative_return,
        ))
    return rows


def _standardize(
    training: Sequence[Sequence[float]],
    current: Sequence[float],
) -> tuple[list[list[float]], list[float]]:
    dimensions = len(current)
    means = [sum(row[column] for row in training) / len(training) for column in range(dimensions)]
    deviations = []
    for column, mean in enumerate(means):
        variance = sum((row[column] - mean) ** 2 for row in training) / len(training)
        deviations.append(sqrt(variance) if variance > 1e-12 else 1.0)
    normalized_training = [
        [(row[column] - means[column]) / deviations[column] for column in range(dimensions)]
        for row in training
    ]
    normalized_current = [
        (current[column] - means[column]) / deviations[column] for column in range(dimensions)
    ]
    return normalized_training, normalized_current


def _dot(weights: Sequence[float], values: Sequence[float]) -> float:
    return sum(weight * value for weight, value in zip(weights, values))


def _sigmoid(value: float) -> float:
    if value >= 0:
        factor = exp(-min(value, 700.0))
        return 1.0 / (1.0 + factor)
    factor = exp(max(value, -700.0))
    return factor / (1.0 + factor)


def fit_logistic_l2(
    features: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    penalty: float = L2_PENALTY,
    iterations: int = 2500,
    learning_rate: float = 0.05,
) -> tuple[float, list[float]]:
    """Fit a small deterministic L2 logistic model with batch gradients."""

    matrix = np.asarray(features, dtype=float)
    outcome = np.asarray(labels, dtype=float)
    dimensions = matrix.shape[1]
    positive_rate = min(max(sum(labels) / len(labels), 1e-5), 1.0 - 1e-5)
    intercept = log(positive_rate / (1.0 - positive_rate))
    weights = [0.0] * dimensions
    for iteration in range(iterations):
        linear = np.clip(intercept + matrix @ np.asarray(weights), -700.0, 700.0)
        error = 1.0 / (1.0 + np.exp(-linear)) - outcome
        intercept_gradient = float(error.mean())
        gradients = matrix.T @ error / len(matrix) + penalty * np.asarray(weights)
        scale = learning_rate / sqrt(1.0 + iteration / 250.0)
        intercept -= scale * intercept_gradient
        weights = (np.asarray(weights) - scale * gradients).tolist()
    return intercept, weights


def _sample_quantile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def fit_quantile_l2(
    features: Sequence[Sequence[float]],
    targets: Sequence[float],
    quantile: float,
    *,
    penalty: float = 0.03,
    iterations: int = 3500,
    learning_rate: float = 0.025,
) -> tuple[float, list[float]]:
    """Fit a regularized linear quantile model with deterministic Adam steps."""

    matrix = np.asarray(features, dtype=float)
    outcome = np.asarray(targets, dtype=float)
    dimensions = matrix.shape[1]
    parameters = np.asarray([_sample_quantile(targets, quantile), *([0.0] * dimensions)])
    first_moment = np.zeros_like(parameters)
    second_moment = np.zeros_like(parameters)
    beta1, beta2 = 0.9, 0.999
    for iteration in range(1, iterations + 1):
        prediction = parameters[0] + matrix @ parameters[1:]
        derivative = np.where(outcome > prediction, -quantile, np.where(outcome < prediction, 1.0 - quantile, 0.0))
        gradients = np.concatenate((
            np.asarray([derivative.mean()]),
            matrix.T @ derivative / len(matrix) + penalty * parameters[1:],
        ))
        first_moment = beta1 * first_moment + (1.0 - beta1) * gradients
        second_moment = beta2 * second_moment + (1.0 - beta2) * gradients * gradients
        corrected_first = first_moment / (1.0 - beta1 ** iteration)
        corrected_second = second_moment / (1.0 - beta2 ** iteration)
        parameters -= learning_rate * corrected_first / (np.sqrt(corrected_second) + 1e-8)
    return float(parameters[0]), parameters[1:].tolist()


def verdict(probability: float, median_return: float) -> str:
    if probability >= 0.65 and median_return > 0:
        return "equity"
    if probability <= 0.35 and median_return < 0:
        return "long_treasury"
    return "neutral"


def walk_forward_forecasts(
    rows: Sequence[FeatureRow],
    *,
    minimum_training_samples: int = MIN_TRAINING_SAMPLES,
) -> list[Forecast]:
    """Create forecasts using only labels whose 12-month outcomes are known."""

    forecasts: list[Forecast] = []
    for current in sorted(rows, key=lambda item: item.month):
        # A label is usable only after its full 12-month endpoint has arrived.
        training = [
            row for row in rows
            if row.month < current.month
            and row.target_end_month is not None
            and row.target_end_month <= current.month
            and row.future_relative_return_pct is not None
        ]
        if len(training) < minimum_training_samples:
            continue
        raw_features = [list(row.features) for row in training]
        normalized, normalized_current = _standardize(raw_features, current.features)
        targets = [float(row.future_relative_return_pct) for row in training]
        labels = [int(target > 0) for target in targets]
        if len(set(labels)) < 2:
            continue
        logistic_intercept, logistic_weights = fit_logistic_l2(normalized, labels)
        median_intercept, median_weights = fit_quantile_l2(normalized, targets, 0.50)
        q25_intercept, q25_weights = fit_quantile_l2(normalized, targets, 0.25)
        probability = _sigmoid(logistic_intercept + _dot(logistic_weights, normalized_current))
        median = median_intercept + _dot(median_weights, normalized_current)
        q25 = q25_intercept + _dot(q25_weights, normalized_current)
        baseline_correct = [
            int((row.features[0] > 0) == (float(row.future_relative_return_pct) > 0))
            for row in training
        ]
        actual = current.future_relative_return_pct
        forecasts.append(Forecast(
            month=current.month,
            source_through_date=current.source_through_date,
            features=current.features,
            stock_probability=probability,
            expected_relative_return_pct=median,
            downside_q25_pct=min(q25, median),
            verdict=verdict(probability, median),
            training_start_month=training[0].month,
            training_end_month=training[-1].month,
            training_sample_count=len(training),
            actual_relative_return_pct=actual,
            validation={
                "baseline_6m_direction_accuracy": sum(baseline_correct) / len(baseline_correct),
                "feature_count": len(FEATURE_NAMES),
                "purge_months": 12,
            },
        ))
    return forecasts
