"""Pure company-quarter growth and causal QoQ seasonal-adjustment logic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Any, Iterable, Mapping


METRICS = ("revenue", "operating_income", "net_income")
HUNDRED = Decimal("100")
CALCULATION_VERSION = 1
BASELINE_YEARS = 5
BASELINE_ELIGIBLE_STATES = frozenset({"normal", "negative_base"})


@dataclass(frozen=True)
class QuarterlyFinancial:
    company_id: str
    fiscal_year: int
    fiscal_quarter: int
    currency: str
    consolidation_scope: str
    canonical_version: int
    values: Mapping[str, Decimal | None]

    @property
    def ordinal(self) -> int:
        return self.fiscal_year * 4 + self.fiscal_quarter - 1


@dataclass
class GrowthMetric:
    company_id: str
    fiscal_year: int
    fiscal_quarter: int
    metric: str
    yoy_pct: Decimal | None
    yoy_state: str
    yoy_delta_pp: Decimal | None
    qoq_raw_pct: Decimal | None
    qoq_state: str
    qoq_seasonal_baseline_pct: Decimal | None
    qoq_seasonal_sample_count: int
    qoq_seasonally_adjusted_pct: Decimal | None
    qoq_seasonally_adjusted_delta_pp: Decimal | None
    source_canonical_version: int

    @property
    def ordinal(self) -> int:
        return self.fiscal_year * 4 + self.fiscal_quarter - 1

    def as_record(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "fiscal_year": self.fiscal_year,
            "fiscal_quarter": self.fiscal_quarter,
            "metric": self.metric,
            "yoy_pct": _serialize(self.yoy_pct),
            "yoy_state": self.yoy_state,
            "yoy_delta_pp": _serialize(self.yoy_delta_pp),
            "qoq_raw_pct": _serialize(self.qoq_raw_pct),
            "qoq_state": self.qoq_state,
            "qoq_seasonal_baseline_pct": _serialize(self.qoq_seasonal_baseline_pct),
            "qoq_seasonal_sample_count": self.qoq_seasonal_sample_count,
            "qoq_seasonally_adjusted_pct": _serialize(self.qoq_seasonally_adjusted_pct),
            "qoq_seasonally_adjusted_delta_pp": _serialize(self.qoq_seasonally_adjusted_delta_pp),
            "source_canonical_version": self.source_canonical_version,
            "calculation_version": CALCULATION_VERSION,
        }


def _serialize(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def financials_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[QuarterlyFinancial]:
    financials = []
    for row in rows:
        financials.append(QuarterlyFinancial(
            company_id=str(row["company_id"]),
            fiscal_year=int(row["fiscal_year"]),
            fiscal_quarter=int(row["fiscal_quarter"]),
            currency=str(row["currency"]),
            consolidation_scope=str(row["consolidation_scope"]),
            canonical_version=int(row["canonical_version"]),
            values={metric: _decimal(row.get(metric)) for metric in METRICS},
        ))
    return financials


def _growth_rate(
    current: QuarterlyFinancial,
    prior: QuarterlyFinancial | None,
    metric: str,
) -> tuple[Decimal | None, str]:
    if prior is None:
        return None, "missing_prior"
    if current.currency != prior.currency:
        return None, "currency_mismatch"
    if current.consolidation_scope != prior.consolidation_scope:
        return None, "scope_mismatch"
    current_value = current.values.get(metric)
    prior_value = prior.values.get(metric)
    if current_value is None or prior_value is None:
        return None, "missing_prior"
    if prior_value == 0:
        return None, "from_zero"
    if prior_value < 0 <= current_value:
        state = "black_turn"
    elif prior_value > 0 > current_value:
        state = "red_turn"
    elif prior_value < 0 and current_value < 0:
        state = "negative_base"
    else:
        state = "normal"
    # abs(prior) preserves the intuitive direction when both periods are
    # negative. The state prevents a turn or negative-base result from being
    # mistaken for an ordinary positive-base growth rate.
    return (current_value - prior_value) / abs(prior_value) * HUNDRED, state


def calculate_growth_metrics(financials: Iterable[QuarterlyFinancial]) -> list[GrowthMetric]:
    """Calculate all three metrics without looking at future seasonal samples."""

    source = list(financials)
    by_period: dict[tuple[str, int], QuarterlyFinancial] = {}
    for financial in source:
        key = (financial.company_id, financial.ordinal)
        if key in by_period:
            raise ValueError(f"duplicate company fiscal quarter: {key}")
        by_period[key] = financial

    derived: list[GrowthMetric] = []
    by_metric_period: dict[tuple[str, str, int], GrowthMetric] = {}
    for current in sorted(source, key=lambda row: (row.company_id, row.ordinal)):
        for metric in METRICS:
            yoy, yoy_state = _growth_rate(
                current, by_period.get((current.company_id, current.ordinal - 4)), metric
            )
            qoq, qoq_state = _growth_rate(
                current, by_period.get((current.company_id, current.ordinal - 1)), metric
            )
            result = GrowthMetric(
                company_id=current.company_id,
                fiscal_year=current.fiscal_year,
                fiscal_quarter=current.fiscal_quarter,
                metric=metric,
                yoy_pct=yoy,
                yoy_state=yoy_state,
                yoy_delta_pp=None,
                qoq_raw_pct=qoq,
                qoq_state=qoq_state,
                qoq_seasonal_baseline_pct=None,
                qoq_seasonal_sample_count=0,
                qoq_seasonally_adjusted_pct=None,
                qoq_seasonally_adjusted_delta_pp=None,
                source_canonical_version=current.canonical_version,
            )
            derived.append(result)
            by_metric_period[(current.company_id, metric, current.ordinal)] = result

    for current in derived:
        previous = by_metric_period.get((current.company_id, current.metric, current.ordinal - 1))
        if current.yoy_pct is not None and previous is not None and previous.yoy_pct is not None:
            current.yoy_delta_pp = current.yoy_pct - previous.yoy_pct

        seasonal_samples = []
        for year_offset in range(1, BASELINE_YEARS + 1):
            historical = by_metric_period.get((
                current.company_id,
                current.metric,
                current.ordinal - year_offset * 4,
            ))
            if (
                historical is not None
                and historical.qoq_raw_pct is not None
                and historical.qoq_state in BASELINE_ELIGIBLE_STATES
            ):
                seasonal_samples.append(historical.qoq_raw_pct)
        current.qoq_seasonal_sample_count = len(seasonal_samples)
        if current.qoq_raw_pct is not None and seasonal_samples:
            current.qoq_seasonal_baseline_pct = median(seasonal_samples)
            current.qoq_seasonally_adjusted_pct = (
                current.qoq_raw_pct - current.qoq_seasonal_baseline_pct
            )

        if (
            current.qoq_seasonally_adjusted_pct is not None
            and previous is not None
            and previous.qoq_seasonally_adjusted_pct is not None
        ):
            current.qoq_seasonally_adjusted_delta_pp = (
                current.qoq_seasonally_adjusted_pct
                - previous.qoq_seasonally_adjusted_pct
            )
    return derived
