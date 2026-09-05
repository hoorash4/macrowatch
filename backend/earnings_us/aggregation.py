from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable

from earnings_v2.aggregation import calculate_market_series
from earnings_v2.models import MarketFact

from .models import USCompany, USFinancialFact


ZERO = Decimal(0)


def _metric(member: USCompany, facts: dict[str, USFinancialFact], previous: dict[str, USFinancialFact], field: str) -> Decimal | None:
    current = facts.get(member.company_id)
    if current is not None:
        value = getattr(current, field)
        if value is not None:
            return value
    prior = previous.get(member.company_id)
    return getattr(prior, field) if prior is not None else None


def aggregate_us_market(
    market_id: str, year: int, quarter: int, members: Iterable[USCompany],
    facts: dict[str, USFinancialFact], previous: dict[str, USFinancialFact],
) -> MarketFact:
    members = sorted(members, key=lambda row: row.rank)
    if len(members) != 100:
        raise ValueError(f"{market_id} universe is {len(members)}/100")
    reported = sum(1 for item in members if item.company_id in facts and facts[item.company_id].fully_complete and not facts[item.company_id].is_pending)
    totals: list[Decimal | None] = []
    for field in ("top_line", "operating_income", "net_income"):
        values = [_metric(item, facts, previous, field) for item in members]
        totals.append(sum((value for value in values if value is not None), ZERO) if any(value is not None for value in values) else None)
    top, operating, net = totals
    status = "complete" if reported == 100 else ("provisional" if any(value is not None for value in totals) else "collecting")
    return MarketFact(
        market_id, year, quarter, members[0].reference_date if members else date(year, quarter * 3, 1),
        top, operating, net,
        (operating / top * 100) if top not in (None, ZERO) and operating is not None else None,
        (net / top * 100) if top not in (None, ZERO) and net is not None else None,
        reported, 100 - reported, 100, status,
    )


def with_market_metrics(history: Iterable[MarketFact]) -> list[MarketFact]:
    return calculate_market_series(history)
