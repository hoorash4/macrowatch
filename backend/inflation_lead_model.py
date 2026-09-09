"""Pure calculations for the MacroWatch inflation prototype.

The integrated headline series is PCE-compatible: a published PCE value is the
monthly anchor.  CPI and PPI are release bridges for a month whose PCE value is
not available yet.  Market prices estimate the change from that anchor; they
are not treated as a second official inflation index.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


MODEL_VERSION = "inflation_lead_v0"
RIDGE_ALPHAS = (0.3, 1.0, 3.0, 10.0, 30.0, 100.0)


@dataclass(frozen=True)
class RidgeModel:
    intercept: float
    weights: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    alpha: float


@dataclass(frozen=True)
class IntegratedInflation:
    headline_yoy_pct: float
    core_yoy_pct: float | None
    status: str
    source: str


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


def choose_integrated_inflation(
    *,
    published_pce_yoy_pct: float | None,
    published_core_pce_yoy_pct: float | None,
    bridged_pce_yoy_pct: float | None,
    bridged_core_pce_yoy_pct: float | None = None,
) -> IntegratedInflation:
    """Return a final PCE anchor or the CPI/PPI bridge when PCE is pending."""

    if published_pce_yoy_pct is not None:
        return IntegratedInflation(
            headline_yoy_pct=float(published_pce_yoy_pct),
            core_yoy_pct=None if published_core_pce_yoy_pct is None else float(published_core_pce_yoy_pct),
            status="final",
            source="PCE",
        )
    if bridged_pce_yoy_pct is None:
        raise ValueError("a PCE value or a bridge estimate is required")
    return IntegratedInflation(
        headline_yoy_pct=float(bridged_pce_yoy_pct),
        core_yoy_pct=None if bridged_core_pce_yoy_pct is None else float(bridged_core_pce_yoy_pct),
        status="provisional",
        source="CPI/PPI bridge",
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

