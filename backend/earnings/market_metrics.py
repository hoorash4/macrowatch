"""Pure point-in-time-universe quarterly market earnings calculations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

from earnings.growth_metrics import METRICS, QuarterlyFinancial
from earnings.market_breadth import MarketQuarter
from earnings.market_universe import QuarterlyUniverse


HUNDRED = Decimal("100")
CALCULATION_VERSION = 2


@dataclass(frozen=True)
class MarketAggregateMetric:
    index_id: str
    fiscal_year: int
    fiscal_quarter: int
    metric: str
    currency: str
    universe_basis: str
    universe_company_count: int
    comparable_company_count: int
    delta_comparable_company_count: int
    company_coverage_pct: Decimal
    current_total: Decimal | None
    prior_total: Decimal | None
    yoy_pct: Decimal | None
    yoy_state: str
    previous_yoy_pct_common: Decimal | None
    yoy_delta_pp: Decimal | None
    is_provisional: bool

    def as_record(self) -> dict[str, Any]:
        return {
            key: format(value, "f") if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }


def _pct(numerator: Decimal | int, denominator: Decimal | int) -> Decimal | None:
    denominator = Decimal(denominator)
    return None if denominator == 0 else Decimal(numerator) / denominator * HUNDRED


def _state_and_growth(current: Decimal, prior: Decimal) -> tuple[str, Decimal | None]:
    """Return an explicit transition state and a conventional growth rate.

    The aggregate keeps every signed company amount. A percentage is only
    meaningful when the signed aggregate baseline is positive.
    """

    if prior <= 0 < current:
        return "black_turn", None
    if prior > 0 and current < 0:
        return "red_turn", (current - prior) / prior * HUNDRED
    if prior < 0 and current < 0:
        if current > prior:
            return "loss_narrowing", None
        if current < prior:
            return "loss_widening", None
        return "loss_unchanged", None
    if prior == 0:
        return "from_zero", None
    return "normal", (current - prior) / prior * HUNDRED


def calculate_market_metric_history(
    *,
    index_id: str,
    currency: str,
    universes_by_period: Mapping[MarketQuarter, QuarterlyUniverse],
    financials: Iterable[QuarterlyFinancial],
    universe_basis: str = "point_in_time_market_cap_snapshot",
) -> list[MarketAggregateMetric]:
    """Calculate each quarter from that quarter's actual market-cap universe.

    The current total and its year-ago baseline deliberately use two different
    point-in-time constituent sets.  Membership change is part of the market
    aggregate, rather than a reason to project today's companies backwards.
    """

    if not universes_by_period:
        return []
    source = list(financials)
    by_period = {(row.company_id, row.market_ordinal): row for row in source}
    results: list[MarketAggregateMetric] = []
    result_by_metric_period: dict[tuple[str, MarketQuarter], MarketAggregateMetric] = {}

    for period, current_universe in sorted(universes_by_period.items()):
        ordinal = period.year * 4 + period.quarter - 1
        prior_period = period.shift(-4)
        prior_universe = universes_by_period.get(prior_period)
        for metric in METRICS:
            current_rows = [
                by_period[(company_id, ordinal)]
                for company_id in current_universe.company_ids
                if (company_id, ordinal) in by_period
                and by_period[(company_id, ordinal)].currency == currency
                and by_period[(company_id, ordinal)].values.get(metric) is not None
            ]
            if not current_rows:
                continue
            current_total = sum(
                (row.values[metric] for row in current_rows),
                Decimal(0),
            )
            assert current_total is not None

            prior_rows = [] if prior_universe is None else [
                by_period[(company_id, ordinal - 4)]
                for company_id in prior_universe.company_ids
                if (company_id, ordinal - 4) in by_period
                and by_period[(company_id, ordinal - 4)].currency == currency
                and by_period[(company_id, ordinal - 4)].values.get(metric) is not None
            ]
            prior_total = (
                sum((row.values[metric] for row in prior_rows), Decimal(0))
                if prior_rows else None
            )
            state, yoy = (
                _state_and_growth(current_total, prior_total)
                if prior_total is not None else ("missing_prior_snapshot", None)
            )

            previous = result_by_metric_period.get((metric, period.shift(-1)))
            previous_yoy = previous.yoy_pct if previous else None
            delta = (
                yoy - previous_yoy
                if state == "normal" and yoy is not None and previous
                and previous.yoy_state == "normal" and previous_yoy is not None
                else None
            )

            result = MarketAggregateMetric(
                index_id=index_id,
                fiscal_year=period.year,
                fiscal_quarter=period.quarter,
                metric=metric,
                currency=currency,
                universe_basis=universe_basis,
                universe_company_count=len(current_universe.company_ids),
                # These legacy column names are retained in storage for a
                # migration-safe rollout. They now mean current/prior reported
                # counts, never a fixed historical cohort.
                comparable_company_count=len(current_rows),
                delta_comparable_company_count=len(prior_rows),
                company_coverage_pct=(
                    _pct(len(current_rows), len(current_universe.company_ids)) or Decimal(0)
                ),
                current_total=current_total,
                prior_total=prior_total,
                yoy_pct=yoy,
                yoy_state=state,
                previous_yoy_pct_common=previous_yoy,
                yoy_delta_pp=delta,
                is_provisional=(
                    len(current_rows) < len(current_universe.company_ids)
                    or prior_universe is None
                    or len(prior_rows) < len(prior_universe.company_ids)
                ),
            )
            results.append(result)
            result_by_metric_period[(metric, period)] = result
    return results
