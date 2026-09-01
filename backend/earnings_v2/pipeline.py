from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from .models import CompanyIdentity, FinancialFact, MarketFact
from .providers import EcosFxClient, KisClient, KrxClient, OpenDartClient
from .repository import EarningsV2Repository
from .transform import (
    aggregate_market,
    calculate_financial_series,
    calculate_market_series,
    decimal_value,
    extract_company_fact,
)


TARGETS = {"kr_largecap": 100, "kr_kosdaq": 50}
EXCHANGES = {"kr_largecap": "KOSPI", "kr_kosdaq": "KOSDAQ"}
# 삭제한 파일럿은 company=4, market=1이었다. 새 세대는 5부터 시작하며
# 이보다 낮은 행을 성장률 이력에 섞지 않는다. 기존 DB를 남겨둔 상태에서도
# 새 수집 결과가 구 계산값에 오염되지 않게 하는 명시적 세대 경계다.
CALCULATION_VERSION = 5


def quarter_end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, 31 if quarter in {1, 4} else 30)


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
        quality_status=str(row.get("quality_status") or "draft"),
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
        market_quarter=int(row["market_quarter"]),
        average_operating_income=decimal_value(row.get("average_operating_income")),
        average_net_income=decimal_value(row.get("average_net_income")),
        operating_margin_pct=decimal_value(row.get("operating_margin_pct")),
        net_margin_pct=decimal_value(row.get("net_margin_pct")),
        actual_company_count=int(row.get("actual_company_count") or 0),
        target_company_count=int(row["target_company_count"]), completion_status=str(row["completion_status"]),
        operating_income_yoy_pct=decimal_value(row.get("operating_income_yoy_pct")),
        operating_income_yoy_state=str(row.get("operating_income_yoy_state") or "missing_prior"),
        net_income_yoy_pct=decimal_value(row.get("net_income_yoy_pct")),
        net_income_yoy_state=str(row.get("net_income_yoy_state") or "missing_prior"),
        operating_income_qoq_sa_pct=decimal_value(row.get("operating_income_qoq_sa_pct")),
        operating_income_qoq_state=str(row.get("operating_income_qoq_state") or "insufficient_history"),
        net_income_qoq_sa_pct=decimal_value(row.get("net_income_qoq_sa_pct")),
        net_income_qoq_state=str(row.get("net_income_qoq_state") or "insufficient_history"),
    )


