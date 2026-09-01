from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from .aggregation import aggregate_market, calculate_market_series
from .models import CompanyIdentity, FinancialFact, MarketFact, Security
from .providers import EcosFxClient, KisClient, KrxClient, OpenDartClient
from .repository import EarningsV2Repository
from .transform import calculate_financial_series, decimal_value, extract_company_fact


TARGETS = {"kr_largecap": 100, "kr_kosdaq": 50}
EXCHANGES = {"kr_largecap": "KOSPI", "kr_kosdaq": "KOSDAQ"}
# V6부터 부분 기업행을 보존하고 잠정 바구니와 확정 총합을 분리한다.
CALCULATION_VERSION = 6


def quarter_end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, 31 if quarter in {1, 4} else 30)


def previous_period(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def _eligible_name(name: str) -> bool:
    compact = re.sub(r"\s+", "", name)
    if any(token in compact for token in ("스팩", "리츠", "인프라", "부동산", "우선주")):
        return False
    return re.search(r"우(?:[A-Z]|\d+[A-Z]?)?$", compact) is None


def _group(rows: Iterable[dict[str, Any]], corp_codes: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    result = {code: [] for code in corp_codes}
    for row in rows:
        corp_code = str(row.get("corp_code") or "").strip()
        if corp_code in result:
            result[corp_code].append(row)
    return result


def _financial_from_db(row: dict[str, Any]) -> FinancialFact:
    filing_text = str(row.get("filing_date") or row.get("period_end"))
    return FinancialFact(
        company_id=str(row["company_id"]), fiscal_year=int(row["fiscal_year"]),
        fiscal_quarter=int(row["fiscal_quarter"]), period_end=date.fromisoformat(str(row["period_end"])),
        top_line=decimal_value(row.get("top_line")), operating_income=decimal_value(row.get("operating_income")),
        net_income=decimal_value(row.get("net_income")), currency=str(row.get("currency") or "KRW"),
        consolidation_scope=str(row.get("consolidation_scope") or "CFS"),
        source_filing_id=str(row.get("source_filing_id") or "stored"), filing_date=date.fromisoformat(filing_text),
        is_pending=bool(row.get("is_pending", False)),
        operating_margin_pct=decimal_value(row.get("operating_margin_pct")),
        net_margin_pct=decimal_value(row.get("net_margin_pct")),
        operating_income_yoy_pct=decimal_value(row.get("operating_income_yoy_pct")),
        operating_income_yoy_state=str(row.get("operating_income_yoy_state") or "missing_prior"),
        net_income_yoy_pct=decimal_value(row.get("net_income_yoy_pct")),
        net_income_yoy_state=str(row.get("net_income_yoy_state") or "missing_prior"),
        operating_income_qoq_sa_pct=decimal_value(row.get("operating_income_qoq_sa_pct")),
        operating_income_qoq_state=str(row.get("operating_income_qoq_state") or "insufficient_history"),
        net_income_qoq_sa_pct=decimal_value(row.get("net_income_qoq_sa_pct")),
        net_income_qoq_state=str(row.get("net_income_qoq_state") or "insufficient_history"),
    )


def _market_from_db(row: dict[str, Any]) -> MarketFact:
    return MarketFact(
        market_id=str(row["market_id"]), market_year=int(row["market_year"]),
        market_quarter=int(row["market_quarter"]), reference_date=date.fromisoformat(str(row["reference_date"])),
        top_line_total=decimal_value(row.get("top_line_total")),
        operating_income_total=decimal_value(row.get("operating_income_total")),
        net_income_total=decimal_value(row.get("net_income_total")),
        operating_margin_pct=decimal_value(row.get("operating_margin_pct")),
        net_margin_pct=decimal_value(row.get("net_margin_pct")),
        reported_company_count=int(row.get("reported_company_count") or 0),
        pending_company_count=int(row.get("pending_company_count") or 0),
        target_company_count=int(row["target_company_count"]),
        completion_status=str(row.get("lifecycle_status") or row["completion_status"]),
        operating_income_yoy_pct=decimal_value(row.get("operating_income_yoy_pct")),
        operating_income_yoy_state=str(row.get("operating_income_yoy_state") or "missing_prior"),
        net_income_yoy_pct=decimal_value(row.get("net_income_yoy_pct")),
        net_income_yoy_state=str(row.get("net_income_yoy_state") or "missing_prior"),
        operating_income_qoq_sa_pct=decimal_value(row.get("operating_income_qoq_sa_pct")),
        operating_income_qoq_state=str(row.get("operating_income_qoq_state") or "insufficient_history"),
        net_income_qoq_sa_pct=decimal_value(row.get("net_income_qoq_sa_pct")),
        net_income_qoq_state=str(row.get("net_income_qoq_state") or "insufficient_history"),
    )


def _identity_from_universe(row: dict[str, Any]) -> CompanyIdentity:
    return CompanyIdentity(
        company_id=str(row["company_id"]), company_name=str(row.get("company_name") or row["company_id"]),
        stock_code=str(row.get("stock_code") or ""), corp_code=str(row.get("corp_code") or ""),
        market_id=str(row["market_id"]), rank=int(row["market_cap_rank"]),
        market_cap=decimal_value(row.get("market_cap")) or Decimal(0),
        reference_date=date.fromisoformat(str(row["reference_date"])),
    )


class KoreaEarningsV2Pipeline:
    """분기 기업군, 부분 실적, 잠정·확정 집계를 한 생명주기로 관리한다."""

    def __init__(self, *, krx: KrxClient, dart: OpenDartClient, repository: EarningsV2Repository,
                 kis: KisClient | None = None, fx: EcosFxClient | None = None) -> None:
        self.krx, self.dart, self.repository, self.kis, self.fx = krx, dart, repository, kis, fx

    @staticmethod
    def _progress(stage: str, **details: Any) -> None:
        print(json.dumps({"stage": stage, **details}, ensure_ascii=False, default=str), flush=True)

    @classmethod
    def from_env(cls) -> "KoreaEarningsV2Pipeline":
        repository = EarningsV2Repository.from_env()
        kis = None
        if os.getenv("KIS_APP_KEY", "").strip() and os.getenv("KIS_APP_SECRET", "").strip():
            kis = KisClient(os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"],
                            cached_token=repository.cached_kis_token, save_token=repository.save_kis_token)
        fx = EcosFxClient(os.environ["ECOS_API_KEY"]) if os.getenv("ECOS_API_KEY", "").strip() else None
        return cls(krx=KrxClient.from_env(), dart=OpenDartClient.from_env(), repository=repository, kis=kis, fx=fx)

    def _latest_public_operating_income(self, company_ids: Iterable[str], reference_date: date) -> dict[str, Decimal]:
        latest: dict[str, tuple[tuple[int, int], Decimal]] = {}
        for record in self.repository.company_history(company_ids):
            filing = str(record.get("filing_date") or "")
            value = decimal_value(record.get("operating_income"))
            if not filing or filing > reference_date.isoformat() or value is None:
                continue
            company_id = str(record["company_id"])
            key = (int(record["fiscal_year"]), int(record["fiscal_quarter"]))
            if company_id not in latest or key > latest[company_id][0]:
                latest[company_id] = (key, value)
        return {company_id: value for company_id, (_, value) in latest.items()}

    def discover_universe(self, market_id: str, year: int, quarter: int,
                          corp_map: dict[str, tuple[str, str]]) -> list[CompanyIdentity]:
        reference_date, securities = self.krx.last_trading_day(market_id, quarter_end(year, quarter))
        by_company: dict[str, tuple[Security, str]] = {}
        for security in securities:
            mapped = corp_map.get(security.stock_code)
            if mapped is None or not _eligible_name(security.name):
                continue
            corp_code, official_name = mapped
            current = by_company.get(corp_code)
            if current is None or security.market_cap > current[0].market_cap:
                by_company[corp_code] = (security, official_name)
        candidates = sorted(by_company.items(), key=lambda item: (-item[1][0].market_cap, item[1][0].stock_code))
        target = TARGETS[market_id]
        if len(candidates) < target:
            raise RuntimeError(f"{market_id} universe is {len(candidates)}/{target}")

        cutoff = candidates[target - 1][1][0].market_cap
        above = [item for item in candidates if item[1][0].market_cap > cutoff]
        tied = [item for item in candidates if item[1][0].market_cap == cutoff]
        if len(above) < target < len(above) + len(tied):
            prior_year, prior_quarter = previous_period(year, quarter)
            incumbents = {str(row["company_id"]) for row in self.repository.universe(market_id, prior_year, prior_quarter)}
            tie_ids = [f"kr:{corp_code}" for corp_code, _ in tied]
            operating = self._latest_public_operating_income(tie_ids, reference_date)
            tied.sort(key=lambda item: (
                0 if f"kr:{item[0]}" in incumbents else 1,
                0 if f"kr:{item[0]}" in operating else 1,
                -operating.get(f"kr:{item[0]}", Decimal(0)), item[1][0].stock_code,
            ))
        selected = above + tied[:target - len(above)]
        selected.sort(key=lambda item: (-item[1][0].market_cap, item[1][0].stock_code))
        return [CompanyIdentity(
            company_id=f"kr:{corp_code}", company_name=official_name or security.name,
            stock_code=security.stock_code, corp_code=corp_code, market_id=market_id,
            rank=rank, market_cap=security.market_cap, reference_date=reference_date,
        ) for rank, (corp_code, (security, official_name)) in enumerate(selected, 1)]

    def collect_financials(self, identities: Iterable[CompanyIdentity], year: int, quarter: int
                           ) -> tuple[dict[str, FinancialFact], list[dict[str, str]]]:
        identities = list(identities)
        codes = [row.corp_code for row in identities]
        current = _group(self.dart.multi_accounts(codes, year, quarter), codes)
        previous = _group(self.dart.multi_accounts(codes, year, quarter - 1), codes) if quarter > 1 else {code: [] for code in codes}
        facts: dict[str, FinancialFact] = {}
        issues: list[dict[str, str]] = []
        end = quarter_end(year, quarter)
        for identity in identities:
            fact = extract_company_fact(identity.corp_code, identity.company_id, year, quarter,
                                        current[identity.corp_code], previous[identity.corp_code])
            if fact is None:
                fact = FinancialFact(
                    identity.company_id, year, quarter, end, None, None, None, "KRW", "CFS",
                    f"pending:{identity.corp_code}:{year}:Q{quarter}", end, is_pending=True,
                )
            if fact.currency != "KRW":
                if fact.currency == "USD" and self.fx is not None:
                    rate = self.fx.usd_krw(end)
                    fact = fact.with_changes(
                        top_line=fact.top_line * rate if fact.top_line is not None else None,
                        operating_income=fact.operating_income * rate if fact.operating_income is not None else None,
                        net_income=fact.net_income * rate if fact.net_income is not None else None,
                        currency="KRW",
                    )
                else:
                    fact = fact.with_changes(is_pending=True)
                    issues.append({"company": identity.company_name, "field": "currency", "reason": f"unsupported {fact.currency}"})
            if fact.top_line is None and fact.profit_complete and year >= 2019 and self.kis is not None:
                self._progress("kis_top_line_start", company=identity.company_name, ticker=identity.stock_code)
                fact = fact.with_changes(top_line=self.kis.quarter_top_line(identity.stock_code, year, quarter))
                self._progress("kis_top_line_done", company=identity.company_name, found=fact.top_line is not None)
            fact = fact.with_changes(is_pending=fact.is_pending or not fact.fully_complete)
            for field in ("top_line", "operating_income", "net_income"):
                if getattr(fact, field) is None:
                    issues.append({"company": identity.company_name, "field": field, "reason": "provider value missing"})
            facts[identity.company_id] = fact
        return facts, issues

    def _calculated_histories(self, facts: dict[str, FinancialFact]) -> dict[str, list[FinancialFact]]:
        histories: dict[str, list[FinancialFact]] = defaultdict(list)
        for record in self.repository.company_history(facts):
            if int(record.get("calculation_version") or 0) >= CALCULATION_VERSION:
                row = _financial_from_db(record)
                histories[row.company_id].append(row)
        for company_id, fact in facts.items():
            histories[company_id] = [row for row in histories[company_id] if row.key != fact.key] + [fact]
        return {company_id: calculate_financial_series(rows) for company_id, rows in histories.items()}

    def _market_rows(self, universes: dict[str, list[CompanyIdentity]], histories: dict[str, list[FinancialFact]],
                     year: int, quarter: int) -> dict[str, list[MarketFact]]:
        output: dict[str, list[MarketFact]] = {}
        prior_year, prior_quarter = previous_period(year, quarter)
        for market_id, members in universes.items():
            previous_members = [_identity_from_universe(row) for row in self.repository.universe(market_id, prior_year, prior_quarter)]
            needed_ids = {row.company_id for row in members + previous_members}
            stored: dict[str, list[FinancialFact]] = defaultdict(list)
            for record in self.repository.company_history(needed_ids):
                if int(record.get("calculation_version") or 0) >= CALCULATION_VERSION:
                    item = _financial_from_db(record)
                    stored[item.company_id].append(item)
            for company_id, rows in histories.items():
                if company_id in needed_ids:
                    stored[company_id] = rows
            current_facts = {member.company_id: next((row for row in stored.get(member.company_id, []) if row.key == (year, quarter)), None) for member in members}
            comparison_facts = {member.company_id: next((row for row in stored.get(member.company_id, []) if row.key == (prior_year, prior_quarter)), None) for member in previous_members}
            market = aggregate_market(
                market_id, year, quarter, members, current_facts, TARGETS[market_id],
                comparison_members=previous_members, comparison_facts=comparison_facts,
            )
            existing: dict[tuple[int, int], MarketFact] = {}
            for record in self.repository.market_history(market_id):
                if int(record.get("calculation_version") or 0) >= CALCULATION_VERSION:
                    prior = _market_from_db(record)
                    existing[prior.key] = prior
            existing[market.key] = market
            output[market_id] = calculate_market_series(existing.values())
        return output

    def _save_universes(self, universes: dict[str, list[CompanyIdentity]], year: int, quarter: int) -> None:
        identities = list({row.company_id: row for rows in universes.values() for row in rows}.values())
        self.repository.upsert_companies({
            "company_id": row.company_id, "country": "KR", "company_name": row.company_name,
            "reporting_currency": "KRW", "entity_kind": "general",
        } for row in identities)
        self.repository.upsert_identifiers(item for row in identities for item in (
            {"company_id": row.company_id, "identifier_type": "dart_corp_code", "identifier_value": row.corp_code, "is_primary": True},
            {"company_id": row.company_id, "identifier_type": "krx_code", "identifier_value": row.stock_code, "exchange": EXCHANGES[row.market_id], "is_primary": True},
        ))
        for market_id, members in universes.items():
            self.repository.replace_universe(market_id, year, quarter, ({
                "market_id": market_id, "market_year": year, "market_quarter": quarter,
                "reference_date": row.reference_date, "company_id": row.company_id,
                "market_cap_rank": row.rank, "market_cap": row.market_cap,
                "currency": "KRW", "selection_method": "direct_market_cap",
            } for row in members))

    def run_quarter(self, year: int, quarter: int, *, write: bool = False,
                    allow_review: bool = False) -> dict[str, Any]:
        operation = f"{year}Q{quarter}"
        if write:
            self.repository.save_state(operation, "running", {})
        try:
            universes: dict[str, list[CompanyIdentity]] = {}
            missing_markets: list[str] = []
            for market_id, target in TARGETS.items():
                frozen = self.repository.universe(market_id, year, quarter)
                if len(frozen) == target:
                    universes[market_id] = [_identity_from_universe(row) for row in frozen]
                else:
                    missing_markets.append(market_id)

            discovered: dict[str, list[CompanyIdentity]] = {}
            if missing_markets:
                self._progress("dart_corporation_map_start", period=operation)
                corp_map = self.dart.corporation_map()
                discovered = {
                    market_id: self.discover_universe(market_id, year, quarter, corp_map)
                    for market_id in missing_markets
                }
                universes.update(discovered)
            self._progress("krx_universe_done", **{market: len(rows) for market, rows in universes.items()})
            if write and discovered:
                self._save_universes(discovered, year, quarter)
            identities = list({row.company_id: row for rows in universes.values() for row in rows}.values())
            facts, issues = self.collect_financials(identities, year, quarter)
            histories = self._calculated_histories(facts)
            markets = self._market_rows(universes, histories, year, quarter)
            current_facts = [row for rows in histories.values() for row in rows if row.key == (year, quarter)]
            current_markets = [row for rows in markets.values() for row in rows if row.key == (year, quarter)]
            if write:
                self.repository.upsert_company_quarters(row.db_row(calculation_version=CALCULATION_VERSION) for row in current_facts)
                self.repository.upsert_market_quarters(row.db_row(calculation_version=CALCULATION_VERSION) for row in current_markets)
            ready = not issues and all(row.completion_status == "complete" for row in current_markets)
            status = "ready" if ready else "incomplete"
            summary = {
                "period": operation, "write": write,
                "universe": {market: len(rows) for market, rows in universes.items()},
                "companies": len(identities), "facts": len(current_facts),
                "complete_facts": sum(not row.is_pending for row in current_facts),
                "markets": {row.market_id: row.completion_status for row in current_markets},
                "issues": issues,
                "requests": {"krx": self.krx.request_count, "open_dart": self.dart.request_count,
                             "kis": self.kis.request_count if self.kis else 0,
                             "ecos": self.fx.request_count if self.fx else 0},
                "status": status,
            }
            if write:
                self.repository.save_state(operation, status, summary)
            return summary
        except Exception as error:
            if write:
                self.repository.save_state(operation, "failed", {}, str(error)[:2000])
            raise

    def run_year(self, year: int, *, write: bool = False, allow_review: bool = False) -> list[dict[str, Any]]:
        results = []
        for quarter in range(1, 5):
            result = self.run_quarter(year, quarter, write=write, allow_review=allow_review)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
            if result["status"] != "ready" and not allow_review:
                break
        return results
