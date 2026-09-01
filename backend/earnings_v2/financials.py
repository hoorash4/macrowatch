from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class StatementAmount:
    account_name: str
    amount: Decimal
    is_total: bool = False
    is_revenue: bool = False
    is_cost_or_loss: bool = False


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


def financial_top_line(rows_above_operating_income: Iterable[StatementAmount]) -> tuple[Decimal | None, str]:
    """Choose one verified total or sum non-overlapping financial revenue leaves."""
    rows = list(rows_above_operating_income)
    totals = [row for row in rows if row.is_total and row.is_revenue and not row.is_cost_or_loss]
    if totals:
        # Source adapters must mark only verified comparable totals. If more
        # than one survives, the highest statement total is the least partial.
        return max(totals, key=lambda row: abs(row.amount)).amount, "reported_total"
    leaves = [row.amount for row in rows if row.is_revenue and not row.is_total and not row.is_cost_or_loss]
    return (sum(leaves, Decimal("0")), "financial_income_sum") if leaves else (None, "financial_income_sum")

