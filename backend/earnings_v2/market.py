from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from .growth import calculate_market_growth
from .models import MarketQuarter


TARGETS = {"kr_largecap": 100, "kr_kosdaq": 50, "us_largecap": 100, "us_nasdaq": 100}


def aggregate_market_quarter(
    *,
    market_id: str,
    market_year: int,
    market_quarter: int,
    company_values: Iterable[tuple[Decimal | None, Decimal | None, str]],
    historical: bool,
) -> MarketQuarter:
    """Average the final point-in-time cohort, never company growth rates."""
    if market_id not in TARGETS:
        raise ValueError(f"Unsupported market_id: {market_id}")
    rows = list(company_values)
    complete = [(op, net) for op, net, quality in rows if quality == "complete" and op is not None and net is not None]
    target = TARGETS[market_id]
    actual = len(complete)
    if not complete:
        average_op = average_net = None
    else:
        average_op = sum((op for op, _ in complete), Decimal("0")) / Decimal(actual)
        average_net = sum((net for _, net in complete), Decimal("0")) / Decimal(actual)

    if actual == target:
        status = "complete"
    elif historical and actual > 0:
        status = "historical_partial"
    else:
        status = "incomplete"
    return MarketQuarter(
        market_id=market_id,
        market_year=market_year,
        market_quarter=market_quarter,
        average_operating_income=average_op,
        average_net_income=average_net,
        actual_company_count=actual,
        target_company_count=target,
        completion_status=status,
    )


def calculate_market_series(rows: Iterable[MarketQuarter]) -> list[MarketQuarter]:
    return calculate_market_growth(rows)

