"""Quarterly market-cap universe selection with historical fallback."""

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
    basis: str = "point_in_time_market_cap_snapshot"


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


def backfill_before_earliest_snapshot(
    universes_by_period: Mapping[MarketQuarter, QuarterlyUniverse],
    periods: Iterable[MarketQuarter],
) -> dict[MarketQuarter, QuarterlyUniverse]:
    """Fill only pre-history with an explicitly labelled fallback universe.

    Actual point-in-time snapshots remain authoritative wherever available.
    Periods strictly earlier than the oldest snapshot reuse that oldest
    constituent set only when an actual historical ranking is unavailable.
    The fallback label is persisted with derived rows, so it can never be
    mistaken for a point-in-time market-cap ranking.
    """

    result = dict(universes_by_period)
    if not result:
        return result

    earliest_period = min(result, key=lambda period: (period.year, period.quarter))
    earliest = result[earliest_period]
    earliest_key = (earliest_period.year, earliest_period.quarter)
    for period in periods:
        if (period.year, period.quarter) >= earliest_key or period in result:
            continue
        result[period] = QuarterlyUniverse(
            index_id=earliest.index_id,
            period=period,
            observed_on=earliest.observed_on,
            company_ids=earliest.company_ids,
            basis="oldest_available_universe_average_fallback",
        )
    return result
