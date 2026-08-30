"""Pure quarterly market earnings-breadth calculations.

This module intentionally performs no provider or database I/O.  The existing
canonical quarterly financials are sufficient input, so callers can recalculate
history after formula changes without downloading filings again.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence


HUNDRED = Decimal("100")


@dataclass(frozen=True, order=True)
class MarketQuarter:
    """Calendar-market quarter used by the canonical financial table."""

    year: int
    quarter: int

    def __post_init__(self) -> None:
        if not 1 <= self.quarter <= 4:
            raise ValueError("quarter must be between 1 and 4")

    def shift(self, quarters: int) -> "MarketQuarter":
        ordinal = self.year * 4 + self.quarter - 1 + quarters
        year, zero_based_quarter = divmod(ordinal, 4)
        return MarketQuarter(year, zero_based_quarter + 1)


@dataclass(frozen=True)
class OperatingIncomeObservation:
    company_id: str
    period: MarketQuarter
    operating_income: Decimal | None
    currency: str | None = None
    consolidation_scope: str | None = None


@dataclass(frozen=True)
class MarketEarningsBreadthResult:
    """One market universe's earnings breadth for one target quarter."""

    index_id: str
    market_year: int
    market_quarter: int
    universe_basis: str
    universe_company_count: int
    comparable_company_count: int
    is_provisional: bool
    company_coverage_pct: Decimal
    op_coverage_pct: Decimal | None
    current_total_op: Decimal | None
    prior_total_op: Decimal | None
    net_op_change: Decimal | None
    op_growth_pct: Decimal | None
    aggregate_turn: str
    positive_company_count: int
    negative_company_count: int
    unchanged_company_count: int
    earnings_breadth_pct: Decimal | None
    breadth_delta_pp: Decimal | None
    breadth_delta_comparable_count: int
    breadth_delta_company_coverage_pct: Decimal
    breadth_delta_op_coverage_pct: Decimal | None
    positive_contribution_total: Decimal
    negative_contribution_total_abs: Decimal
    top5_positive_contribution_share_pct: Decimal | None
    top5_negative_contribution_share_pct: Decimal | None
    negative_offset_ratio_pct: Decimal | None
    black_turn_count: int
    red_turn_count: int
    profit_turn_net: int
    classification: str

    def as_record(self) -> dict[str, Any]:
        """Return a JSON/PostgREST-safe record without rounding numeric values."""

        return {
            key: format(value, "f") if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def observations_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[OperatingIncomeObservation]:
    """Normalize rows selected from ``earnings_quarterly_financials``."""

    observations: list[OperatingIncomeObservation] = []
    for row in rows:
        observations.append(OperatingIncomeObservation(
            company_id=str(row["company_id"]),
            period=MarketQuarter(
                int(row.get("market_year", row.get("fiscal_year"))),
                int(row.get("market_quarter", row.get("fiscal_quarter"))),
            ),
            operating_income=_decimal(row.get("operating_income")),
            currency=str(row["currency"]) if row.get("currency") else None,
            consolidation_scope=(
                str(row["consolidation_scope"])
                if row.get("consolidation_scope") else None
            ),
        ))
    return observations


def _pct(numerator: Decimal | int, denominator: Decimal | int) -> Decimal | None:
    denominator_decimal = Decimal(denominator)
    if denominator_decimal == 0:
        return None
    return Decimal(numerator) / denominator_decimal * HUNDRED


def _top_share(values: Sequence[Decimal]) -> Decimal | None:
    total = sum(values, Decimal(0))
    if total <= 0:
        return None
    return _pct(sum(sorted(values, reverse=True)[:5], Decimal(0)), total)


def _aggregate_turn(prior: Decimal, current: Decimal) -> str:
    if prior <= 0 < current:
        return "black_turn"
    if prior >= 0 > current:
        return "red_turn"
    return "none"


def _classification(
    *,
    net_change: Decimal,
    positive_share: Decimal | None,
    negative_share: Decimal | None,
    positive_count: int,
    negative_count: int,
    comparable_count: int,
) -> str:
    """Apply the agreed initial rules; market-specific calibration can follow."""

    if comparable_count <= 0 or net_change == 0:
        return "flat_or_unavailable"
    if net_change > 0:
        breadth = _pct(positive_count, comparable_count)
        if breadth is not None and breadth >= 60 and positive_share is not None and positive_share <= 50:
            return "broad_growth"
        if breadth is not None and breadth >= 60 and positive_share is not None and positive_share >= 60:
            return "broad_improvement_large_cap_led"
        if breadth is not None and breadth < 50 and positive_share is not None and positive_share >= 60:
            return "concentrated_growth"
        if breadth is not None and breadth < 50 and positive_share is not None and positive_share <= 50:
            return "limited_improvement"
        return "mixed_growth"

    decline_breadth = _pct(negative_count, comparable_count)
    if decline_breadth is not None and decline_breadth >= 60 and negative_share is not None and negative_share <= 50:
        return "broad_deterioration"
    if decline_breadth is not None and decline_breadth < 50 and negative_share is not None and negative_share >= 60:
        return "large_company_shock"
    return "mixed_deterioration"


def calculate_market_earnings_breadth(
    *,
    index_id: str,
    target: MarketQuarter,
    universe_company_ids: Iterable[str],
    observations: Iterable[OperatingIncomeObservation],
    universe_basis: str = "current_snapshot_reconstruction",
) -> MarketEarningsBreadthResult:
    """Calculate one quarter from existing canonical operating-income values.

    ``Breadth`` uses each quarter's own t/t-4 comparable set. ``Breadth Delta``
    is recalculated on the stricter common cohort having t, t-1, t-4 and t-5.
    Black/red turns remain part of breadth and are also counted separately.
    """

    universe = {str(company_id) for company_id in universe_company_ids}
    if not universe:
        raise ValueError("universe_company_ids must not be empty")

    rows_by_key: dict[tuple[str, MarketQuarter], OperatingIncomeObservation] = {}
    for observation in observations:
        if observation.company_id not in universe or observation.operating_income is None:
            continue
        key = (observation.company_id, observation.period)
        if key in rows_by_key and rows_by_key[key] != observation:
            raise ValueError(f"conflicting operating income for {observation.company_id} {observation.period}")
        rows_by_key[key] = observation

    def value(company_id: str, period: MarketQuarter) -> Decimal:
        observed = rows_by_key[(company_id, period)].operating_income
        assert observed is not None
        return observed

    def compatible(company_id: str, periods: Sequence[MarketQuarter]) -> bool:
        selected = [rows_by_key.get((company_id, period)) for period in periods]
        if any(row is None for row in selected):
            return False
        currencies = {row.currency for row in selected if row and row.currency}
        scopes = {
            row.consolidation_scope for row in selected
            if row and row.consolidation_scope not in (None, "NA")
        }
        return len(currencies) <= 1 and len(scopes) <= 1

    prior = target.shift(-4)
    previous = target.shift(-1)
    previous_prior = target.shift(-5)
    comparable = sorted(
        company_id for company_id in universe
        if compatible(company_id, (target, prior))
    )

    current_total = sum((value(company_id, target) for company_id in comparable), Decimal(0))
    prior_total = sum((value(company_id, prior) for company_id in comparable), Decimal(0))
    net_change = current_total - prior_total
    deltas = [value(company_id, target) - value(company_id, prior) for company_id in comparable]
    positive = [delta for delta in deltas if delta > 0]
    negative_abs = [-delta for delta in deltas if delta < 0]

    positive_count = len(positive)
    negative_count = len(negative_abs)
    unchanged_count = len(comparable) - positive_count - negative_count
    positive_total = sum(positive, Decimal(0))
    negative_total_abs = sum(negative_abs, Decimal(0))
    top5_positive = _top_share(positive)
    top5_negative = _top_share(negative_abs)

    prior_population = [
        company_id for company_id in universe if (company_id, prior) in rows_by_key
    ]
    prior_population_abs = sum(
        (abs(value(company_id, prior)) for company_id in prior_population), Decimal(0)
    )
    comparable_prior_abs = sum(
        (abs(value(company_id, prior)) for company_id in comparable), Decimal(0)
    )

    delta_comparable = sorted(
        company_id for company_id in universe
        if compatible(company_id, (target, previous, prior, previous_prior))
    )
    current_delta_positive = sum(
        value(company_id, target) > value(company_id, prior)
        for company_id in delta_comparable
    )
    previous_delta_positive = sum(
        value(company_id, previous) > value(company_id, previous_prior)
        for company_id in delta_comparable
    )
    current_common_breadth = _pct(current_delta_positive, len(delta_comparable))
    previous_common_breadth = _pct(previous_delta_positive, len(delta_comparable))
    breadth_delta = (
        current_common_breadth - previous_common_breadth
        if current_common_breadth is not None and previous_common_breadth is not None else None
    )
    # Delta coverage spans both YoY baselines because the common-cohort rule
    # requires t-4 and t-5, not only the current comparison's baseline.
    delta_population_abs = sum((
        abs(value(company_id, period))
        for company_id in universe
        for period in (prior, previous_prior)
        if (company_id, period) in rows_by_key
    ), Decimal(0))
    delta_comparable_abs = sum((
        abs(value(company_id, period))
        for company_id in delta_comparable
        for period in (prior, previous_prior)
    ), Decimal(0))

    black_turn_count = sum(
        value(company_id, prior) <= 0 < value(company_id, target)
        for company_id in comparable
    )
    red_turn_count = sum(
        value(company_id, prior) >= 0 > value(company_id, target)
        for company_id in comparable
    )

    has_comparable = bool(comparable)
    return MarketEarningsBreadthResult(
        index_id=index_id,
        market_year=target.year,
        market_quarter=target.quarter,
        universe_basis=universe_basis,
        universe_company_count=len(universe),
        comparable_company_count=len(comparable),
        is_provisional=len(comparable) < len(universe),
        company_coverage_pct=_pct(len(comparable), len(universe)) or Decimal(0),
        op_coverage_pct=_pct(comparable_prior_abs, prior_population_abs),
        current_total_op=current_total if has_comparable else None,
        prior_total_op=prior_total if has_comparable else None,
        net_op_change=net_change if has_comparable else None,
        # No arbitrary small-positive threshold: observe real history first.
        op_growth_pct=_pct(net_change, prior_total) if prior_total > 0 else None,
        aggregate_turn=_aggregate_turn(prior_total, current_total) if has_comparable else "unavailable",
        positive_company_count=positive_count,
        negative_company_count=negative_count,
        unchanged_company_count=unchanged_count,
        earnings_breadth_pct=_pct(positive_count, len(comparable)),
        breadth_delta_pp=breadth_delta,
        breadth_delta_comparable_count=len(delta_comparable),
        breadth_delta_company_coverage_pct=_pct(len(delta_comparable), len(universe)) or Decimal(0),
        breadth_delta_op_coverage_pct=_pct(delta_comparable_abs, delta_population_abs),
        positive_contribution_total=positive_total,
        negative_contribution_total_abs=negative_total_abs,
        top5_positive_contribution_share_pct=top5_positive,
        top5_negative_contribution_share_pct=top5_negative,
        # Values above 100 are deliberately preserved; the UI can add a label.
        negative_offset_ratio_pct=_pct(negative_total_abs, positive_total),
        black_turn_count=black_turn_count,
        red_turn_count=red_turn_count,
        profit_turn_net=black_turn_count - red_turn_count,
        classification=_classification(
            net_change=net_change,
            positive_share=top5_positive,
            negative_share=top5_negative,
            positive_count=positive_count,
            negative_count=negative_count,
            comparable_count=len(comparable),
        ),
    )


def calculate_market_earnings_history(
    *,
    index_id: str,
    universe_company_ids: Iterable[str],
    observations: Iterable[OperatingIncomeObservation],
    universe_basis: str = "current_snapshot_reconstruction",
) -> list[MarketEarningsBreadthResult]:
    """Reconstruct all available YoY quarters for one fixed universe.

    This is the Python-side historical backfill entry point. It only reuses
    already stored quarterly values and therefore never calls DART, SEC or KIS.
    """

    universe = {str(company_id) for company_id in universe_company_ids}
    rows = list(observations)
    available_periods = {
        row.period for row in rows
        if row.company_id in universe and row.operating_income is not None
    }
    targets = sorted(
        period for period in available_periods if period.shift(-4) in available_periods
    )
    return [
        calculate_market_earnings_breadth(
            index_id=index_id,
            target=target,
            universe_company_ids=universe,
            observations=rows,
            universe_basis=universe_basis,
        )
        for target in targets
    ]
