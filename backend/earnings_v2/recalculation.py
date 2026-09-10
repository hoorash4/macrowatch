from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from earnings_common.db_rows import _financial_from_db, _market_from_db
from earnings_common.models import CompanyIdentity, FinancialFact, MarketFact
from earnings_common.periods import previous_period
from earnings_common.seasonal_windows import (
    _advance_window,
    _seasonal_window_index,
    _window_samples,
)

from .aggregation import aggregate_market, calculate_market_point
from .contract import CALCULATION_VERSION, TARGETS
from .repository import EarningsV2Repository
from .transform import calculate_financial_point, decimal_value


def _identity_from_universe(row: dict[str, Any]) -> CompanyIdentity:
    return CompanyIdentity(
        company_id=str(row["company_id"]),
        company_name=str(row.get("company_name") or row["company_id"]),
        stock_code=str(row.get("stock_code") or ""),
        corp_code=str(row.get("corp_code") or ""),
        market_id=str(row["market_id"]),
        rank=int(row["market_cap_rank"]),
        market_cap=decimal_value(row.get("market_cap")) or Decimal(0),
        reference_date=date.fromisoformat(str(row["reference_date"])),
        industry_code=str(row.get("industry_code") or "").strip() or None,
        entity_kind=str(row.get("entity_kind") or "").strip() or None,
    )


