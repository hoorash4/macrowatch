from __future__ import annotations

from dataclasses import dataclass

from .universe import MARKET_TARGETS


@dataclass(frozen=True)
class PilotPlan:
    year: int
    markets: tuple[str, ...]

    @property
    def quarters(self) -> tuple[tuple[str, int, int], ...]:
        return tuple((market, self.year, quarter) for market in self.markets for quarter in range(1, 5))


def build_one_year_pilot(year: int, markets: tuple[str, ...] | None = None) -> PilotPlan:
    """Create exactly one review boundary; multi-year plans are not accepted."""
    if year < 2000 or year > 2200:
        raise ValueError("pilot year is outside the supported range")
    selected = markets or tuple(MARKET_TARGETS)
    invalid = [market for market in selected if market not in MARKET_TARGETS]
    if invalid:
        raise ValueError(f"unsupported pilot markets: {', '.join(invalid)}")
    return PilotPlan(year=year, markets=selected)

