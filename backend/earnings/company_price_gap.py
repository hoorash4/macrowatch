"""Pure company quarterly earnings/price disparity calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from earnings.market_breadth import MarketQuarter


HUNDRED = Decimal("100")
CALCULATION_VERSION = 1


@dataclass(frozen=True)
class QuarterlyOperatingIncome:
    company_id: str
    period: MarketQuarter
    operating_income: Decimal | None


@dataclass(frozen=True)
class QuarterlyAdjustedPrice:
    company_id: str
    period: MarketQuarter
    price_date: date
    adjusted_close: Decimal


@dataclass
class CompanyPriceGap:
    company_id: str
    period: MarketQuarter
    base_period: MarketQuarter
    price_date: date
    adjusted_close: Decimal
    ttm_operating_income: Decimal | None
    normalized_price: Decimal | None
    normalized_ttm_operating_income: Decimal | None
    gap_pct: Decimal | None
    gap_delta_pp: Decimal | None
    calculation_state: str

    def as_record(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "market_year": self.period.year,
            "market_quarter": self.period.quarter,
            "base_market_year": self.base_period.year,
            "base_market_quarter": self.base_period.quarter,
            "price_date": self.price_date.isoformat(),
            "adjusted_close": _serialize(self.adjusted_close),
            "ttm_operating_income": _serialize(self.ttm_operating_income),
            "normalized_price": _serialize(self.normalized_price),
            "normalized_ttm_operating_income": _serialize(
                self.normalized_ttm_operating_income
            ),
            "gap_pct": _serialize(self.gap_pct),
            "gap_delta_pp": _serialize(self.gap_delta_pp),
            "calculation_state": self.calculation_state,
            "calculation_version": CALCULATION_VERSION,
        }


def _serialize(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _period(raw_date: str | date) -> MarketQuarter:
    parsed = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date)[:10])
    return MarketQuarter(parsed.year, (parsed.month - 1) // 3 + 1)


def operating_income_from_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[QuarterlyOperatingIncome]:
    return [QuarterlyOperatingIncome(
        company_id=str(row["company_id"]),
        period=_period(row["period_end"]),
        operating_income=_decimal(row.get("operating_income")),
    ) for row in rows]


def prices_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[QuarterlyAdjustedPrice]:
    return [QuarterlyAdjustedPrice(
        company_id=str(row["company_id"]),
        period=MarketQuarter(int(row["market_year"]), int(row["market_quarter"])),
        price_date=date.fromisoformat(str(row["price_date"])[:10]),
        adjusted_close=Decimal(str(row["adjusted_close"])),
    ) for row in rows]


def calculate_company_price_gaps(
    *,
    company_id: str,
    operating_income: Iterable[QuarterlyOperatingIncome],
    prices: Iterable[QuarterlyAdjustedPrice],
) -> list[CompanyPriceGap]:
    """Rebase both quarterly lines at their first positive common TTM point."""

    op_by_period: dict[MarketQuarter, Decimal | None] = {}
    for row in operating_income:
        if row.company_id != company_id:
            continue
        if row.period in op_by_period:
            raise ValueError(f"duplicate company calendar quarter: {company_id} {row.period}")
        op_by_period[row.period] = row.operating_income

    price_by_period: dict[MarketQuarter, QuarterlyAdjustedPrice] = {}
    for row in prices:
        if row.company_id != company_id:
            continue
        if row.adjusted_close <= 0:
            raise ValueError("adjusted_close must be positive")
        price_by_period[row.period] = row

    ttm_by_period: dict[MarketQuarter, Decimal | None] = {}
    for period in sorted(price_by_period):
        quarters = [period.shift(-offset) for offset in range(4)]
        values = [op_by_period.get(quarter) for quarter in quarters]
        ttm_by_period[period] = (
            sum((value for value in values if value is not None), Decimal(0))
            if all(value is not None for value in values) else None
        )

    base_period = next((
        period for period in sorted(price_by_period)
        if ttm_by_period.get(period) is not None and ttm_by_period[period] > 0
    ), None)
    if base_period is None:
        return []
    base_price = price_by_period[base_period].adjusted_close
    base_ttm = ttm_by_period[base_period]
    assert base_ttm is not None and base_ttm > 0

    results: list[CompanyPriceGap] = []
    previous_normal_gap: Decimal | None = None
    for period in sorted(p for p in price_by_period if p >= base_period):
        price = price_by_period[period]
        ttm = ttm_by_period.get(period)
        normalized_price = price.adjusted_close / base_price * HUNDRED
        if ttm is None:
            state = "missing_ttm"
            normalized_ttm = gap = delta = None
            previous_normal_gap = None
        elif ttm <= 0:
            state = "nonpositive_ttm"
            normalized_ttm = gap = delta = None
            previous_normal_gap = None
        else:
            state = "normal"
            normalized_ttm = ttm / base_ttm * HUNDRED
            gap = (normalized_price / normalized_ttm - 1) * HUNDRED
            delta = gap - previous_normal_gap if previous_normal_gap is not None else None
            previous_normal_gap = gap
        results.append(CompanyPriceGap(
            company_id=company_id,
            period=period,
            base_period=base_period,
            price_date=price.price_date,
            adjusted_close=price.adjusted_close,
            ttm_operating_income=ttm,
            normalized_price=normalized_price,
            normalized_ttm_operating_income=normalized_ttm,
            gap_pct=gap,
            gap_delta_pp=delta,
            calculation_state=state,
        ))
    return results
