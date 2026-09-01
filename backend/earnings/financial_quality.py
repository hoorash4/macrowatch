"""Deterministic quality gates for canonical quarterly financial facts.

The collector must distinguish a syntactically valid OpenDART response from a
plausible standalone quarter. These checks quarantine suspicious rows for a
source retry; they never invent, cap, or silently adjust reported amounts.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


CORE_METRICS = ("operating_income", "net_income")


def _amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def validate_canonical_quarter(
    quarter: dict[str, Any] | None,
    history: Iterable[dict[str, Any]] = (),
) -> list[str]:
    """Return stable reason codes when a quarter requires source review."""
    if quarter is None:
        return []

    # Kept in the public signature for callers that still pass historical rows;
    # profit-only validation does not manufacture cross-period thresholds.
    del history
    values = {metric: _amount(quarter.get(metric)) for metric in CORE_METRICS}
    issues: list[str] = []
    if all(value == 0 for value in values.values() if value is not None) and all(
        value is not None for value in values.values()
    ):
        issues.append("all_zero_income_statement")

    return sorted(set(issues))
