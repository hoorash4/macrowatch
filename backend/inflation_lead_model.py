"""Pure calculations for the MacroWatch integrated-inflation model.

Headline and core are separate composites.  Each combines CPI, PCE, and a
matching PPI measure.  PPI is first aligned to consumer-inflation volatility so
that its larger swings do not dominate the common percentage scale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


MODEL_VERSION = "inflation_lead_v1"
RIDGE_ALPHAS = (0.3, 1.0, 3.0, 10.0, 30.0, 100.0)
DEFAULT_COMPOSITE_WEIGHTS = (0.30, 0.50, 0.20)


@dataclass(frozen=True)
class RidgeModel:
    intercept: float
    weights: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    alpha: float


@dataclass(frozen=True)
class IntegratedInflation:
    yoy_pct: float
    status: str
    cpi_yoy_pct: float
    pce_yoy_pct: float
    aligned_ppi_yoy_pct: float


@dataclass(frozen=True)
class ProducerCalibration:
    consumer_mean_pct: float
    producer_mean_pct: float
    producer_to_consumer_scale: float


def fit_ridge(
    features: Sequence[Sequence[float]],
    targets: Sequence[float],
    *,
    alpha: float,
) -> RidgeModel:
    """Fit a standardized ridge model without penalizing the intercept."""

    matrix = np.asarray(features, dtype=float)
    outcome = np.asarray(targets, dtype=float)
    if matrix.ndim != 2 or not len(matrix) or matrix.shape[0] != len(outcome):
        raise ValueError("features and targets must contain matching non-empty rows")
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales < 1e-9] = 1.0
    standardized = (matrix - means) / scales
    intercept = float(outcome.mean())
    weights = np.linalg.solve(
        standardized.T @ standardized + alpha * np.eye(matrix.shape[1]),
        standardized.T @ (outcome - intercept),
    )
    return RidgeModel(
        intercept=intercept,
        weights=tuple(float(value) for value in weights),
        means=tuple(float(value) for value in means),
        scales=tuple(float(value) for value in scales),
        alpha=float(alpha),
    )


def predict_ridge(model: RidgeModel, features: Sequence[float]) -> float:
    values = np.asarray(features, dtype=float)
    if len(values) != len(model.weights):
        raise ValueError("feature count does not match the fitted model")
    standardized = (values - np.asarray(model.means)) / np.asarray(model.scales)
    return float(model.intercept + standardized @ np.asarray(model.weights))


def select_ridge_alpha(
    features: Sequence[Sequence[float]],
    targets: Sequence[float],
    *,
    minimum_training_rows: int = 48,
) -> float:
    """Select regularization with a causal expanding validation window."""

    matrix = np.asarray(features, dtype=float)
    outcome = np.asarray(targets, dtype=float)
    start = max(minimum_training_rows, len(outcome) - 36)
    if len(outcome) <= start + 6:
        return 10.0
    scores: dict[float, float] = {}
    for alpha in RIDGE_ALPHAS:
        errors = []
        for index in range(start, len(outcome)):
            model = fit_ridge(matrix[:index], outcome[:index], alpha=alpha)
            errors.append(abs(predict_ridge(model, matrix[index]) - outcome[index]))
        scores[alpha] = float(np.mean(errors))
    return min(scores, key=scores.get)


def calibrate_producer_inflation(
    *,
    consumer_yoy_history: Sequence[float],
    producer_yoy_history: Sequence[float],
) -> ProducerCalibration:
    """Fit the fixed pre-backtest scale used to align PPI with CPI/PCE."""

    consumer = np.asarray(consumer_yoy_history, dtype=float)
    producer = np.asarray(producer_yoy_history, dtype=float)
    if len(consumer) < 24 or len(consumer) != len(producer):
        raise ValueError("matching calibration histories need at least 24 months")
    producer_deviation = float(producer.std())
    if producer_deviation < 1e-9:
        raise ValueError("producer calibration history must vary")
    return ProducerCalibration(
        consumer_mean_pct=float(consumer.mean()),
        producer_mean_pct=float(producer.mean()),
        producer_to_consumer_scale=float(consumer.std() / producer_deviation),
    )


def align_producer_inflation(ppi_yoy_pct: float, calibration: ProducerCalibration) -> float:
    return calibration.consumer_mean_pct + calibration.producer_to_consumer_scale * (
        ppi_yoy_pct - calibration.producer_mean_pct
    )


def integrated_inflation_yoy(
    *,
    cpi_yoy_pct: float,
    pce_yoy_pct: float,
    ppi_yoy_pct: float,
    producer_calibration: ProducerCalibration,
    status: str,
    weights: Sequence[float] = DEFAULT_COMPOSITE_WEIGHTS,
) -> IntegratedInflation:
    """Combine CPI, PCE, and volatility-aligned PPI on one percent scale."""

    if status not in {"provisional", "final"}:
        raise ValueError("status must be provisional or final")
    if len(weights) != 3 or any(weight < 0.0 for weight in weights) or abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError("three non-negative weights must sum to one")
    aligned_ppi = align_producer_inflation(ppi_yoy_pct, producer_calibration)
    return IntegratedInflation(
        yoy_pct=float(weights[0] * cpi_yoy_pct + weights[1] * pce_yoy_pct + weights[2] * aligned_ppi),
        status=status,
        cpi_yoy_pct=float(cpi_yoy_pct),
        pce_yoy_pct=float(pce_yoy_pct),
        aligned_ppi_yoy_pct=float(aligned_ppi),
    )


def fisher_real_rate_pct(nominal_rate_pct: float, inflation_yoy_pct: float) -> float:
    """Calculate the exact Fisher real rate in percentage points."""

    denominator = 1.0 + inflation_yoy_pct / 100.0
    if denominator <= 0.0:
        raise ValueError("inflation must be greater than -100 percent")
    return 100.0 * ((1.0 + nominal_rate_pct / 100.0) / denominator - 1.0)


def month_to_date_average_return_pct(
    current_month_prices: Sequence[float],
    prior_month_average: float,
) -> float:
    """Convert daily prices into the month-to-date pressure used by the model."""

    if not current_month_prices or prior_month_average <= 0.0:
        raise ValueError("prices must be non-empty and prior average must be positive")
    return 100.0 * (float(np.mean(current_month_prices)) / prior_month_average - 1.0)
