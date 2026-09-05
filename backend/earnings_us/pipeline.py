from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .aggregation import aggregate_us_market, with_market_metrics
from .models import MarketSecurity, USCompany, USFinancialFact, market_period
from .constituents import USIndexConstituentClient
from .providers import ProviderError, SecEdgarClient
from .repository import USEarningsRepository
from .transform import extract_new_sec_facts


MARKETS = ("us_sp100", "us_nasdaq100")


def latest_completed_period(today: date) -> tuple[int, int]:
    index = today.year * 4 + (today.month - 1) // 3 - 1
    return index // 4, index % 4 + 1


def period_end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, (31 if quarter in {1, 4} else 30))


def in_snapshot_window(today: date) -> bool:
    """KST morning after a U.S. calendar-quarter close, never a late bootstrap."""
    return today.month in {1, 4, 7, 10} and today.day <= 3


def fact_from_row(row: dict[str, Any]) -> USFinancialFact:
    number = lambda name: Decimal(str(row[name])) if row.get(name) is not None else None
    return USFinancialFact(
        company_id=str(row["company_id"]), fiscal_year=int(row["fiscal_year"]), fiscal_quarter=int(row["fiscal_quarter"]),
        period_start=date.fromisoformat(str(row["period_start"])) if row.get("period_start") else None,
        period_end=date.fromisoformat(str(row["period_end"])), top_line=number("top_line"),
        operating_income=number("operating_income"), net_income=number("net_income"),
        source_filing_id=str(row["source_filing_id"]), filing_date=date.fromisoformat(str(row["filing_date"])),
        is_pending=bool(row.get("is_pending")),
    )


