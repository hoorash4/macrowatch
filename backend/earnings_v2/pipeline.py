from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .growth import calculate_company_growth
from .market import aggregate_market_quarter, calculate_market_series
from .models import MarketQuarter, QuarterValue


@dataclass(frozen=True)
class QuarterCoverage:
    market_id: str
    year: int
    quarter: int
    target_count: int
    universe_count: int
    financial_count: int
    missing_company_ids: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.universe_count == self.target_count and self.financial_count == self.target_count


def prepare_company_series(rows: Iterable[QuarterValue]) -> list[QuarterValue]:
    """Validate durable facts and calculate every UI value before persistence."""
    source = list(rows)
    if not source:
        return []
    company_ids = {row.company_id for row in source}
    if len(company_ids) != 1:
        raise ValueError("prepare_company_series accepts one company at a time")
    keys = [row.key for row in source]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate company fiscal quarter")

    normalized = []
    for row in source:
        complete = row.top_line is not None and row.operating_income is not None and row.net_income is not None
        normalized.append(row.with_metrics(quality_status="complete" if complete else "review_required"))
    return calculate_company_growth(normalized)


def build_market_series(
    *,
    market_id: str,
    quarter_inputs: Iterable[tuple[int, int, list[tuple[Decimal | None, Decimal | None, str]], bool]],
) -> list[MarketQuarter]:
    rows = [
        aggregate_market_quarter(
            market_id=market_id,
            market_year=year,
            market_quarter=quarter,
            company_values=values,
            historical=historical,
        )
        for year, quarter, values, historical in quarter_inputs
    ]
    return calculate_market_series(rows)


def coverage_report(
    *,
    market_id: str,
    year: int,
    quarter: int,
    target_count: int,
    universe_company_ids: Iterable[str],
    complete_financial_company_ids: Iterable[str],
) -> QuarterCoverage:
    universe = set(universe_company_ids)
    complete = set(complete_financial_company_ids)
    return QuarterCoverage(
        market_id=market_id,
        year=year,
        quarter=quarter,
        target_count=target_count,
        universe_count=len(universe),
        financial_count=len(universe & complete),
        missing_company_ids=tuple(sorted(universe - complete)),
    )
