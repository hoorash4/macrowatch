from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from .growth import calculate_market_growth
from .financials import profit_margin
from .models import MarketQuarter


TARGETS = {"kr_largecap": 100, "kr_kosdaq": 50, "us_largecap": 100, "us_nasdaq": 100}


def aggregate_market_quarter(
    *,
    market_id: str,
    market_year: int,
    market_quarter: int,
    company_values: Iterable[
        tuple[Decimal | None, Decimal | None, Decimal | None, str]
    ],
    historical: bool,
) -> MarketQuarter:
    """Average the final point-in-time cohort, never company growth rates."""
    if market_id not in TARGETS:
        raise ValueError(f"Unsupported market_id: {market_id}")
    rows = list(company_values)
    complete = [
        (top_line, op, net)
        for top_line, op, net, quality in rows
        if quality == "complete"
        and top_line is not None
        and op is not None
        and net is not None
    ]
    target = TARGETS[market_id]
    actual = len(complete)
    if not complete:
        average_op = average_net = None
        operating_margin = net_margin = None
    else:
        total_top_line = sum((top for top, _, _ in complete), Decimal("0"))
        total_op = sum((op for _, op, _ in complete), Decimal("0"))
        total_net = sum((net for _, _, net in complete), Decimal("0"))
        average_op = total_op / Decimal(actual)
        average_net = total_net / Decimal(actual)
        operating_margin = profit_margin(total_op, total_top_line)
        net_margin = profit_margin(total_net, total_top_line)

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
        operating_margin_pct=operating_margin,
        net_margin_pct=net_margin,
        actual_company_count=actual,
        target_company_count=target,
        completion_status=status,
    )


def calculate_market_series(rows: Iterable[MarketQuarter]) -> list[MarketQuarter]:
    return calculate_market_growth(rows)