class USEarningsAutomaticPipeline:
    def __init__(self, repository: USEarningsRepository, sec: SecEdgarClient, constituents: USIndexConstituentClient) -> None:
        self.repository, self.sec, self.constituents = repository, sec, constituents
        self._historical_company_ids: set[str] = set()

    @classmethod
    def from_env(cls) -> "USEarningsAutomaticPipeline":
        sec = SecEdgarClient.from_env()
        return cls(USEarningsRepository.from_env(), sec, USIndexConstituentClient(sec))

    def snapshot(self, *, today: date | None = None, write: bool = True) -> dict[str, Any]:
        current_day = today or date.today()
        year, quarter = latest_completed_period(current_day)
        if not in_snapshot_window(current_day):
            return {
                "period": f"{year}Q{quarter}", "status": "ready", "write": write,
                "markets": "outside_snapshot_window",
            }
        existing = {market: self.repository.us_universe(market, year, quarter) for market in MARKETS}
        pending_markets = [market for market, rows in existing.items() if len(rows) != 100]
        if not pending_markets:
            return {"period": f"{year}Q{quarter}", "status": "ready", "markets": "already_frozen", "write": write}
        directory = self.sec.ticker_directory()
        securities: list[MarketSecurity] = []
        for market in pending_markets:
            reference_date = period_end(year, quarter)
            if market == "us_sp100":
                securities.extend(self.constituents.sp100_current(reference_date, directory))
            else:
                securities.extend(self.constituents.nasdaq100(reference_date, directory))
        if write:
            self.persist_universe_securities(securities)
            for market in pending_markets:
                rows = [item for item in securities if item.market_id == market]
                self.repository.save_us_universe(market, year, quarter, rows)
        summary = {"period": f"{year}Q{quarter}", "status": "ready", "write": write,
                   "markets": {market: len([item for item in securities if item.market_id == market]) for market in pending_markets},
                   "missing_cik": sum(item.cik is None for item in securities),
                   "requests": {"index_sources": self.constituents.request_count, "sec": self.sec.request_count}}
        if write:
            self.repository.save_us_state("snapshot", "ready", {"period": summary["period"]})
        return summary

    def persist_universe_securities(self, securities: list[MarketSecurity], *, historical: bool = False) -> None:
        """Persist shared index members once per database conflict key."""
        companies = {item.company_id: item for item in securities}
        if historical:
            companies = {
                company_id: item for company_id, item in companies.items()
                if company_id not in self._historical_company_ids
            }
        if companies:
            self.repository.upsert_companies({
                "company_id": item.company_id, "country": "US", "company_name": item.name,
                "reporting_currency": "USD", "entity_kind": "general", "listed_from": None, "delisted_on": None,
            } for item in companies.values())
            if historical:
                self._historical_company_ids.update(companies)

        ticker_rows = {}
        cik_rows = {}
        for item in securities:
            ticker_key = (item.company_id, item.ticker, item.reference_date)
            ticker_rows.setdefault(ticker_key, {
                "company_id": item.company_id, "identifier_type": "ticker", "identifier_value": item.ticker,
                "exchange": item.market_id, "valid_from": item.reference_date, "valid_to": None,
                "is_primary": not historical,
            })
            if item.cik:
                cik_key = (item.company_id, item.cik, item.reference_date)
                cik_rows.setdefault(cik_key, {
                    "company_id": item.company_id, "identifier_type": "cik", "identifier_value": item.cik,
                    "exchange": None, "valid_from": item.reference_date, "valid_to": None,
                    "is_primary": not historical,
                })
        self.repository.upsert_identifiers(ticker_rows.values())
        self.repository.upsert_identifiers(cik_rows.values())

    def daily_edgar(self, *, today: date | None = None, write: bool = True) -> dict[str, Any]:
        current_day = today or date.today()
        state = self.repository.us_state("daily_edgar") or {}
        cursor = state.get("cursor") if isinstance(state.get("cursor"), dict) else {}
        try:
            since = date.fromisoformat(str(cursor.get("last_checked_date")))
        except (TypeError, ValueError):
            since = current_day - timedelta(days=1)
        companies = self.repository.us_active_companies(current_day.year - 2)
        changed: dict[tuple[str, int, int], USFinancialFact] = {}
        issues: list[dict[str, str]] = []
        checked = 0
        for company in companies:
            cik = str(company.get("cik") or "")
            if not cik:
                issues.append({"company": str(company.get("company_name") or company["company_id"]), "reason": "SEC CIK missing"})
                continue
            checked += 1
            try:
                accessions = self.sec.new_financial_accessions(cik, since)
                if not accessions:
                    continue
                for fact in extract_new_sec_facts(str(company["company_id"]), self.sec.company_facts(cik), accessions):
                    changed[(fact.company_id, fact.fiscal_year, fact.fiscal_quarter)] = fact
            except ProviderError as exc:
                issues.append({"company": str(company.get("company_name") or company["company_id"]), "reason": str(exc)})
        if write and changed:
            self.repository.upsert_company_quarters(fact.db_row() for fact in changed.values())
            affected = {market_period(fact.period_end) for fact in changed.values()}
            for year, quarter in affected:
                self.recalculate_market_period(year, quarter)
        status = "incomplete" if issues else "ready"
        result = {"date": current_day.isoformat(), "status": status, "write": write, "companies_checked": checked,
                  "updated_company_quarters": len(changed), "issues": issues,
                  "requests": {"sec": self.sec.request_count}}
        if write:
            self.repository.save_us_state("daily_edgar", status, {"last_checked_date": current_day.isoformat()}, None)
        return result

    def recalculate_market_period(self, year: int, quarter: int) -> None:
        for market in MARKETS:
            members = self.repository.us_universe(market, year, quarter)
            if len(members) != 100:
                continue
            facts = {row.company_id: row for row in map(fact_from_row, self.repository.us_market_facts(market, year, quarter))}
            previous_year, previous_quarter = (year - 1, 4) if quarter == 1 else (year, quarter - 1)
            prior_rows = self.repository.us_market_facts(market, previous_year, previous_quarter)
            previous = {row.company_id: row for row in map(fact_from_row, prior_rows)}
            current = aggregate_us_market(market, year, quarter, members, facts, previous)
            history = [*self._market_history(market), current]
            calculated = with_market_metrics({row.key: row for row in history}.values())
            final = next(row for row in calculated if row.key == current.key)
            self.repository.upsert_market_quarters([final.db_row(calculation_version=6)])

    def _market_history(self, market: str) -> list[Any]:
        rows = self.repository.market_history(market)
        # Existing reader output has all fields required by MarketFact.db_row; only current records matter here.
        from earnings_v2.models import MarketFact
        result = []
        for row in rows:
            try:
                result.append(MarketFact(
                    market_id=str(row["market_id"]), market_year=int(row["market_year"]), market_quarter=int(row["market_quarter"]),
                    reference_date=date.fromisoformat(str(row["reference_date"])), top_line_total=Decimal(str(row["top_line_total"])) if row.get("top_line_total") is not None else None,
                    operating_income_total=Decimal(str(row["operating_income_total"])) if row.get("operating_income_total") is not None else None,
                    net_income_total=Decimal(str(row["net_income_total"])) if row.get("net_income_total") is not None else None,
                    operating_margin_pct=None, net_margin_pct=None, reported_company_count=int(row.get("reported_company_count") or 0),
                    pending_company_count=int(row.get("pending_company_count") or 0), target_company_count=int(row.get("target_company_count") or 100),
                    completion_status=str(row.get("lifecycle_status") or "collecting"),
                ))
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
        return result
