"""Pure calculation for the Korea/US stock-attractiveness flow."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite


LOOKBACK_WEEKS = 260
RETURN_WEEKS = 13
SMOOTH_WEEKS = 4
METHOD_VERSION = "stock-attractiveness-v3"


@dataclass(frozen=True)
class QuarterlyInput:
    period_end: date
    net_income: float
    market_cap: float | None


def percentile_score(value: float, history: list[float]) -> float:
    values = sorted(item for item in history if isfinite(item))
    if not values:
        raise ValueError("percentile history is empty")
    equal = values.count(value)
    lower = bisect_right(values, value) - equal
    percentile = (lower + equal / 2) / len(values)
    return percentile * 100.0


def trailing_four_quarter_income(rows: list[QuarterlyInput]) -> list[tuple[date, float, float | None]]:
    ordered = sorted(rows, key=lambda row: row.period_end)
    result: list[tuple[date, float, float | None]] = []
    for index in range(3, len(ordered)):
        window = ordered[index - 3:index + 1]
        result.append((window[-1].period_end, sum(row.net_income for row in window), window[-1].market_cap))
    return result


def symmetric_change(current: float, previous: float) -> float:
    denominator = abs(current) + abs(previous)
    return 0.0 if denominator == 0 else 200.0 * (current - previous) / denominator


def build_weekly_rows(
    country: str,
    weeks: list[date],
    equity_prices: dict[date, float],
    sovereign_yields: dict[date, float],
    quarters: list[QuarterlyInput],
    *,
    us_anchor_earnings_yield: float | None = None,
) -> list[dict]:
    """Historical valuation proxy with reporting lags and a current US valuation anchor."""

    ttm = trailing_four_quarter_income(quarters)
    lag = timedelta(days=75 if country == "KR" else 60)
    available = [(period + lag, income, cap) for period, income, cap in ttm]
    available_dates = [item[0] for item in available]
    if country == "US" and (not available or us_anchor_earnings_yield is None):
        raise ValueError("US calculation requires an earnings-yield anchor")
    anchor_available = [item for item in available if weeks and item[0] <= weeks[-1]]
    anchor_income = anchor_available[-1][1] if anchor_available else 0.0
    anchor_price = equity_prices.get(weeks[-1]) if weeks else None
    price_dates = sorted(equity_prices)
    raw: list[dict] = []
    for index, week in enumerate(weeks):
        current_index = bisect_right(available_dates, week) - 1
        if current_index < 0 or week not in equity_prices or week not in sovereign_yields:
            continue
        available_date, income, market_cap = available[current_index]
        if equity_prices[week] <= 0:
            continue
        if country == "KR":
            if not market_cap or market_cap <= 0:
                continue
            # The cap belongs to the quarter end, not the reporting-lag date.
            cap_date = available_date - lag
            price_index = bisect_right(price_dates, cap_date) - 1
            if price_index < 0 or equity_prices[price_dates[price_index]] <= 0:
                continue
            current_cap = market_cap * equity_prices[week] / equity_prices[price_dates[price_index]]
            earnings_yield = income / current_cap * 100.0
        else:
            if not anchor_price or not anchor_income or equity_prices[week] <= 0:
                continue
            earnings_yield = us_anchor_earnings_yield * (income / anchor_income) / (equity_prices[week] / anchor_price)
        prior_week = weeks[index - RETURN_WEEKS] if index >= RETURN_WEEKS else None
        prior_year = weeks[index - 52] if index >= 52 else None
        if prior_week not in equity_prices or prior_year is None:
            continue
        prior_index = bisect_right(available_dates, prior_year) - 1
        if prior_index < 0:
            continue
        prior_income = available[prior_index][1]
        equity_return = (equity_prices[week] / equity_prices[prior_week] - 1.0) * 100.0
        raw.append({
            "observation_date": week,
            "earnings_yield_pct": earnings_yield,
            "sovereign_yield_pct": sovereign_yields[week],
            "yield_gap_pct": earnings_yield - sovereign_yields[week],
            "earnings_momentum_pct": symmetric_change(income, prior_income),
            "equity_return_13w_pct": equity_return,
        })
    scored: list[dict] = []
    gaps = {row["observation_date"]: row["yield_gap_pct"] for row in raw}
    for item in raw:
        prior_gap = gaps.get(item["observation_date"] - timedelta(weeks=RETURN_WEEKS))
        item["gap_change_13w_pp"] = None if prior_gap is None else item["yield_gap_pct"] - prior_gap
    for index, item in enumerate(raw):
        history = raw[max(0, index - LOOKBACK_WEEKS + 1):index + 1]
        change_history = [row["gap_change_13w_pp"] for row in history if row["gap_change_13w_pp"] is not None]
        if len(change_history) < 52 or item["gap_change_13w_pp"] is None:
            continue
        components = {
            "yield_gap_level": percentile_score(item["yield_gap_pct"], [row["yield_gap_pct"] for row in history]),
            "yield_gap_change": percentile_score(item["gap_change_13w_pp"], change_history),
        }
        item = dict(item)
        item["raw_score"] = components["yield_gap_level"] * .70 + components["yield_gap_change"] * .30
        item["components"] = components
        scored.append(item)
    for index, item in enumerate(scored):
        window = scored[max(0, index - SMOOTH_WEEKS + 1):index + 1]
        item["score"] = sum(row["raw_score"] for row in window) / len(window)
    return scored