class KoreaEarningsV2Pipeline:
    """한 분기를 완전히 검증한 뒤에만 저장하는 한국 V2 실행기."""

    def __init__(
        self,
        *,
        krx: KrxClient,
        dart: OpenDartClient,
        repository: EarningsV2Repository,
        kis: KisClient | None = None,
        fx: EcosFxClient | None = None,
    ) -> None:
        self.krx = krx
        self.dart = dart
        self.repository = repository
        self.kis = kis
        self.fx = fx

    @classmethod
    def from_env(cls) -> "KoreaEarningsV2Pipeline":
        repository = EarningsV2Repository.from_env()
        kis = None
        if os.getenv("KIS_APP_KEY", "").strip() and os.getenv("KIS_APP_SECRET", "").strip():
            kis = KisClient(
                os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"],
                cached_token=repository.cached_kis_token,
                save_token=repository.save_kis_token,
            )
        fx = EcosFxClient(os.environ["ECOS_API_KEY"]) if os.getenv("ECOS_API_KEY", "").strip() else None
        return cls(krx=KrxClient.from_env(), dart=OpenDartClient.from_env(), repository=repository, kis=kis, fx=fx)

    def discover_universe(
        self,
        market_id: str,
        year: int,
        quarter: int,
        corp_map: dict[str, tuple[str, str]],
    ) -> list[CompanyIdentity]:
        reference_date, securities = self.krx.last_trading_day(market_id, quarter_end(year, quarter))
        selected = [row for row in securities if row.stock_code in corp_map and _eligible_name(row.name)][:TARGETS[market_id]]
        if len(selected) != TARGETS[market_id]:
            raise RuntimeError(f"{market_id} universe is {len(selected)}/{TARGETS[market_id]}")
        result = []
        for rank, security in enumerate(selected, 1):
            corp_code, official_name = corp_map[security.stock_code]
            result.append(CompanyIdentity(
                company_id=f"kr:{corp_code}", company_name=official_name or security.name,
                stock_code=security.stock_code, corp_code=corp_code, market_id=market_id,
                rank=rank, market_cap=security.market_cap, reference_date=reference_date,
            ))
        return result

    def collect_financials(
        self,
        identities: Iterable[CompanyIdentity],
        year: int,
        quarter: int,
    ) -> tuple[dict[str, FinancialFact], list[dict[str, str]]]:
        identities = list(identities)
        codes = [row.corp_code for row in identities]
        current = _group(self.dart.multi_accounts(codes, year, quarter), codes)
        previous = _group(self.dart.multi_accounts(codes, year, quarter - 1), codes) if quarter > 1 else {code: [] for code in codes}
        facts: dict[str, FinancialFact] = {}
        issues: list[dict[str, str]] = []
        end = quarter_end(year, quarter)

        for identity in identities:
            fact = extract_company_fact(
                identity.corp_code, identity.company_id, year, quarter,
                current[identity.corp_code], previous[identity.corp_code],
            )
            if fact is None:
                issues.append({"company": identity.company_name, "field": "all", "reason": "OpenDART rows missing"})
                continue

            if fact.currency != "KRW":
                if fact.currency != "USD" or self.fx is None:
                    issues.append({"company": identity.company_name, "field": "currency", "reason": f"unsupported {fact.currency}"})
                    continue
                rate = self.fx.usd_krw(end)
                fact = fact.with_changes(
                    top_line=fact.top_line * rate if fact.top_line is not None else None,
                    operating_income=fact.operating_income * rate if fact.operating_income is not None else None,
                    net_income=fact.net_income * rate if fact.net_income is not None else None,
                    currency="KRW",
                )

            # KIS는 OpenDART에서 영업이익과 순이익을 확보했지만 매출만 없는
            # 2019년 이후 기업에만 호출한다. 다른 필드를 다시 가져오지 않는다.
            if fact.top_line is None and fact.profit_complete and year >= 2019 and self.kis is not None:
                fact = fact.with_changes(top_line=self.kis.quarter_top_line(identity.stock_code, year, quarter))
            for field in ("top_line", "operating_income", "net_income"):
                if getattr(fact, field) is None:
                    issues.append({"company": identity.company_name, "field": field, "reason": "provider value missing"})
            facts[identity.company_id] = fact
        return facts, issues

    def _calculated_histories(self, facts: dict[str, FinancialFact]) -> dict[str, list[FinancialFact]]:
        histories: dict[str, list[FinancialFact]] = defaultdict(list)
        for record in self.repository.company_history(facts):
            if int(record.get("calculation_version") or 0) < CALCULATION_VERSION:
                continue
            row = _financial_from_db(record)
            histories[row.company_id].append(row)
        for company_id, fact in facts.items():
            histories[company_id] = [row for row in histories[company_id] if row.key != fact.key] + [fact]
        return {company_id: calculate_financial_series(rows) for company_id, rows in histories.items()}

    def _market_rows(
        self,
        universes: dict[str, list[CompanyIdentity]],
        histories: dict[str, list[FinancialFact]],
        year: int,
        quarter: int,
    ) -> dict[str, list[MarketFact]]:
        output: dict[str, list[MarketFact]] = {}
        for market_id, identities in universes.items():
            current = [
                row for identity in identities for row in histories.get(identity.company_id, [])
                if row.key == (year, quarter)
            ]
            market = aggregate_market(market_id, year, quarter, current, TARGETS[market_id])
            existing: dict[tuple[int, int], MarketFact] = {}
            for record in self.repository.market_history(market_id):
                if int(record.get("calculation_version") or 0) < CALCULATION_VERSION:
                    continue
                stored = _market_from_db(record)
                existing[stored.key] = stored
            existing[market.key] = market
            output[market_id] = calculate_market_series(existing.values())
        return output

    def run_quarter(
        self,
        year: int,
        quarter: int,
        *,
        write: bool = False,
        allow_review: bool = False,
    ) -> dict[str, Any]:
        operation = f"{year}Q{quarter}"
        if write:
            self.repository.save_state(operation, "running", {})
        try:
            corp_map = self.dart.corporation_map()
            universes = {
                market_id: self.discover_universe(market_id, year, quarter, corp_map)
                for market_id in TARGETS
            }
            identities = list({row.company_id: row for rows in universes.values() for row in rows}.values())
            facts, issues = self.collect_financials(identities, year, quarter)
            histories = self._calculated_histories(facts)
            markets = self._market_rows(universes, histories, year, quarter)
            summary = {
                "period": operation,
                "write": write,
                "universe": {market: len(rows) for market, rows in universes.items()},
                "companies": len(identities),
                "facts": len(facts),
                "complete_facts": sum(row.fully_complete for row in facts.values()),
                "issues": issues,
                "requests": {
                    "krx": self.krx.request_count,
                    "open_dart": self.dart.request_count,
                    "kis": self.kis.request_count if self.kis else 0,
                    "ecos": self.fx.request_count if self.fx else 0,
                },
            }
            if issues and not allow_review:
                if write:
                    self.repository.save_state(operation, "incomplete", summary)
                return {**summary, "status": "incomplete"}

            if write:
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
                self.repository.upsert_company_quarters(
                    row.db_row(calculation_version=CALCULATION_VERSION)
                    for rows in histories.values() for row in rows
                )
                self.repository.upsert_market_quarters(
                    row.db_row(calculation_version=CALCULATION_VERSION)
                    for rows in markets.values() for row in rows
                )
                self.repository.save_state(operation, "ready" if not issues else "incomplete", summary)
            return {**summary, "status": "ready" if not issues else "incomplete"}
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
