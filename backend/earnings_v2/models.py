from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class QuarterValue:
    """The three durable financial facts for one single fiscal quarter."""

    company_id: str
    fiscal_year: int
    fiscal_quarter: int
    market_year: int
    market_quarter: int
    period_end: date
    top_line: Decimal | None
    operating_income: Decimal | None
    net_income: Decimal | None
    currency: str
    consolidation_scope: str
    period_start: date | None = None
    source: str = "open_dart"
    source_filing_id: str = ""
    filing_date: date | None = None
    revision_reference_date: date | None = None
    quality_status: str = "draft"
    calculation_version: int = 1
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

    def with_metrics(self, **changes: object) -> "QuarterValue":
        return replace(self, **changes)


@dataclass(frozen=True)
class GrowthResult:
    value: Decimal | None
    state: str


@dataclass(frozen=True)
class UniverseCandidate:
    company_id: str
    company_name: str
    currency: str
    market_cap: Decimal
    reference_date: date
    eligible: bool = True
    is_new_listing: bool = False


@dataclass(frozen=True)
class UniverseMember:
    market_id: str
    market_year: int
    market_quarter: int
    reference_date: date
    company_id: str
    market_cap_rank: int
    market_cap: Decimal
    currency: str
    selection_method: str


@dataclass(frozen=True)
class MarketQuarter:
    market_id: str
    market_year: int
    market_quarter: int
    average_operating_income: Decimal | None
    average_net_income: Decimal | None
    actual_company_count: int
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

    def with_metrics(self, **changes: object) -> "MarketQuarter":
        return replace(self, **changes)
