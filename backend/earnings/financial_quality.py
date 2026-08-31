"""Deterministic quality gates for canonical quarterly financial facts.

The collector must distinguish a syntactically valid OpenDART response from a
plausible standalone quarter. These checks quarantine suspicious rows for a
source retry; they never invent, cap, or silently adjust reported amounts.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any, Iterable


CORE_METRICS = ("revenue", "operating_income", "net_income")
ABSOLUTE_REVENUE_LIMIT = {
    "KRW": Decimal("1000000000000000"),
    "USD": Decimal("2000000000000"),
}


def _amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _ratio(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    if current is None or prior is None or prior == 0:
        return None
    return abs(current) / abs(prior)


def validate_canonical_quarter(
    quarter: dict[str, Any] | None,
    history: Iterable[dict[str, Any]] = (),
) -> list[str]:
    """Return stable reason codes when a quarter requires source review."""
    if quarter is None:
        return []

    history = list(history)
    values = {metric: _amount(quarter.get(metric)) for metric in CORE_METRICS}
    revenue = values["revenue"]
    issues: list[str] = []
    if revenue is not None and revenue < 0:
        issues.append("non_positive_revenue")
    if all(value == 0 for value in values.values() if value is not None) and all(
        value is not None for value in values.values()
    ):
        issues.append("all_zero_income_statement")

    currency = str(quarter.get("currency") or "")
    absolute_limit = ABSOLUTE_REVENUE_LIMIT.get(currency)
    if revenue is not None and absolute_limit is not None and revenue > absolute_limit:
        issues.append("absolute_revenue_limit")

    fiscal_year = int(quarter.get("market_year") or 0)
    fiscal_quarter = int(quarter.get("fiscal_quarter") or 0)
    comparable = [
        row for row in history
        if str(row.get("currency") or "") == currency
        and int(row.get("fiscal_quarter") or 0) == fiscal_quarter
        and int(row.get("fiscal_year") or 0) < fiscal_year
    ]
    positive_history = [
        value for row in history
        if str(row.get("currency") or "") == currency
        if (value := _amount(row.get("revenue"))) is not None and value > 0
    ]
    if revenue is not None and revenue > 0 and len(positive_history) >= 6:
        historical_median = Decimal(str(median(positive_history)))
        if historical_median > 0:
            history_ratio = revenue / historical_median
            if history_ratio > 50 or history_ratio < Decimal("0.02"):
                issues.append("revenue_history_outlier")

    if comparable:
        prior = max(comparable, key=lambda row: int(row.get("fiscal_year") or 0))
        revenue_ratio = _ratio(revenue, _amount(prior.get("revenue")))
        operating_ratio = _ratio(
            values["operating_income"], _amount(prior.get("operating_income"))
        )
        net_ratio = _ratio(values["net_income"], _amount(prior.get("net_income")))
        if (
            revenue_ratio is not None
            and (revenue_ratio > 10 or revenue_ratio < Decimal("0.1"))
            and operating_ratio is not None and operating_ratio > 50
            and net_ratio is not None and net_ratio > 50
        ):
            issues.append("multi_metric_yoy_break")

    return sorted(set(issues))
