"""Pure calculation for the Korea/US stock-attractiveness flow."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite


LOOKBACK_WEEKS = 260
RETURN_WEEKS = 13
SMOOTH_WEEKS = 4
METHOD_VERSION = "stock-attractiveness-v2"


@dataclass(frozen=True)
class QuarterlyInput:
    period_end: date
    net_income: float
    market_cap: float | None


def percentile_score(value: float, history: list[float]) -> float:
    values = sorted(item for item in history if isfinite(item))
    if not values:
        raise ValueError("percentile history is empty")
    lower = bisect_right(values, value) - values.count(value)
    equal = values.count(value)
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
    """Build causal weekly scores; quarterly figures become available after a reporting lag."""

    ttm = trailing_four_quarter_income(quarters)
    lag = timedelta(days=75 if country == "KR" else 60)
    available = [(period + lag, income, cap) for period, income, cap in ttm]
    if country == "US" and (not available or us_anchor_earnings_yield is None):
        raise ValueError("US calculation requires an earnings-yield anchor")
    anchor_available = [item for item in available if weeks and item[0] <= weeks[-1]]
    anchor_income = anchor_available[-1][1] if anchor_available else 0.0
    anchor_price = equity_prices.get(weeks[-1]) if weeks else None
    raw: list[dict] = []
    for index, week in enumerate(weeks):
        eligible = [item for item in available if item[0] <= week]
        if not eligible or week not in equity_prices or week not in sovereign_yields:
            continue
        _, income, market_cap = eligible[-1]
        if country == "KR":
            if not market_cap or market_cap <= 0:
                continue
            earnings_yield = income / market_cap * 100.0
        else:
            if not anchor_price or not anchor_income or equity_prices[week] <= 0:
                continue
            earnings_yield = us_anchor_earnings_yield * (income / anchor_income) / (equity_prices[week] / anchor_price)
        prior_week = weeks[index - RETURN_WEEKS] if index >= RETURN_WEEKS else None
        prior_year = weeks[index - 52] if index >= 52 else None
        if prior_week not in equity_prices or prior_year is None:
            continue
        prior_available = [item for item in available if item[0] <= prior_year]
        if not prior_available:
            continue
        prior_income = prior_available[-1][1]
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
    for index, item in enumerate(raw):
        history = raw[max(0, index - LOOKBACK_WEEKS + 1):index + 1]
        if len(history) < 52:
            continue
        components = {
            "valuation": percentile_score(item["yield_gap_pct"], [row["yield_gap_pct"] for row in history]),
            "earnings_environment": percentile_score(item["earnings_momentum_pct"], [row["earnings_momentum_pct"] for row in history]),
            "market_confirmation": percentile_score(item["equity_return_13w_pct"], [row["equity_return_13w_pct"] for row in history]),
        }
        item = dict(item)
        item["raw_score"] = components["valuation"] * .45 + components["earnings_environment"] * .35 + components["market_confirmation"] * .20
        item["components"] = components
        scored.append(item)
    for index, item in enumerate(scored):
        window = scored[max(0, index - SMOOTH_WEEKS + 1):index + 1]
        item["score"] = sum(row["raw_score"] for row in window) / len(window)
    return scored
