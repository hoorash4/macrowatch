from __future__ import annotations

from decimal import Decimal


def profit_margin(profit: Decimal | None, top_line: Decimal | None) -> Decimal | None:
    """Return a same-quarter profit margin percentage without inventing a denominator."""
    if profit is None or top_line is None or top_line == 0:
        return None
    return ((profit / top_line) * Decimal("100")).quantize(Decimal("0.00000001"))


def single_quarter_amount(
    fiscal_quarter: int,
    *,
    current_three_month: Decimal | None = None,
    cumulative: Decimal | None = None,
    previous_cumulative: Decimal | None = None,
) -> Decimal | None:
    """Convert a cumulative filing value into a single-quarter fact.

    A disclosed three-month amount always wins. Otherwise Q1 is already a
    single quarter and Q2-Q4 require a compatible prior cumulative amount.
    """
    if fiscal_quarter not in (1, 2, 3, 4):
        raise ValueError("fiscal_quarter must be between 1 and 4")
    if current_three_month is not None:
        return current_three_month
    if cumulative is None:
        return None
    if fiscal_quarter == 1:
        return cumulative
    if previous_cumulative is None:
        return None
    return cumulative - previous_cumulative
