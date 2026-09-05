from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any


def market_period(period_end: date) -> tuple[int, int]:
    return period_end.year, (period_end.month - 1) // 3 + 1


@dataclass(frozen=True)
class MarketSecurity:
    ticker: str
    name: str
    cik: str | None
    market_cap: Decimal
    rank: int
    reference_date: date
    market_id: str

    @property
    def company_id(self) -> str:
        return f"us:cik:{self.cik}" if self.cik else f"us:ticker:{self.ticker}"


@dataclass(frozen=True)
class USCompany:
    company_id: str
    company_name: str
    ticker: str
    cik: str | None
    market_id: str
    rank: int
    market_cap: Decimal
    reference_date: date


@dataclass(frozen=True)
class USFinancialFact:
    company_id: str
    fiscal_year: int
    fiscal_quarter: int
    period_start: date | None
    period_end: date
    top_line: Decimal | None
    operating_income: Decimal | None
    net_income: Decimal | None
    source_filing_id: str
    filing_date: date
    is_pending: bool

    currency: str = "USD"
    consolidation_scope: str = "CFS"
    source: str = "sec_edgar"
    operating_margin_pct: Decimal | None = None
    net_margin_pct: Decimal | None = None
    operating_income_yoy_pct: Decimal | None = None
    operating_income_yoy_state: str = "missing_prior"
    net_income_yoy_pct: Decimal | None = None
    net_income_yoy_state: str = "missing_prior"
    operating_income_qoq_sa_pct: Decimal | None = None
    operating_income_qoq_state: str = "insufficient_history"
    net_income_qoq_sa_pct: Decimal | None = None
    net_income_qoq_state: str = "insufficient_history"

    @property
    def key(self) -> tuple[int, int]:
        return self.fiscal_year, self.fiscal_quarter

    @property
    def fully_complete(self) -> bool:
        return self.top_line is not None and self.operating_income is not None and self.net_income is not None

    def with_changes(self, **changes: Any) -> "USFinancialFact":
        return replace(self, **changes)

    def db_row(self, *, calculation_version: int = 6) -> dict[str, Any]:
        row = asdict(self)
        market_year, market_quarter = market_period(self.period_end)
        row.update({
            "market_year": market_year,
            "market_quarter": market_quarter,
            "source_currency": "USD",
            "source_top_line_cumulative": None,
            "source_operating_income_cumulative": None,
            "source_net_income_cumulative": None,
            "revision_reference_date": None,
            "calculation_version": calculation_version,
            "is_pending": self.is_pending or not self.fully_complete,
        })
        return row
