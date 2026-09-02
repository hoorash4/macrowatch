from __future__ import annotations

import json
import os
import re
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from .aggregation import aggregate_market, calculate_market_point
from .models import CompanyIdentity, FinancialFact, MarketFact, PeriodicFiling, Security
from .providers import EcosFxClient, KisClient, KrxClient, OpenDartClient, ProviderError
from .repository import EarningsV2Repository
from .transform import (
    calculate_financial_point,
    decimal_value,
    extract_company_fact,
    update_seasonal_window,
)


TARGETS = {"kr_largecap": 100, "kr_kosdaq": 50}
EXCHANGES = {"kr_largecap": "KOSPI", "kr_kosdaq": "KOSDAQ"}
# V6부터 부분 기업행을 보존하고 잠정 바구니와 확정 총합을 분리한다.
CALCULATION_VERSION = 6


def quarter_end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, 31 if quarter in {1, 4} else 30)


def previous_period(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def _seasonal_window_index(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str, int], tuple[list[int], list[Decimal]]]:
    windows: dict[tuple[str, str, int], tuple[list[int], list[Decimal]]] = {}
    for row in rows:
        pairs = [
            (int(year), parsed)
            for year, value in zip(row.get("sample_years") or [], row.get("sample_values") or [])
            if (parsed := decimal_value(value)) is not None
        ]
        windows[(str(row["entity_id"]), str(row["metric"]), int(row["fiscal_quarter"]))] = (
            [year for year, _ in pairs],
            [value for _, value in pairs],
        )
    return windows


def _window_samples(
    windows: dict[tuple[str, str, int], tuple[list[int], list[Decimal]]],
    entity_id: str,
    metric: str,
    quarter: int,
    before_year: int,
) -> list[Decimal]:
    years, values = windows.get((entity_id, metric, quarter), ([], []))
    return [value for year, value in zip(years, values) if year < before_year]


def _advance_window(
    windows: dict[tuple[str, str, int], tuple[list[int], list[Decimal]]],
    *,
    entity_type: str,
    entity_id: str,
    metric: str,
    year: int,
    quarter: int,
    value: Decimal | None,
) -> dict[str, Any] | None:
    key = (entity_id, metric, quarter)
    if key not in windows and value is None:
        return None
    years, values = windows.get(key, ([], []))
    updated_years, updated_values = update_seasonal_window(years, values, year=year, value=value)
    windows[key] = (updated_years, updated_values)
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metric": metric,
        "fiscal_quarter": quarter,
        "sample_years": updated_years,
        "sample_values": updated_values,
    }


def latest_completed_quarter(today: date) -> tuple[int, int]:
    current_quarter = (today.month - 1) // 3 + 1
    return previous_period(today.year, current_quarter)


