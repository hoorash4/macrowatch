from __future__ import annotations

from datetime import date
from decimal import Decimal
from statistics import median
from typing import Iterable

from .models import GrowthResult, MarketQuarter, QuarterValue


HUNDRED = Decimal("100")
MAX_SEASONAL_YEARS = 15
MAX_SEASONAL_SAMPLES = 10
MIN_SEASONAL_SAMPLES = 2


def conventional_growth(
    current: Decimal | None,
    previous: Decimal | None,
    *,
    compatible: bool = True,
    incompatible_state: str = "scope_mismatch",
) -> GrowthResult:
    """Return a conventional percentage only when that percentage is honest.

    Profit/loss transitions deliberately produce a state and a null number.
    The UI can therefore break the line and describe the actual transition
    instead of inventing a percentage with a misleading denominator.
    """
    if current is None or previous is None:
        return GrowthResult(None, "missing_prior")
    if not compatible:
        return GrowthResult(None, incompatible_state)
    if previous == 0:
        return GrowthResult(None, "from_zero")
    if previous < 0:
        if current >= 0:
            return GrowthResult(None, "black_turn")
        if current > previous:
            return GrowthResult(None, "loss_narrowing")
        if current < previous:
            return GrowthResult(None, "loss_widening")
        return GrowthResult(None, "loss_unchanged")
    if current < 0:
        return GrowthResult(None, "red_turn")
    return GrowthResult(((current - previous) / previous) * HUNDRED, "normal")


def _quarter_ordinal(year: int, quarter: int) -> int:
    return year * 4 + quarter - 1


def _raw_qoq(
    current: QuarterValue,
    previous: QuarterValue,
    field: str,
) -> GrowthResult:
    if current.currency != previous.currency:
        return GrowthResult(None, "currency_mismatch")
    if current.consolidation_scope != previous.consolidation_scope:
        return GrowthResult(None, "scope_mismatch")
    return conventional_growth(getattr(current, field), getattr(previous, field))


def _seasonal_baseline(
    ordered: list[QuarterValue],
    target_index: int,
    field: str,
) -> list[Decimal]:
    target = ordered[target_index]
    target_transition = target.fiscal_quarter
    target_ordinal = _quarter_ordinal(target.fiscal_year, target.fiscal_quarter)
    samples: list[tuple[int, Decimal]] = []

    # Only transitions that were already known before the target are eligible.
    for index in range(1, target_index):
        candidate = ordered[index]
        prior = ordered[index - 1]
        candidate_ordinal = _quarter_ordinal(candidate.fiscal_year, candidate.fiscal_quarter)
        if candidate_ordinal - _quarter_ordinal(prior.fiscal_year, prior.fiscal_quarter) != 1:
            continue
        if candidate.fiscal_quarter != target_transition:
            continue
        if target_ordinal - candidate_ordinal > MAX_SEASONAL_YEARS * 4:
            continue
        raw = _raw_qoq(candidate, prior, field)
        if raw.state == "normal" and raw.value is not None:
            samples.append((candidate_ordinal, raw.value))

    samples.sort(key=lambda item: item[0], reverse=True)
    return [value for _, value in samples[:MAX_SEASONAL_SAMPLES]]


def calculate_company_growth(rows: Iterable[QuarterValue]) -> list[QuarterValue]:
    ordered = sorted(rows, key=lambda row: row.key)
    by_key = {row.key: row for row in ordered}
    results: list[QuarterValue] = []

    for index, current in enumerate(ordered):
        previous_year = by_key.get((current.fiscal_year - 1, current.fiscal_quarter))
        previous_quarter = ordered[index - 1] if index else None
        updates: dict[str, object] = {}
        for field, prefix in (
            ("operating_income", "operating_income"),
            ("net_income", "net_income"),
        ):
            if previous_year is None:
                yoy = GrowthResult(None, "missing_prior")
            elif current.currency != previous_year.currency:
                yoy = GrowthResult(None, "currency_mismatch")
            elif current.consolidation_scope != previous_year.consolidation_scope:
                yoy = GrowthResult(None, "scope_mismatch")
            else:
                yoy = conventional_growth(getattr(current, field), getattr(previous_year, field))
            updates[f"{prefix}_yoy_pct"] = yoy.value
            updates[f"{prefix}_yoy_state"] = yoy.state

            consecutive = (
                previous_quarter is not None
                and _quarter_ordinal(current.fiscal_year, current.fiscal_quarter)
                - _quarter_ordinal(previous_quarter.fiscal_year, previous_quarter.fiscal_quarter)
                == 1
            )
            raw = _raw_qoq(current, previous_quarter, field) if consecutive else GrowthResult(None, "missing_prior")
            if raw.state != "normal" or raw.value is None:
                qoq = raw
            else:
                samples = _seasonal_baseline(ordered, index, field)
                qoq = (
                    GrowthResult(raw.value - Decimal(str(median(samples))), "normal")
                    if len(samples) >= MIN_SEASONAL_SAMPLES
                    else GrowthResult(None, "insufficient_history")
                )
            updates[f"{prefix}_qoq_sa_pct"] = qoq.value
            updates[f"{prefix}_qoq_state"] = qoq.state

        results.append(current.with_metrics(**updates))
    return results


def calculate_market_growth(rows: Iterable[MarketQuarter]) -> list[MarketQuarter]:
    """Apply the exact company rules to the already aggregated market series."""
    ordered = sorted(rows, key=lambda row: row.key)
    synthetic = [
        QuarterValue(
            company_id=row.market_id,
            fiscal_year=row.market_year,
            fiscal_quarter=row.market_quarter,
            market_year=row.market_year,
            market_quarter=row.market_quarter,
            period_end=date(row.market_year, row.market_quarter * 3, 1),
            top_line=None,
            operating_income=row.average_operating_income,
            net_income=row.average_net_income,
            currency="UNIT",
            consolidation_scope="CFS",
        )
        for row in ordered
    ]
    calculated = calculate_company_growth(synthetic)
    return [
        source.with_metrics(
            operating_income_yoy_pct=metric.operating_income_yoy_pct,
            operating_income_yoy_state=metric.operating_income_yoy_state,
            net_income_yoy_pct=metric.net_income_yoy_pct,
            net_income_yoy_state=metric.net_income_yoy_state,
            operating_income_qoq_sa_pct=metric.operating_income_qoq_sa_pct,
            operating_income_qoq_state=metric.operating_income_qoq_state,
            net_income_qoq_sa_pct=metric.net_income_qoq_sa_pct,
            net_income_qoq_state=metric.net_income_qoq_state,
        )
        for source, metric in zip(ordered, calculated, strict=True)
    ]
