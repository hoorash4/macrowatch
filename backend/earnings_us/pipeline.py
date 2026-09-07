from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .aggregation import aggregate_us_market, with_market_metrics
from .models import MarketSecurity, USCompany, USFinancialFact, market_period
from .constituents import USIndexConstituentClient
from .providers import ProviderError, SecEdgarClient, SecFinancialFiling
from .repository import USEarningsRepository
from .transform import extract_inline_xbrl_fact, extract_new_sec_facts
from .six_k import SixKFiling, extract_six_k_fact, has_six_k_results_context
from earnings_v2.providers import EcosFxClient


MARKETS = ("us_sp100", "us_nasdaq100")


def latest_completed_period(today: date) -> tuple[int, int]:
    index = today.year * 4 + (today.month - 1) // 3 - 1
    return index // 4, index % 4 + 1


def period_end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, (31 if quarter in {1, 4} else 30))


def previous_market_period(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def _first_non_null(current: Decimal | None, replacement: Decimal | None) -> Decimal | None:
    return current if current is not None else replacement


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
    six_k_backfill_mode = False

    def __init__(
        self, repository: USEarningsRepository, sec: SecEdgarClient,
        constituents: USIndexConstituentClient, fx: EcosFxClient | None = None,
    ) -> None:
        self.repository, self.sec, self.constituents, self.fx = repository, sec, constituents, fx
        self._historical_company_ids: set[str] = set()

    @classmethod
    def from_env(cls) -> "USEarningsAutomaticPipeline":
        sec = SecEdgarClient.from_env()
        fx = EcosFxClient(os.environ["ECOS_API_KEY"]) if os.getenv("ECOS_API_KEY", "").strip() else None
        return cls(USEarningsRepository.from_env(), sec, USIndexConstituentClient(sec), fx)

    def _fx_to_usd(self, currency: str, reference_date: date) -> Decimal:
        if currency == "USD":
            return Decimal(1)
        if self.fx is None:
            raise ProviderError(f"{currency}/USD conversion requires ECOS_API_KEY")
        _, source_krw = self.fx.latest_krw(currency, reference_date)
        _, usd_krw = self.fx.latest_usd_krw(reference_date)
        return source_krw / usd_krw

    def _six_k_candidates(self, company_id: str, cik: str, year: int, quarter: int) -> list[USFinancialFact]:
        if not hasattr(self.sec, "six_k_filings") or not hasattr(self.sec, "six_k_documents"):
            return []
        target_end = period_end(year, quarter)
        filings = self.sec.six_k_filings(
            cik, filed_from=target_end - timedelta(days=10), filed_to=target_end + timedelta(days=100),
        )
        result: list[USFinancialFact] = []
        for filing in filings:
            fact = extract_six_k_fact(
                company_id, filing, self.sec.six_k_documents(cik, filing), year, quarter,
                self._fx_to_usd, backfill_mode=self.six_k_backfill_mode,
            )
            if fact is not None:
                result.append(fact)
        if not result:
            return []
        complete = [fact for fact in result if fact.fully_complete]
        pool = complete or result
        earliest = min(fact.filing_date for fact in pool)
        same_release = [fact for fact in pool if fact.filing_date == earliest]
        selected = max(same_release, key=lambda fact: (
            sum(value is not None for value in (fact.top_line, fact.operating_income, fact.net_income)),
            abs(fact.top_line or Decimal(0)),
        ))
        return [selected]

    def _exact_inline_xbrl_candidate(
        self, company_id: str, cik: str, year: int, quarter: int,
        *, filings: list[SecFinancialFiling] | None = None,
    ) -> USFinancialFact | None:
        """Read an exact quarter from a domestic filing when companyfacts lags."""
        if not hasattr(self.sec, "financial_filings") or not hasattr(self.sec, "inline_xbrl_instance"):
            return None
        if filings is None:
            target_end = period_end(year, quarter)
            filings = self.sec.financial_filings(
                cik, filed_from=target_end - timedelta(days=10), filed_to=target_end + timedelta(days=180),
            )
        candidates: list[USFinancialFact] = []
        for filing in filings:
            if filing.report_date is not None and market_period(filing.report_date) != (year, quarter):
                continue
            content = self.sec.inline_xbrl_instance(cik, filing)
            if content is None:
                continue
            fact = extract_inline_xbrl_fact(
                company_id, content, year=year, quarter=quarter,
                accession=filing.accession, filing_date=filing.filing_date,
            )
            if fact is not None:
                candidates.append(fact)
        return max(candidates, key=lambda fact: (fact.period_end, fact.filing_date)) if candidates else None

    @staticmethod
    def _financial_queue_item(company: dict[str, Any], filing: SecFinancialFiling) -> dict[str, Any]:
        return {
            "company_id": str(company["company_id"]), "company_name": str(company.get("company_name") or ""),
            "cik": str(company.get("cik") or ""), "accession": filing.accession,
            "filing_date": filing.filing_date.isoformat(),
            "report_date": filing.report_date.isoformat() if filing.report_date else None,
            "primary_document": filing.primary_document,
        }

    @staticmethod
    def _six_k_queue_item(company: dict[str, Any], filing: SixKFiling) -> dict[str, Any]:
        return {
            "company_id": str(company["company_id"]), "company_name": str(company.get("company_name") or ""),
            "cik": str(company.get("cik") or ""), "accession": filing.accession,
            "filing_date": filing.filing_date.isoformat(),
            "report_date": filing.report_date.isoformat() if filing.report_date else None,
            "primary_document": filing.primary_document,
        }

    @staticmethod
    def _queued_financial_filing(item: dict[str, Any]) -> SecFinancialFiling | None:
        try:
            return SecFinancialFiling(
                str(item["accession"]), date.fromisoformat(str(item["filing_date"])),
                date.fromisoformat(str(item["report_date"])) if item.get("report_date") else None,
                str(item["primary_document"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _queued_six_k_filing(item: dict[str, Any]) -> SixKFiling | None:
        try:
            return SixKFiling(
                str(item["accession"]), date.fromisoformat(str(item["filing_date"])),
                date.fromisoformat(str(item["report_date"])) if item.get("report_date") else None,
                str(item["primary_document"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

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
            if item.ticker and re.fullmatch(r"[A-Z][A-Z0-9./-]{0,9}", item.ticker) and item.ticker != item.cik:
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
        default_since = min(since, current_day)
        company_cursors = cursor.get("company_last_checked_dates")
        if not isinstance(company_cursors, dict):
            company_cursors = {}
        queued_financial_items = cursor.get("unresolved_financial_filings")
        if not isinstance(queued_financial_items, list):
            queued_financial_items = []
        queued_six_k_items = cursor.get("unresolved_six_k_filings")
        if not isinstance(queued_six_k_items, list):
            queued_six_k_items = []
        companies_by_id = {
            str(company["company_id"]): company
            for company in self.repository.us_active_companies(current_day.year - 2)
        }
        for item in [*queued_financial_items, *queued_six_k_items]:
            if not isinstance(item, dict) or not item.get("company_id"):
                continue
            companies_by_id.setdefault(str(item["company_id"]), {
                "company_id": str(item["company_id"]), "company_name": str(item.get("company_name") or ""),
                "cik": str(item.get("cik") or ""),
            })
        companies = list(companies_by_id.values())
        queued_financial_by_company: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        queued_six_k_by_company: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for item in queued_financial_items:
            if isinstance(item, dict) and item.get("company_id") and item.get("accession"):
                queued_financial_by_company[str(item["company_id"])][str(item["accession"])] = item
        for item in queued_six_k_items:
            if isinstance(item, dict) and item.get("company_id") and item.get("accession"):
                queued_six_k_by_company[str(item["company_id"])][str(item["accession"])] = item
        changed: dict[tuple[str, int, int], USFinancialFact] = {}
        issues: list[dict[str, str]] = []
        checked = 0
        for company in companies:
            company_id = str(company["company_id"])
            cik = str(company.get("cik") or "")
            if not cik:
                issues.append({"company": str(company.get("company_name") or company["company_id"]), "reason": "SEC CIK missing"})
                continue
            checked += 1
            try:
                company_since = date.fromisoformat(str(company_cursors.get(company_id)))
            except (TypeError, ValueError):
                company_since = default_since
            # Preserve this company's own lower bound if any provider call fails.
            # Advancing only the global cursor would otherwise age out its filing.
            company_cursors.setdefault(company_id, company_since.isoformat())
            try:
                financial_filings: dict[str, SecFinancialFiling] = {}
                if hasattr(self.sec, "financial_filings"):
                    financial_filings.update({
                        filing.accession: filing for filing in self.sec.financial_filings(
                            cik, filed_from=min(company_since, current_day), filed_to=current_day,
                        )
                    })
                    for item in queued_financial_by_company.get(company_id, {}).values():
                        filing = self._queued_financial_filing(item)
                        if filing is not None:
                            financial_filings[filing.accession] = filing
                    if financial_filings:
                        facts = extract_new_sec_facts(
                            company_id, self.sec.company_facts(cik), set(financial_filings),
                        )
                        represented = {fact.source_filing_id for fact in facts}
                        for fact in facts:
                            changed[(fact.company_id, fact.fiscal_year, fact.fiscal_quarter)] = fact
                        for accession, filing in financial_filings.items():
                            if accession in represented:
                                queued_financial_by_company[company_id].pop(accession, None)
                                continue
                            target = market_period(filing.report_date) if filing.report_date else latest_completed_period(filing.filing_date)
                            fact = self._exact_inline_xbrl_candidate(
                                company_id, cik, target[0], target[1], filings=[filing],
                            )
                            if fact is not None:
                                changed[(fact.company_id, fact.fiscal_year, fact.fiscal_quarter)] = fact
                                queued_financial_by_company[company_id].pop(accession, None)
                            else:
                                queued_financial_by_company[company_id][accession] = self._financial_queue_item(company, filing)
                else:
                    accessions = self.sec.new_financial_accessions(cik, company_since)
                    for fact in extract_new_sec_facts(
                        company_id, self.sec.company_facts(cik), accessions,
                    ) if accessions else ():
                        changed[(fact.company_id, fact.fiscal_year, fact.fiscal_quarter)] = fact

                six_k_filings: dict[str, SixKFiling] = {
                    filing.accession: filing for filing in self.sec.six_k_filings(
                        cik, filed_from=min(company_since, current_day) - timedelta(days=2), filed_to=current_day,
                    )
                }
                for item in queued_six_k_by_company.get(company_id, {}).values():
                    filing = self._queued_six_k_filing(item)
                    if filing is not None:
                        six_k_filings[filing.accession] = filing
                for filing in six_k_filings.values():
                    documents = self.sec.six_k_documents(cik, filing)
                    targets = {latest_completed_period(filing.filing_date)}
                    if filing.report_date is not None:
                        targets.add(market_period(filing.report_date))
                    produced = False
                    for year, quarter in targets:
                        fact = extract_six_k_fact(
                            company_id, filing, documents, year, quarter,
                            self._fx_to_usd,
                        )
                        if fact is not None:
                            changed[(fact.company_id, fact.fiscal_year, fact.fiscal_quarter)] = fact
                            produced = True
                    if produced or not has_six_k_results_context(documents):
                        queued_six_k_by_company[company_id].pop(filing.accession, None)
                    else:
                        queued_six_k_by_company[company_id][filing.accession] = self._six_k_queue_item(company, filing)
                company_cursors[company_id] = current_day.isoformat()
            except ProviderError as exc:
                issues.append({"company": str(company.get("company_name") or company["company_id"]), "reason": str(exc)})
        if write and changed:
            self.repository.upsert_company_quarters(fact.db_row() for fact in changed.values())
            affected = {market_period(fact.period_end) for fact in changed.values()}
            for year, quarter in affected:
                self.recalculate_market_period(year, quarter)
        unresolved_financial = [item for group in queued_financial_by_company.values() for item in group.values()]
        unresolved_six_k = [item for group in queued_six_k_by_company.values() for item in group.values()]
        status = "incomplete" if issues or unresolved_financial or unresolved_six_k else "ready"
        result = {"date": current_day.isoformat(), "status": status, "write": write, "companies_checked": checked,
                  "updated_company_quarters": len(changed), "issues": issues,
                  "unresolved_financial_filings": len(unresolved_financial),
                  "unresolved_six_k_filings": len(unresolved_six_k),
                  "requests": {"sec": self.sec.request_count}}
        if write:
            self.repository.save_us_state("daily_edgar", status, {
                "last_checked_date": current_day.isoformat(),
                "company_last_checked_dates": company_cursors,
                "unresolved_financial_filings": unresolved_financial,
                "unresolved_six_k_filings": unresolved_six_k,
            }, None)
        return result

    def retry_incomplete(self, *, today: date | None = None, write: bool = True) -> dict[str, Any]:
        """Recheck only U.S. pending rows; completed rows never enter this phase."""
        current_day = today or date.today()
        pending = self.repository.us_pending_rows(current_day.year - 2)
        unique_pending = {
            (str(row["company_id"]), int(row["market_year"]), int(row["market_quarter"])): row
            for row in pending
        }
        if not unique_pending:
            result = {"date": current_day.isoformat(), "status": "ready", "write": write,
                      "companies_checked": 0, "updated_company_quarters": 0,
                      "remaining_pending_company_quarters": 0, "issues": []}
            if write:
                self.repository.save_us_state("retry_incomplete", "ready", {"date": current_day.isoformat()})
            return result

        company_ids = sorted({key[0] for key in unique_pending})
        companies = {
            str(row["company_id"]): row
            for row in self.repository.us_active_companies(current_day.year - 2)
            if str(row.get("company_id") or "") in company_ids
        }
        history = self.repository.company_history(company_ids)
        by_market_period: dict[tuple[str, int, int], USFinancialFact] = {}
        for row in history:
            try:
                fact = fact_from_row(row)
                key = (fact.company_id, int(row["market_year"]), int(row["market_quarter"]))
                existing = by_market_period.get(key)
                if existing is None or fact.period_end > existing.period_end:
                    by_market_period[key] = fact
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue

        changed: dict[tuple[str, int, int], USFinancialFact] = {}
        issues: list[dict[str, str]] = []
        try:
            current_ciks: set[str] | None = {cik for _, _, cik in self.sec.company_ticker_rows()}
        except ProviderError as exc:
            current_ciks = None
            issues.append({"company": "SEC ticker directory", "reason": str(exc)})
        remaining_pending = len(unique_pending)
        checked = 0
        for company_id in company_ids:
            company = companies.get(company_id)
            cik = str(company.get("cik") or "") if company else ""
            company_name = str(company.get("company_name") or company_id) if company else company_id
            company_pending = [key for key in unique_pending if key[0] == company_id]
            if not cik:
                issues.append({"company": company_name, "reason": "SEC CIK missing"})
                continue
            checked += 1
            try:
                facts_payload = self.sec.company_facts(cik)
                accessions = {
                    by_market_period[key].source_filing_id
                    for key in company_pending if key in by_market_period
                }
                refreshed = {
                    (fact.fiscal_year, fact.fiscal_quarter): fact
                    for fact in extract_new_sec_facts(company_id, facts_payload, accessions)
                }
                for key in company_pending:
                    for fact in self._six_k_candidates(company_id, cik, key[1], key[2]):
                        refreshed[(fact.fiscal_year, fact.fiscal_quarter)] = fact
                    exact = self._exact_inline_xbrl_candidate(company_id, cik, key[1], key[2])
                    if exact is not None:
                        refreshed[(exact.fiscal_year, exact.fiscal_quarter)] = exact
                disappeared = current_ciks is not None and cik.zfill(10) not in current_ciks
                delisting_dates = self.sec.delisting_dates(cik) if disappeared else []
            except ProviderError as exc:
                issues.append({"company": company_name, "reason": str(exc)})
                continue

            for key in company_pending:
                current = by_market_period.get(key)
                if current is None:
                    issues.append({"company": company_name, "reason": f"pending row {key[1]}Q{key[2]} not readable"})
                    continue
                candidate = refreshed.get(current.key)
                updated = current
                if candidate is not None:
                    updated = current.with_changes(
                        top_line=_first_non_null(current.top_line, candidate.top_line),
                        operating_income=_first_non_null(current.operating_income, candidate.operating_income),
                        net_income=_first_non_null(current.net_income, candidate.net_income),
                    )
                still_pending = not updated.fully_complete
                delisting_cutoff = current.period_end + timedelta(days=120)
                delisting_window_start = current.period_start or date(key[1], (key[2] - 1) * 3 + 1, 1)
                applicable_delisting = next((
                    filed for filed in delisting_dates
                    if delisting_window_start <= filed <= delisting_cutoff
                ), None)
                if still_pending and disappeared and applicable_delisting:
                    prior_key = (company_id, *previous_market_period(key[1], key[2]))
                    prior = by_market_period.get(prior_key)
                    if prior is not None:
                        updated = updated.with_changes(
                            top_line=_first_non_null(updated.top_line, prior.top_line),
                            operating_income=_first_non_null(updated.operating_income, prior.operating_income),
                            net_income=_first_non_null(updated.net_income, prior.net_income),
                            source_filing_id=(
                                f"{current.source_filing_id}|carry-forward-form25-{applicable_delisting.isoformat()}"
                            ),
                        )
                updated = updated.with_changes(is_pending=not updated.fully_complete)
                if not updated.is_pending:
                    remaining_pending -= 1
                if updated != current:
                    changed[(updated.company_id, updated.fiscal_year, updated.fiscal_quarter)] = updated

        if write and changed:
            self.repository.upsert_company_quarters(fact.db_row() for fact in changed.values())
            affected = {market_period(fact.period_end) for fact in changed.values()}
            for year, quarter in affected:
                self.recalculate_market_period(year, quarter)
        status = "incomplete" if issues or remaining_pending else "ready"
        result = {"date": current_day.isoformat(), "status": status, "write": write,
                  "companies_checked": checked, "updated_company_quarters": len(changed),
                  "remaining_pending_company_quarters": remaining_pending, "issues": issues,
                  "requests": {"sec": self.sec.request_count}}
        if write:
            self.repository.save_us_state("retry_incomplete", status, {"date": current_day.isoformat()})
        return result

    def recalculate_market_period(self, year: int, quarter: int) -> None:
        for market in MARKETS:
            members = self.repository.us_universe(market, year, quarter)
            if len(members) != 100:
                continue
            facts = {row.company_id: row for row in map(fact_from_row, self.repository.us_market_facts(market, year, quarter))}
            previous_year, previous_quarter = previous_market_period(year, quarter)
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

