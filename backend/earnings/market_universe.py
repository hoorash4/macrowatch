"""Point-in-time quarterly market-cap universe selection.

Daily market-cap snapshots are durable source data.  Market earnings analysis
uses the last complete snapshot observed inside each calendar quarter; it must
never project today's constituents backwards into periods with no snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from earnings.market_breadth import MarketQuarter


@dataclass(frozen=True)
class QuarterlyUniverse:
    index_id: str
    period: MarketQuarter
    observed_on: date
    company_ids: frozenset[str]


def quarterly_universes_from_rows(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[MarketQuarter, QuarterlyUniverse]]:
    """Build complete quarterly universes from the database RPC rows.

    The RPC already returns only the latest snapshot date in each quarter.
    This adapter additionally fails closed on duplicate ranks, companies or
    mixed snapshot dates so a partial/corrupt universe cannot reach metrics.
    """

    grouped: dict[tuple[str, MarketQuarter], list[Mapping[str, Any]]] = {}
    for row in rows:
        index_id = str(row["index_id"])
        observed_on = date.fromisoformat(str(row["observed_on"])[:10])
        period = MarketQuarter(observed_on.year, (observed_on.month - 1) // 3 + 1)
        grouped.setdefault((index_id, period), []).append(row)

    result: dict[str, dict[MarketQuarter, QuarterlyUniverse]] = {}
    for (index_id, period), members in grouped.items():
        dates = {date.fromisoformat(str(row["observed_on"])[:10]) for row in members}
        ranks = [int(row["rank"]) for row in members]
        companies = [str(row["company_id"]) for row in members]
        if len(dates) != 1:
            raise ValueError(f"mixed snapshot dates for {index_id} {period}")
        if len(set(ranks)) != len(ranks) or len(set(companies)) != len(companies):
            raise ValueError(f"duplicate snapshot member for {index_id} {period}")
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError(f"incomplete snapshot ranks for {index_id} {period}")
        result.setdefault(index_id, {})[period] = QuarterlyUniverse(
            index_id=index_id,
            period=period,
            observed_on=next(iter(dates)),
            company_ids=frozenset(companies),
        )
    return result
