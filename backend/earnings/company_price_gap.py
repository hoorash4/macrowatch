"""Pure company quarterly earnings/price disparity calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from calendar import monthrange
from decimal import Decimal
from typing import Any, Iterable, Mapping

from earnings.market_breadth import MarketQuarter


HUNDRED = Decimal("100")
CALCULATION_VERSION = 2


@dataclass(frozen=True)
class QuarterlyOperatingIncome:
    company_id: str
    period: MarketQuarter
    operating_income: Decimal | None
    period_end: date | None = None


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
    gap_points: Decimal | None
    gap_delta_points: Decimal | None
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
            "gap_points": _serialize(self.gap_points),
            "gap_delta_points": _serialize(self.gap_delta_points),
            "calculation_state": self.calculation_state,
            "calculation_version": CALCULATION_VERSION,
        }


def _serialize(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _quarter_end(period: MarketQuarter) -> date:
    month = period.quarter * 3
    return date(period.year, month, monthrange(period.year, month)[1])


def _observation_end(row: QuarterlyOperatingIncome) -> date:
    return row.period_end or _quarter_end(row.period)


def operating_income_from_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[QuarterlyOperatingIncome]:
    return [QuarterlyOperatingIncome(
        company_id=str(row["company_id"]),
        period=MarketQuarter(int(row["fiscal_year"]), int(row["fiscal_quarter"])),
        operating_income=_decimal(row.get("operating_income")),
        period_end=date.fromisoformat(str(row["period_end"])[:10]),
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
    """Rebase both lines to 100 and store their finite index-point distance.

    Division between the two normalized lines is deliberately avoided. Once a
    company's TTM operating income crosses zero, a ratio becomes undefined or
    explodes even though the two plotted lines remain perfectly meaningful.
    """

    fiscal_rows: dict[MarketQuarter, QuarterlyOperatingIncome] = {}
    for row in operating_income:
        if row.company_id != company_id:
            continue
        if row.period in fiscal_rows:
            raise ValueError(f"duplicate company fiscal quarter: {company_id} {row.period}")
        fiscal_rows[row.period] = row

    price_by_period: dict[MarketQuarter, QuarterlyAdjustedPrice] = {}
    for row in prices:
        if row.company_id != company_id:
            continue
        if row.adjusted_close <= 0:
            raise ValueError("adjusted_close must be positive")
        price_by_period[row.period] = row

    ttm_by_period: dict[MarketQuarter, Decimal | None] = {}
    for period in sorted(price_by_period):
        calendar_end = _quarter_end(period)
        eligible = sorted(
            (
                row for row in fiscal_rows.values()
                if _observation_end(row) <= calendar_end
            ),
            key=lambda row: (_observation_end(row), row.period),
        )[-4:]
        ordinals = [row.period.year * 4 + row.period.quarter - 1 for row in eligible]
        consecutive = len(ordinals) == 4 and all(
            current - previous == 1
            for previous, current in zip(ordinals, ordinals[1:])
        )
        values = [row.operating_income for row in eligible]
        ttm_by_period[period] = (
            sum((value for value in values if value is not None), Decimal(0))
            if consecutive and all(value is not None for value in values) else None
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
        else:
            normalized_ttm = ttm / base_ttm * HUNDRED
            gap = normalized_price - normalized_ttm
            delta = gap - previous_normal_gap if previous_normal_gap is not None else None
            previous_normal_gap = gap
            state = "normal" if ttm > 0 else "nonpositive_ttm"
        results.append(CompanyPriceGap(
            company_id=company_id,
            period=period,
            base_period=base_period,
            price_date=price.price_date,
            adjusted_close=price.adjusted_close,
            ttm_operating_income=ttm,
            normalized_price=normalized_price,
            normalized_ttm_operating_income=normalized_ttm,
            gap_points=gap,
            gap_delta_points=delta,
            calculation_state=state,
        ))
    return results
