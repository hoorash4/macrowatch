"""Pure fixed-universe quarterly market earnings aggregate calculations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Iterable, Sequence

from earnings.growth_metrics import METRICS, QuarterlyFinancial
from earnings.market_breadth import MarketQuarter


HUNDRED = Decimal("100")
CALCULATION_VERSION = 1


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


def _compatible(rows: Sequence[QuarterlyFinancial], currency: str) -> bool:
    if not rows or any(row.values is None or row.currency != currency for row in rows):
        return False
    scopes = {
        row.consolidation_scope for row in rows
        if row.consolidation_scope not in ("", "NA")
    }
    return len(scopes) <= 1


def calculate_market_metric_history(
    *,
    index_id: str,
    currency: str,
    universe_company_ids: Iterable[str],
    financials: Iterable[QuarterlyFinancial],
    universe_basis: str = "current_membership_reconstruction",
) -> list[MarketAggregateMetric]:
    """Calculate three signed market aggregates and their YoY acceleration.

    Headline YoY uses each quarter's t/t-4 comparable cohort. YoY delta is
    recomputed on the stricter t/t-1/t-4/t-5 common cohort so a membership or
    missing-data change cannot masquerade as acceleration.
    """

    universe = {str(company_id) for company_id in universe_company_ids}
    if not universe:
        return []
    source = [row for row in financials if row.company_id in universe]
    by_period = {(row.company_id, row.ordinal): row for row in source}
    ordinals = sorted({row.ordinal for row in source})
    results: list[MarketAggregateMetric] = []

    for ordinal in ordinals:
        year, zero_quarter = divmod(ordinal, 4)
        quarter = zero_quarter + 1
        for metric in METRICS:
            def eligible(company_id: str, offsets: Sequence[int]) -> bool:
                selected = [by_period.get((company_id, ordinal + offset)) for offset in offsets]
                if any(row is None for row in selected):
                    return False
                typed = [row for row in selected if row is not None]
                return _compatible(typed, currency) and all(
                    row.values.get(metric) is not None for row in typed
                )

            comparable = sorted(
                company_id for company_id in universe if eligible(company_id, (0, -4))
            )
            if not comparable:
                continue
            current_total = sum(
                (by_period[(company_id, ordinal)].values[metric] for company_id in comparable),
                Decimal(0),
            )
            prior_total = sum(
                (by_period[(company_id, ordinal - 4)].values[metric] for company_id in comparable),
                Decimal(0),
            )
            assert current_total is not None and prior_total is not None
            state, yoy = _state_and_growth(current_total, prior_total)

            delta_cohort = sorted(
                company_id for company_id in universe
                if eligible(company_id, (0, -1, -4, -5))
            )
            previous_yoy = delta = None
            if delta_cohort:
                common_current = sum(
                    (by_period[(company_id, ordinal)].values[metric] for company_id in delta_cohort),
                    Decimal(0),
                )
                common_prior = sum(
                    (by_period[(company_id, ordinal - 4)].values[metric] for company_id in delta_cohort),
                    Decimal(0),
                )
                common_previous = sum(
                    (by_period[(company_id, ordinal - 1)].values[metric] for company_id in delta_cohort),
                    Decimal(0),
                )
                common_previous_prior = sum(
                    (by_period[(company_id, ordinal - 5)].values[metric] for company_id in delta_cohort),
                    Decimal(0),
                )
                assert all(value is not None for value in (
                    common_current, common_prior, common_previous, common_previous_prior
                ))
                common_state, common_yoy = _state_and_growth(common_current, common_prior)
                previous_state, previous_yoy = _state_and_growth(
                    common_previous, common_previous_prior
                )
                if (
                    common_state == previous_state == "normal"
                    and common_yoy is not None and previous_yoy is not None
                ):
                    delta = common_yoy - previous_yoy

            results.append(MarketAggregateMetric(
                index_id=index_id,
                fiscal_year=year,
                fiscal_quarter=quarter,
                metric=metric,
                currency=currency,
                universe_basis=universe_basis,
                universe_company_count=len(universe),
                comparable_company_count=len(comparable),
                delta_comparable_company_count=len(delta_cohort),
                company_coverage_pct=_pct(len(comparable), len(universe)) or Decimal(0),
                current_total=current_total,
                prior_total=prior_total,
                yoy_pct=yoy,
                yoy_state=state,
                previous_yoy_pct_common=previous_yoy,
                yoy_delta_pp=delta,
                is_provisional=len(comparable) < len(universe),
            ))
    return results
