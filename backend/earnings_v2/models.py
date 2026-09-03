from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Security:
    stock_code: str
    name: str
    market_cap: Decimal
    reference_date: date


@dataclass(frozen=True)
class PeriodicFiling:
    corp_code: str
    receipt_no: str
    received_on: date
    report_name: str


@dataclass(frozen=True)
class DelistingFiling:
    corp_code: str
    receipt_no: str
    received_on: date
    report_name: str
    event_type: str

    def db_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompanyIdentity:
    company_id: str
    company_name: str
    stock_code: str
    corp_code: str
    market_id: str
    rank: int
    market_cap: Decimal
    reference_date: date
    industry_code: str | None = None
    entity_kind: str | None = None


@dataclass(frozen=True)
class QuarterFxRate:
    fiscal_year: int
    fiscal_quarter: int
    base_currency: str
    quote_currency: str
    target_date: date
    observed_on: date
    rate: Decimal
    source: str = "ecos"

    def db_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FinancialFact:
    company_id: str
    fiscal_year: int
    fiscal_quarter: int
    period_end: date
    top_line: Decimal | None
    operating_income: Decimal | None
    net_income: Decimal | None
    currency: str
    consolidation_scope: str
    source_filing_id: str
    filing_date: date
    source: str = "open_dart"
    source_currency: str = "KRW"
    source_top_line_cumulative: Decimal | None = None
    source_operating_income_cumulative: Decimal | None = None
    source_net_income_cumulative: Decimal | None = None
    is_pending: bool = False
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
    def profit_complete(self) -> bool:
        return self.operating_income is not None and self.net_income is not None

    @property
    def fully_complete(self) -> bool:
        return self.top_line is not None and self.profit_complete

    def with_changes(self, **changes: Any) -> "FinancialFact":
        return replace(self, **changes)

    def db_row(self, *, calculation_version: int) -> dict[str, Any]:
        row = asdict(self)
        row.update({
            "period_start": None,
            "market_year": self.fiscal_year,
            "market_quarter": self.fiscal_quarter,
            "source": self.source,
            "revision_reference_date": None,
            "calculation_version": calculation_version,
            "is_pending": self.is_pending or not self.fully_complete,
        })
        return row


@dataclass(frozen=True)
class MarketFact:
    market_id: str
    market_year: int
    market_quarter: int
    reference_date: date
    top_line_total: Decimal | None
    operating_income_total: Decimal | None
    net_income_total: Decimal | None
    operating_margin_pct: Decimal | None
    net_margin_pct: Decimal | None
    reported_company_count: int
    pending_company_count: int
    target_company_count: int
    completion_status: str
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
        return self.market_year, self.market_quarter

    def with_changes(self, **changes: Any) -> "MarketFact":
        return replace(self, **changes)

    def db_row(self, *, calculation_version: int) -> dict[str, Any]:
        row = asdict(self)
        row["lifecycle_status"] = row.pop("completion_status")
        row["calculation_version"] = calculation_version
        return row