class StoredQuarterRecalculation:
    """DB에 저장된 실적만 읽어 한 분기의 파생값을 다시 계산한다.

    이 서비스는 외부 데이터 공급자나 자동수집 파이프라인에 의존하지 않는다.
    과거 데이터는 계절조정과 비교 계산의 입력으로만 읽고, 지정된 분기의
    기업 파생값·시장 집계·계절창만 갱신한다.
    """

    def __init__(self, repository: EarningsV2Repository) -> None:
        self.repository = repository

    @classmethod
    def from_env(cls) -> "StoredQuarterRecalculation":
        return cls(EarningsV2Repository.from_env())

    @staticmethod
    def _stored_facts(
        records: Iterable[dict[str, Any]],
    ) -> dict[tuple[str, int, int], FinancialFact]:
        facts: dict[tuple[str, int, int], FinancialFact] = {}
        for record in records:
            if int(record.get("calculation_version") or 0) < CALCULATION_VERSION:
                continue
            fact = _financial_from_db(record)
            facts[(fact.company_id, *fact.key)] = fact
        return facts

    def _calculate_company_points(
        self,
        current: dict[str, FinancialFact],
        references: dict[tuple[str, int, int], FinancialFact],
        year: int,
        quarter: int,
    ) -> tuple[dict[str, FinancialFact], list[dict[str, Any]]]:
        company_ids = set(current)
        windows = _seasonal_window_index(
            self.repository.seasonal_windows("company", company_ids)
        )
        previous_key = previous_period(year, quarter)
        calculated = dict(current)
        window_rows: list[dict[str, Any]] = []
        for company_id, row in current.items():
            samples = {
                metric: _window_samples(windows, company_id, metric, quarter, year)
                for metric in ("operating_income", "net_income")
            }
            value, raw_samples = calculate_financial_point(
                row,
                previous=references.get((company_id, *previous_key)),
                prior_year=references.get((company_id, year - 1, quarter)),
                seasonal_samples=samples,
            )
            calculated[company_id] = value
            for metric, raw_sample in raw_samples.items():
                window_row = _advance_window(
                    windows,
                    entity_type="company",
                    entity_id=company_id,
                    metric=metric,
                    year=year,
                    quarter=quarter,
                    value=raw_sample,
                )
                if window_row is not None:
                    window_rows.append(window_row)
        return calculated, window_rows

    def _market_rows(
        self,
        universes: dict[str, list[CompanyIdentity]],
        previous_universes: dict[str, list[CompanyIdentity]],
        current_facts: dict[str, FinancialFact],
        references: dict[tuple[str, int, int], FinancialFact],
        year: int,
        quarter: int,
    ) -> tuple[list[MarketFact], list[dict[str, Any]]]:
        previous_key = previous_period(year, quarter)
        prior_markets = {
            (row.market_id, *row.key): row
            for record in self.repository.market_periods(
                TARGETS, [previous_key, (year - 1, quarter)]
            )
            if int(record.get("calculation_version") or 0) >= CALCULATION_VERSION
            for row in [_market_from_db(record)]
        }
        windows = _seasonal_window_index(
            self.repository.seasonal_windows("market", TARGETS)
        )
        output: list[MarketFact] = []
        window_rows: list[dict[str, Any]] = []
        for market_id, members in universes.items():
            previous_members = previous_universes.get(market_id, [])
            comparison_facts = {
                member.company_id: references.get((member.company_id, *previous_key))
                for member in previous_members
            }
            market = aggregate_market(
                market_id,
                year,
                quarter,
                members,
                {
                    member.company_id: current_facts.get(member.company_id)
                    for member in members
                },
                TARGETS[market_id],
                comparison_members=previous_members,
                comparison_facts=comparison_facts,
            )
            samples = {
                metric: _window_samples(windows, market_id, metric, quarter, year)
                for metric in ("operating_income", "net_income")
            }
            calculated, raw_samples = calculate_market_point(
                market,
                previous=prior_markets.get((market_id, *previous_key)),
                prior_year=prior_markets.get((market_id, year - 1, quarter)),
                seasonal_samples=samples,
            )
            output.append(calculated)
            for metric, raw_sample in raw_samples.items():
                window_row = _advance_window(
                    windows,
                    entity_type="market",
                    entity_id=market_id,
                    metric=metric,
                    year=year,
                    quarter=quarter,
                    value=raw_sample,
                )
                if window_row is not None:
                    window_rows.append(window_row)
        return output, window_rows

    def recalculate_quarter(
        self,
        year: int,
        quarter: int,
        *,
        write: bool = True,
    ) -> dict[str, Any]:
        if quarter not in (1, 2, 3, 4):
            raise ValueError("quarter must be 1..4")

        universes: dict[str, list[CompanyIdentity]] = {}
        for market_id, target in TARGETS.items():
            frozen = self.repository.universe(market_id, year, quarter)
            if len(frozen) != target:
                raise ValueError(f"{market_id} universe is {len(frozen)}/{target}")
            universes[market_id] = [_identity_from_universe(row) for row in frozen]

        identities = list(
            {row.company_id: row for rows in universes.values() for row in rows}.values()
        )
        previous_key = previous_period(year, quarter)
        previous_universes = {
            market_id: [
                _identity_from_universe(row)
                for row in self.repository.universe(market_id, *previous_key)
            ]
            for market_id in TARGETS
        }
        history_ids = {
            row.company_id
            for rows in [*universes.values(), *previous_universes.values()]
            for row in rows
        }
        stored_rows = self.repository.company_periods(
            history_ids,
            [(year, quarter), previous_key, (year - 1, quarter)],
        )
        stored = self._stored_facts(stored_rows)
        stored_current = {
            identity.company_id: stored[(identity.company_id, year, quarter)]
            for identity in identities
            if (identity.company_id, year, quarter) in stored
        }

        current_by_company, company_window_rows = self._calculate_company_points(
            stored_current, stored, year, quarter
        )
        current_markets, market_window_rows = self._market_rows(
            universes,
            previous_universes,
            current_by_company,
            stored,
            year,
            quarter,
        )

        if write:
            self.repository.upsert_company_quarters(
                row.db_row(calculation_version=CALCULATION_VERSION)
                for row in current_by_company.values()
            )
            if company_window_rows:
                self.repository.upsert_seasonal_windows(company_window_rows)
            self.repository.upsert_market_quarters(
                row.db_row(calculation_version=CALCULATION_VERSION)
                for row in current_markets
            )
            if market_window_rows:
                self.repository.upsert_seasonal_windows(market_window_rows)

        return {
            "period": f"{year}Q{quarter}",
            "write": write,
            "mode": "stored_recalculation",
            "companies": len(identities),
            "markets": {
                row.market_id: row.completion_status for row in current_markets
            },
            "status": "ready",
        }