def filing_period(filing: PeriodicFiling) -> tuple[int, int] | None:
    """정기보고서명 끝의 기준월을 회계 분기로 변환한다."""
    match = re.search(r"\((\d{4})\.(03|06|09|12)\)", filing.report_name)
    if match is None:
        return None
    return int(match.group(1)), {"03": 1, "06": 2, "09": 3, "12": 4}[match.group(2)]


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
        source=str(row.get("source") or "open_dart"),
        source_currency=str(row.get("source_currency") or row.get("currency") or "KRW"),
        source_top_line_cumulative=decimal_value(row.get("source_top_line_cumulative")),
        source_operating_income_cumulative=decimal_value(row.get("source_operating_income_cumulative")),
        source_net_income_cumulative=decimal_value(row.get("source_net_income_cumulative")),
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
        reference_date, candidates = self._market_cap_candidates(market_id, year, quarter, corp_map)
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

    def _market_cap_candidates(self, market_id: str, year: int, quarter: int,
                               corp_map: dict[str, tuple[str, str]]) -> tuple[date, list[tuple[str, tuple[Security, str]]]]:
        """한 번 정규화한 시총 후보군을 운영 기업군과 진단에서 함께 쓴다."""
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
        return reference_date, sorted(
            by_company.items(),
            key=lambda item: (-item[1][0].market_cap, item[1][0].stock_code),
        )

    def diagnose_kosdaq_slice(self, year: int, quarter: int, start_rank: int, end_rank: int) -> dict[str, Any]:
        """DB 저장 없이 코스닥 시총 구간의 누적 손익 부호만 점검한다."""
        if start_rank < 1 or end_rank < start_rank or end_rank - start_rank + 1 > 100:
            raise ValueError("diagnostic rank range must contain 1 to 100 companies")
        corp_map = self.dart.corporation_map()
        reference_date, candidates = self._market_cap_candidates("kr_kosdaq", year, quarter, corp_map)
        if len(candidates) < end_rank:
            raise RuntimeError(f"kr_kosdaq candidates are {len(candidates)}/{end_rank}")
        selected = candidates[start_rank - 1:end_rank]
        identities = [CompanyIdentity(
            company_id=f"kr:{corp_code}", company_name=official_name or security.name,
            stock_code=security.stock_code, corp_code=corp_code, market_id="kr_kosdaq",
            rank=rank, market_cap=security.market_cap, reference_date=reference_date,
        ) for rank, (corp_code, (security, official_name)) in enumerate(selected, start_rank)]
        codes = [row.corp_code for row in identities]
        grouped = _group(self.dart.multi_accounts(codes, year, quarter), codes)
        rows = []
        for identity in identities:
            fact = extract_company_fact(
                identity.corp_code, identity.company_id, year, quarter,
                grouped[identity.corp_code],
            )
            rows.append({
                "rank": identity.rank,
                "company": identity.company_name,
                "operating_income_cumulative": fact.source_operating_income_cumulative if fact else None,
                "net_income_cumulative": fact.source_net_income_cumulative if fact else None,
            })
        return {
            "period": f"{year}Q{quarter}",
            "reference_date": reference_date,
            "rank_range": [start_rank, end_rank],
            "companies": len(rows),
            "operating_loss_companies": sum(
                row["operating_income_cumulative"] is not None and row["operating_income_cumulative"] < 0
                for row in rows
            ),
            "net_loss_companies": sum(
                row["net_income_cumulative"] is not None and row["net_income_cumulative"] < 0
                for row in rows
            ),
            "missing_operating_income": sum(row["operating_income_cumulative"] is None for row in rows),
            "missing_net_income": sum(row["net_income_cumulative"] is None for row in rows),
            "loss_companies": [row for row in rows if (
                row["operating_income_cumulative"] is not None and row["operating_income_cumulative"] < 0
            ) or (
                row["net_income_cumulative"] is not None and row["net_income_cumulative"] < 0
            )],
            "database_written": False,
        }

    def _try_kis_top_line(
        self,
        identity: CompanyIdentity,
        fact: FinancialFact,
        year: int,
        quarter: int,
        *,
        stage: str,
    ) -> tuple[FinancialFact, dict[str, str] | None]:
        """KIS 보완 실패를 기업 한 건의 대기 상태로 격리한다."""
        self._progress(f"{stage}_start", company=identity.company_name, ticker=identity.stock_code)
        try:
            top_line = self.kis.quarter_top_line(identity.stock_code, year, quarter)
        except ProviderError as error:
            self._progress(f"{stage}_failed", company=identity.company_name)
            return fact.with_changes(is_pending=True), {
                "company": identity.company_name,
                "field": "top_line",
                "reason": str(error),
            }
        self._progress(f"{stage}_done", company=identity.company_name, found=top_line is not None)
        return fact.with_changes(top_line=top_line, is_pending=top_line is None), None

    def collect_financials(self, identities: Iterable[CompanyIdentity], year: int, quarter: int,
                           previous_facts: dict[str, FinancialFact] | None = None,
                           ) -> tuple[dict[str, FinancialFact], list[dict[str, str]]]:
        identities = list(identities)
        codes = [row.corp_code for row in identities]
        current = _group(self.dart.multi_accounts(codes, year, quarter), codes)
        previous_facts = previous_facts or {}
        # 직전 분기의 누적 원본은 DB가 단일 진실 공급원이다. 과거 기업군에
        # 없었던 기업 등 실제 단독값 계산에 필요한 원본이 없는 경우에만
        # 해당 기업만 폴백한다. 현재 누적값 자체가 없는 필드는 재호출해도
        # 해결되지 않으므로 폴백 대상에서 제외한다.
        fallback_codes = []
        if quarter > 1:
            for identity in identities:
                prior = previous_facts.get(identity.company_id)
                preview = extract_company_fact(
                    identity.corp_code, identity.company_id, year, quarter,
                    current[identity.corp_code], previous_fact=prior,
                )
                needs_previous = preview is not None and any(
                    source_value is not None and standalone_value is None
                    for source_value, standalone_value in (
                        (preview.source_top_line_cumulative, preview.top_line),
                        (preview.source_operating_income_cumulative, preview.operating_income),
                        (preview.source_net_income_cumulative, preview.net_income),
                    )
                )
                if needs_previous:
                    fallback_codes.append(identity.corp_code)
        previous = (
            _group(self.dart.multi_accounts(fallback_codes, year, quarter - 1), fallback_codes)
            if fallback_codes else {}
        )
        facts: dict[str, FinancialFact] = {}
        issues: list[dict[str, str]] = []
        end = quarter_end(year, quarter)
        for identity in identities:
            fact = extract_company_fact(identity.corp_code, identity.company_id, year, quarter,
                                        current[identity.corp_code], previous.get(identity.corp_code, []),
                                        previous_fact=previous_facts.get(identity.company_id))
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
                fact, kis_issue = self._try_kis_top_line(
                    identity, fact, year, quarter, stage="kis_top_line",
                )
                if kis_issue is not None:
                    issues.append(kis_issue)
            fact = fact.with_changes(is_pending=fact.is_pending or not fact.fully_complete)
            existing_issues = {(item["company"], item["field"]) for item in issues}
            for field in ("top_line", "operating_income", "net_income"):
                if getattr(fact, field) is None and (identity.company_name, field) not in existing_issues:
                    issues.append({"company": identity.company_name, "field": field, "reason": "provider value missing"})
            facts[identity.company_id] = fact
        return facts, issues

    @staticmethod
    def _stored_facts(
        records: Iterable[dict[str, Any]],
        current_key: tuple[int, int],
    ) -> tuple[dict[tuple[str, int, int], FinancialFact], set[str]]:
        facts: dict[tuple[str, int, int], FinancialFact] = {}
        manual_ids: set[str] = set()
        for record in records:
            if int(record.get("calculation_version") or 0) < CALCULATION_VERSION:
                continue
            fact = _financial_from_db(record)
            facts[(fact.company_id, *fact.key)] = fact
            if fact.key == current_key and str(record.get("source") or "") == "manual":
                manual_ids.add(fact.company_id)
        return facts, manual_ids

    def _calculate_company_points(
        self,
        current: dict[str, FinancialFact],
        references: dict[tuple[str, int, int], FinancialFact],
        changed_ids: set[str],
        year: int,
        quarter: int,
    ) -> tuple[dict[str, FinancialFact], list[dict[str, Any]]]:
        windows = _seasonal_window_index(self.repository.seasonal_windows("company", changed_ids))
        previous_key = previous_period(year, quarter)
        calculated = dict(current)
        window_rows: list[dict[str, Any]] = []
        for company_id in changed_ids:
            row = current.get(company_id)
            if row is None:
                continue
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
            for record in self.repository.market_periods(TARGETS, [previous_key, (year - 1, quarter)])
            if int(record.get("calculation_version") or 0) >= CALCULATION_VERSION
            for row in [_market_from_db(record)]
        }
        windows = _seasonal_window_index(self.repository.seasonal_windows("market", TARGETS))
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
                {member.company_id: current_facts.get(member.company_id) for member in members},
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
                    allow_review: bool = False, incremental: bool = False,
                    refresh_corp_codes: set[str] | None = None) -> dict[str, Any]:
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
            # 백필은 현재 분기 기존값을 참조하지 않는다. 증분 수집만 현재값과 수동 확정을 읽는다.
            reference_periods = [previous_key, (year - 1, quarter)]
            if incremental:
                reference_periods.insert(0, (year, quarter))
            stored_rows = self.repository.company_periods(history_ids, reference_periods)
            stored, manual_ids = self._stored_facts(stored_rows, (year, quarter))
            if incremental:
                stored_current = {
                    identity.company_id: stored[(identity.company_id, year, quarter)]
                    for identity in identities
                    if (identity.company_id, year, quarter) in stored
                }
                provider_refresh = refresh_corp_codes or set()
                selected = [
                    row for row in identities
                    if row.company_id not in manual_ids and (
                        row.corp_code in provider_refresh
                        or row.company_id not in stored_current
                    )
                ]
                preserved = stored_current
            else:
                # 명시적 백필은 자동/수동 여부와 관계없이 공급자 원자료로 전부 교체한다.
                stored_current = {}
                selected = identities
                preserved = {}
            previous_facts = {
                identity.company_id: stored[(identity.company_id, *previous_key)]
                for identity in selected
                if (identity.company_id, *previous_key) in stored
            }
            fresh_facts, issues = (
                self.collect_financials(selected, year, quarter, previous_facts)
                if selected else ({}, [])
            )
            pending_top_lines: dict[str, FinancialFact] = {}
            if incremental and self.kis is not None and year >= 2019:
                selected_ids = {row.company_id for row in selected}
                for identity in identities:
                    fact = stored_current.get(identity.company_id)
                    if (
                        identity.company_id in manual_ids
                        or identity.company_id in selected_ids
                        or fact is None
                        or not fact.is_pending
                        or fact.top_line is not None
                        or not fact.profit_complete
                    ):
                        continue
                    retried, kis_issue = self._try_kis_top_line(
                        identity, fact, year, quarter, stage="kis_top_line_retry",
                    )
                    pending_top_lines[identity.company_id] = retried
                    if kis_issue is not None:
                        issues.append(kis_issue)
                    elif retried.top_line is None:
                        issues.append({"company": identity.company_name, "field": "top_line", "reason": "provider value missing"})
            facts = {**preserved, **fresh_facts, **pending_top_lines}
            changed_ids = set(fresh_facts) | set(pending_top_lines)
            current_by_company, company_window_rows = self._calculate_company_points(
                facts, stored, changed_ids, year, quarter,
            )
            current_markets, market_window_rows = self._market_rows(
                universes, previous_universes, current_by_company, stored, year, quarter,
            )
            current_facts = list(current_by_company.values())
            if write:
                company_rows = [
                    row.db_row(calculation_version=CALCULATION_VERSION)
                    for row in current_facts if row.company_id in changed_ids
                ]
                if incremental:
                    self.repository.upsert_company_quarters(company_rows)
                else:
                    self.repository.replace_company_quarters_for_backfill(company_rows)
                if company_window_rows:
                    self.repository.upsert_seasonal_windows(company_window_rows)
                self.repository.upsert_market_quarters(row.db_row(calculation_version=CALCULATION_VERSION) for row in current_markets)
                if market_window_rows:
                    self.repository.upsert_seasonal_windows(market_window_rows)
            ready = not issues and all(row.completion_status == "complete" for row in current_markets)
            status = "ready" if ready else "incomplete"
            summary = {
                "period": operation, "write": write,
                "universe": {market: len(rows) for market, rows in universes.items()},
                "companies": len(identities), "facts": len(current_facts),
                "refreshed_companies": len(fresh_facts),
                "retried_pending_top_lines": len(pending_top_lines),
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

    def recalculate_quarter(self, year: int, quarter: int, *, write: bool = True) -> dict[str, Any]:
        """저장된 기업 실적만으로 시장 합계와 파생값을 다시 계산한다.

        관리자 수동 확정 뒤에는 외부 공급자를 다시 호출할 이유가 없다.
        이 경로는 확정된 기업군과 DB 실적만 읽고 시장 분기 행만 갱신한다.
        """
        universes: dict[str, list[CompanyIdentity]] = {}
        for market_id, target in TARGETS.items():
            frozen = self.repository.universe(market_id, year, quarter)
            if len(frozen) != target:
                raise ValueError(f"{market_id} universe is {len(frozen)}/{target}")
            universes[market_id] = [_identity_from_universe(row) for row in frozen]

        identities = list({row.company_id: row for rows in universes.values() for row in rows}.values())
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
        stored, _manual_ids = self._stored_facts(stored_rows, (year, quarter))
        stored_current = {
            identity.company_id: stored[(identity.company_id, year, quarter)]
            for identity in identities
            if (identity.company_id, year, quarter) in stored
        }
        current_by_company, company_window_rows = self._calculate_company_points(
            stored_current, stored, set(stored_current), year, quarter,
        )
        current_markets, market_window_rows = self._market_rows(
            universes, previous_universes, current_by_company, stored, year, quarter,
        )
        if write:
            self.repository.upsert_company_quarters(
                row.db_row(calculation_version=CALCULATION_VERSION)
                for row in current_by_company.values()
            )
            if company_window_rows:
                self.repository.upsert_seasonal_windows(company_window_rows)
            self.repository.upsert_market_quarters(
                row.db_row(calculation_version=CALCULATION_VERSION) for row in current_markets
            )
            if market_window_rows:
                self.repository.upsert_seasonal_windows(market_window_rows)
        return {
            "period": f"{year}Q{quarter}",
            "write": write,
            "mode": "stored_recalculation",
            "companies": len(identities),
            "markets": {row.market_id: row.completion_status for row in current_markets},
            "status": "ready",
        }

    def run_daily(self, *, write: bool = True, today: date | None = None) -> dict[str, Any]:
        """마지막 정상 확인일 이후의 신규 접수번호만 증분 처리한다."""
        current_day = today or date.today()
        state = self.repository.pipeline_state("daily_filings") or {}
        cursor = state.get("cursor") if isinstance(state.get("cursor"), dict) else {}
        checked_text = str(cursor.get("last_checked_date") or "")
        try:
            checked_on = date.fromisoformat(checked_text)
        except ValueError:
            checked_on = current_day
        if checked_on > current_day:
            checked_on = current_day

        boundary_receipts = {
            str(value) for value in cursor.get("boundary_receipt_ids", [])
            if re.fullmatch(r"\d{14}", str(value))
        }
        filings = self.dart.periodic_filings(checked_on, current_day)
        new_filings = [
            filing for filing in filings
            if not (filing.received_on == checked_on and filing.receipt_no in boundary_receipts)
        ]
        year, quarter = latest_completed_quarter(current_day)
        refresh_corp_codes = {
            filing.corp_code for filing in new_filings if filing_period(filing) == (year, quarter)
        }
        result = self.run_quarter(
            year, quarter, write=write, allow_review=True, incremental=True,
            refresh_corp_codes=refresh_corp_codes,
        )
        result["filing_discovery"] = {
            "checked_from": checked_on.isoformat(),
            "checked_through": current_day.isoformat(),
            "new_receipts": len(new_filings),
            "refreshed_companies": len(refresh_corp_codes),
        }
        if write:
            self.repository.save_state("daily_filings", "ready", {
                "last_checked_date": current_day.isoformat(),
                "boundary_receipt_ids": sorted(
                    filing.receipt_no for filing in filings if filing.received_on == current_day
                ),
            })
        return result

    def run_year(self, year: int, *, write: bool = False, allow_review: bool = False) -> list[dict[str, Any]]:
        """백필은 참조 분기가 먼저 존재하도록 항상 1분기부터 순서대로 실행한다."""
        results = []
        for quarter in range(1, 5):
            result = self.run_quarter(year, quarter, write=write, allow_review=allow_review)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
            if result["status"] != "ready" and not allow_review:
                break
        return results
