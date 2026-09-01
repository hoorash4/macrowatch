from __future__ import annotations

from dataclasses import dataclass

from .universe import MARKET_TARGETS


@dataclass(frozen=True)
class PilotPlan:
    start_year: int
    start_quarter: int
    end_year: int
    end_quarter: int
    markets: tuple[str, ...]

    @property
    def quarters(self) -> tuple[tuple[str, int, int], ...]:
        start = self.start_year * 4 + self.start_quarter - 1
        end = self.end_year * 4 + self.end_quarter - 1
        return tuple(
            (market, ordinal // 4, ordinal % 4 + 1)
            for market in self.markets
            for ordinal in range(start, end + 1)
        )


def build_one_year_pilot(year: int, markets: tuple[str, ...] | None = None) -> PilotPlan:
    """Create exactly one review boundary; multi-year plans are not accepted."""
    if year < 2000 or year > 2200:
        raise ValueError("pilot year is outside the supported range")
    selected = markets or tuple(MARKET_TARGETS)
    invalid = [market for market in selected if market not in MARKET_TARGETS]
    if invalid:
        raise ValueError(f"unsupported pilot markets: {', '.join(invalid)}")
    return PilotPlan(year, 1, year, 4, selected)


def build_recent_four_quarter_pilot(
    *, end_year: int, end_quarter: int, markets: tuple[str, ...] | None = None,
) -> PilotPlan:
    """Build the current trailing-four-quarter pilot, not a calendar year."""
    if end_year < 2000 or end_year > 2200 or end_quarter not in (1, 2, 3, 4):
        raise ValueError("invalid final confirmed quarter")
    end = end_year * 4 + end_quarter - 1
    start = end - 3
    selected = markets or tuple(MARKET_TARGETS)
    invalid = [market for market in selected if market not in MARKET_TARGETS]
    if invalid:
        raise ValueError(f"unsupported pilot markets: {', '.join(invalid)}")
    return PilotPlan(start // 4, start % 4 + 1, end_year, end_quarter, selected)
