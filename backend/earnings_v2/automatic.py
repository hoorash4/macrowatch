from __future__ import annotations

import json
import os
import re
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from .aggregation import aggregate_market, calculate_market_point
from .models import (
    CompanyIdentity,
    DelistingFiling,
    FinancialFact,
    MarketFact,
    PeriodicFiling,
    QuarterFxRate,
    Security,
)
from .providers import EcosFxClient, KisClient, KrxClient, OpenDartClient, ProviderError
from .repository import EarningsV2Repository
from .runtime import execution_deadline
from .transform import (
    calculate_financial_point,
    decimal_value,
    extract_company_fact,
    update_seasonal_window,
)


TARGETS = {"kr_largecap": 100, "kr_kosdaq": 100}
EXCHANGES = {"kr_largecap": "KOSPI", "kr_kosdaq": "KOSDAQ"}
# V6부터 부분 기업행을 보존하고 잠정 바구니와 확정 총합을 분리한다.
CALCULATION_VERSION = 6


def quarter_end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, 31 if quarter in {1, 4} else 30)


def quarter_start(year: int, quarter: int) -> date:
    return date(year, (quarter - 1) * 3 + 1, 1)


def quarter_resolution_end(year: int, quarter: int) -> date:
    """해당 분기 실적이 통상 확정되는 시점까지 최종 상폐공시를 찾는다."""
    if quarter == 1:
        return date(year, 5, 15)
    if quarter == 2:
        return date(year, 8, 14)
    if quarter == 3:
        return date(year, 11, 14)
    return date(year + 1, 3, 31)


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
        industry_code=str(row.get("industry_code") or "").strip() or None,
        entity_kind=str(row.get("entity_kind") or "").strip() or None,
    )


class KoreaEarningsV2AutomaticPipeline:
    """백필 추정과 전체 교체를 허용하지 않는 운영 자동수집 전용 파이프라인."""

    def __init__(self, *, krx: KrxClient, dart: OpenDartClient, repository: EarningsV2Repository,
                 kis: KisClient | None = None, fx: EcosFxClient | None = None) -> None:
        self.krx, self.dart, self.repository, self.kis, self.fx = krx, dart, repository, kis, fx

    @staticmethod
    def _progress(stage: str, **details: Any) -> None:
        print(json.dumps({"stage": stage, **details}, ensure_ascii=False, default=str), flush=True)

    @classmethod
    def from_env(cls) -> "KoreaEarningsV2AutomaticPipeline":
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

    def _try_kis_missing_financials(
        self,
        identity: CompanyIdentity,
        fact: FinancialFact,
        year: int,
        quarter: int,
        *,
        stage: str,
        propagate_provider_error: bool = False,
        previous_fact: FinancialFact | None = None,
    ) -> tuple[FinancialFact, dict[str, str] | None]:
        """KIS로 OpenDART 누락 손익만 보완하고, 실패는 기업 단위 대기로 격리한다."""
        self._progress(f"{stage}_start", company=identity.company_name, ticker=identity.stock_code)
        try:
            values = self.kis.quarter_cumulative_financials(
                identity.stock_code, year, quarter,
            )
        except ProviderError as error:
            self._progress(f"{stage}_failed", company=identity.company_name)
            if propagate_provider_error:
                raise
            return fact.with_changes(is_pending=True), {
                "company": identity.company_name,
                "field": "top_line",
                "reason": str(error),
            }
        changes: dict[str, Decimal] = {}
        for field, cumulative in values.items():
            if getattr(fact, field) is not None:
                continue
            current = cumulative.get("current")
            if current is None:
                continue
            if quarter == 1:
                standalone = current
            else:
                # 자동 수집은 확정된 DB 직전 누적만 신뢰한다. 없으면 추정하거나
                # 과거 KIS를 다시 호출하지 않고 null/관리자 검토로 남긴다.
                previous = (
                    getattr(previous_fact, f"source_{field}_cumulative")
                    if previous_fact is not None
                    and previous_fact.source_currency == "KRW"
                    else None
                )
                if previous is None:
                    continue
                standalone = current - previous
            changes[field] = standalone
            changes[f"source_{field}_cumulative"] = current
        updated = fact.with_changes(**changes)
        updated = updated.with_changes(is_pending=not updated.fully_complete)
        self._progress(
            f"{stage}_done", company=identity.company_name,
            filled=sorted(
                field for field in ("top_line", "operating_income", "net_income")
                if field in changes
            ), complete=updated.fully_complete,
        )
        return updated, None

    @staticmethod
    def _delisting_event_map(
        events: Iterable[DelistingFiling],
    ) -> dict[str, DelistingFiling]:
        """기업별 최초 결정공시를 우선하고, 없으면 최초 최종공시를 쓴다."""
        grouped: dict[str, list[DelistingFiling]] = {}
        for event in events:
            grouped.setdefault(event.corp_code, []).append(event)
        result: dict[str, DelistingFiling] = {}
        for corp_code, candidates in grouped.items():
            ordered = sorted(candidates, key=lambda row: (row.event_on, row.receipt_no))
            result[corp_code] = next(
                (row for row in ordered if row.event_type == "decision"), ordered[0],
            )
        return result

    def _stored_delisting_events(
        self,
        identities: Iterable[CompanyIdentity],
        year: int,
        quarter: int,
        supplied: Iterable[DelistingFiling] = (),
        effective_cutoff: date | None = None,
    ) -> dict[str, DelistingFiling]:
        identities = list(identities)
        start = quarter_start(year, quarter)
        quarter_last_day = quarter_end(year, quarter)
        end = quarter_resolution_end(year, quarter)
        rows = self.repository.delisting_events(
            (row.corp_code for row in identities), start, end,
        )
        events = [
            DelistingFiling(
                corp_code=str(row["corp_code"]),
                receipt_no=str(row["receipt_no"]),
                received_on=date.fromisoformat(str(row["received_on"])),
                report_name=str(row["report_name"]),
                event_type=str(row["event_type"]),
                effective_on=(
                    date.fromisoformat(str(row["effective_on"]))
                    if row.get("effective_on") else None
                ),
            )
            for row in rows
        ]
        events.extend(event for event in supplied if start <= event.event_on <= end)
        events = [
            event for event in events
            if (
                (effective_cutoff is None or event.event_on <= effective_cutoff)
                and (
                    event.event_type in {"final", "absorbed_merger"}
                    or event.event_on <= quarter_last_day
                )
            )
        ]
        return self._delisting_event_map(events)

    def _discover_delisting_events(
        self,
        identities: Iterable[CompanyIdentity],
        year: int,
        quarter: int,
        *,
        write: bool,
    ) -> list[DelistingFiling]:
        """과거 재처리는 미완결 기업만 회사별 거래소공시를 확인한다."""
        start = quarter_start(year, quarter)
        quarter_last_day = quarter_end(year, quarter)
        end = quarter_resolution_end(year, quarter)
        events: dict[str, DelistingFiling] = {}
        for identity in identities:
            for event in self.dart.delisting_filings(
                start, end, corp_code=identity.corp_code,
            ):
                if event.event_type == "final" or event.event_on <= quarter_last_day:
                    events[event.receipt_no] = event
            merger_loader = getattr(self.dart, "absorbed_merger_filings", None)
            if callable(merger_loader):
                for event in merger_loader(
                    date(year - 1, 1, 1), end, corp_code=identity.corp_code,
                ):
                    if start <= event.event_on <= end:
                        events[event.receipt_no] = event
        ordered = sorted(events.values(), key=lambda row: (row.event_on, row.receipt_no))
        if write and ordered:
            self.repository.upsert_delisting_events(event.db_row() for event in ordered)
        return ordered

    @staticmethod
    def _delisting_fact(
        identity: CompanyIdentity,
        previous: FinancialFact,
        event: DelistingFiling,
        year: int,
        quarter: int,
    ) -> FinancialFact:
        cumulative: dict[str, Decimal | None] = {}
        for field in ("top_line", "operating_income", "net_income"):
            value = getattr(previous, field)
            if quarter == 1:
                cumulative[field] = value
            else:
                prior_cumulative = getattr(previous, f"source_{field}_cumulative")
                cumulative[field] = (
                    prior_cumulative + value
                    if prior_cumulative is not None and value is not None else None
                )
        return FinancialFact(
            company_id=identity.company_id,
            fiscal_year=year,
            fiscal_quarter=quarter,
            period_end=quarter_end(year, quarter),
            top_line=previous.top_line,
            operating_income=previous.operating_income,
            net_income=previous.net_income,
            currency=previous.currency,
            consolidation_scope=previous.consolidation_scope,
            source_filing_id=f"delisting_previous_quarter:{event.receipt_no}",
            filing_date=event.received_on,
            source="open_dart",
            source_currency=previous.source_currency,
            source_top_line_cumulative=cumulative["top_line"],
            source_operating_income_cumulative=cumulative["operating_income"],
            source_net_income_cumulative=cumulative["net_income"],
            is_pending=False,
        )

    def _previous_fact_for_delisting(
        self,
        identity: CompanyIdentity,
        year: int,
        quarter: int,
        stored: dict[tuple[str, int, int], FinancialFact],
        *,
        usd_krw_rate: Decimal,
        write: bool,
    ) -> FinancialFact | None:
        previous_key = previous_period(year, quarter)
        previous = stored.get((identity.company_id, *previous_key))
        if previous is not None and previous.fully_complete and not previous.is_pending:
            return previous
        # 직전 분기에 상위 100개가 아니었던 기업도 있으므로, 상폐 처리에
        # 필요한 정확한 직전 분기 한 건만 공급자에서 새로 구한다.
        fetched, _issues = self.collect_financials(
            [identity], previous_key[0], previous_key[1], {},
            usd_krw_rate=usd_krw_rate,
            tolerate_provider_errors=True,
            force_previous_cumulative=previous_key[1] > 1,
            persist_profiles=write,
        )
        candidate = fetched.get(identity.company_id)
        return (
            candidate
            if candidate is not None and candidate.fully_complete and not candidate.is_pending
            else None
        )

    @staticmethod
    def _entity_kind(industry_code: str | None) -> str | None:
        digits = re.sub(r"\D", "", industry_code or "")
        if not digits:
            return None
        return "financial" if digits[:2] in {"64", "65", "66"} else "general"

    def _single_open_dart_missing_financials(
        self,
        identity: CompanyIdentity,
        fact: FinancialFact,
        year: int,
        quarter: int,
        previous_fact: FinancialFact | None,
        previous_rows: list[dict[str, Any]],
    ) -> FinancialFact:
        existing_count = sum(
            getattr(fact, field) is not None
            for field in ("top_line", "operating_income", "net_income")
        )
        scopes = [fact.consolidation_scope] if existing_count else ["CFS", "OFS"]
        rows: list[dict[str, Any]] = []
        prior_rows = list(previous_rows)
        best: FinancialFact | None = None
        for scope in scopes:
            rows.extend(self.dart.single_accounts(identity.corp_code, year, quarter, scope))
            candidate = extract_company_fact(
                identity.corp_code, identity.company_id, year, quarter,
                rows, prior_rows, previous_fact=previous_fact,
                consolidation_scope=fact.consolidation_scope if existing_count else None,
                allow_annual_average=False,
            )
            if candidate is not None:
                best = candidate
            # 연결 손익계산서가 존재하면 완결 여부와 무관하게 연결을 쓴다.
            # 빠진 항목은 별도재무제표와 섞지 않고 KIS 경로에서 보완한다.
            if best is not None:
                break
        if best is None or (existing_count and best.consolidation_scope != fact.consolidation_scope):
            return fact

        changes: dict[str, Any] = {}
        for field in ("top_line", "operating_income", "net_income"):
            if getattr(fact, field) is None and getattr(best, field) is not None:
                changes[field] = getattr(best, field)
                changes[f"source_{field}_cumulative"] = getattr(best, f"source_{field}_cumulative")
        if not existing_count and changes:
            changes.update({
                "consolidation_scope": best.consolidation_scope,
                "source_filing_id": best.source_filing_id,
                "filing_date": best.filing_date,
                "currency": best.currency,
                "source_currency": best.source_currency,
            })
        return fact.with_changes(**changes) if changes else fact

    def _resolve_missing_financials(
        self,
        identity: CompanyIdentity,
        fact: FinancialFact,
        year: int,
        quarter: int,
        *,
        previous_fact: FinancialFact | None = None,
        previous_rows: list[dict[str, Any]] | None = None,
        tolerate_provider_errors: bool = False,
        profile_updates: dict[str, dict[str, str]] | None = None,
        stage: str = "financial_fallback",
        allow_backfill_zero_top_line: bool = False,
        use_kis: bool = True,
    ) -> tuple[FinancialFact, dict[str, str] | None]:
        if fact.fully_complete:
            return fact.with_changes(is_pending=False), None

        industry_code = identity.industry_code
        entity_kind = self._entity_kind(industry_code)
        if entity_kind is None:
            try:
                profile = self.dart.company_profile(identity.corp_code)
            except ProviderError:
                profile = None
            industry_code = str((profile or {}).get("industry_code") or "").strip() or None
            entity_kind = self._entity_kind(industry_code)
            if industry_code and entity_kind and profile_updates is not None:
                profile_updates[identity.company_id] = {
                    "company_id": identity.company_id,
                    "industry_code": industry_code,
                    "entity_kind": entity_kind,
                }

        # 업종과 관계없이 전체계정 단일 조회로 먼저 누락값을 보완한다.
        # 그래도 완결되지 않은 기업만 KIS로 넘겨 불필요한 보완 호출을 줄인다.
        self._progress(f"{stage}_open_dart_start", company=identity.company_name)
        try:
            fact = self._single_open_dart_missing_financials(
                identity, fact, year, quarter, previous_fact, previous_rows or [],
            )
        except ProviderError:
            if not tolerate_provider_errors:
                raise
        self._progress(
            f"{stage}_open_dart_done", company=identity.company_name,
            complete=fact.fully_complete,
        )

        if use_kis and not fact.fully_complete and self.kis is not None:
            fact, kis_issue = self._try_kis_missing_financials(
                identity, fact, year, quarter, stage=f"{stage}_kis",
                propagate_provider_error=not tolerate_provider_errors,
                previous_fact=previous_fact,
            )
            if kis_issue is not None:
                return fact, kis_issue
        # 이 추정은 사용자가 허용한 과거 백필 전용 규칙이다. 자동 수집은
        # 원자료에 탑라인이 없으면 null/incomplete를 유지해 관리자 검토로 보낸다.
        if (
            allow_backfill_zero_top_line
            and
            fact.top_line is None
            and fact.profit_complete
            and entity_kind == "general"
            and fact.operating_income is not None
            and fact.operating_income < 0
        ):
            previous_top = (
                previous_fact.source_top_line_cumulative
                if previous_fact is not None else None
            )
            fact = fact.with_changes(
                top_line=Decimal(0),
                source_top_line_cumulative=(
                    Decimal(0) if quarter == 1 or previous_top is None else previous_top
                ),
                source_filing_id=f"zero_top_line:{fact.source_filing_id}",
            )
        return fact.with_changes(is_pending=not fact.fully_complete), None

    def collect_financials(self, identities: Iterable[CompanyIdentity], year: int, quarter: int,
                           previous_facts: dict[str, FinancialFact] | None = None,
                           *, usd_krw_rate: Decimal | None = None,
                           tolerate_provider_errors: bool = False,
                           force_previous_cumulative: bool = False,
                            persist_profiles: bool = False,
                            allow_backfill_zero_top_line: bool = False,
                            use_kis: bool = True,
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
        if quarter > 1 and force_previous_cumulative:
            for identity in identities:
                fallback_codes.append(identity.corp_code)
        previous = (
            _group(self.dart.multi_accounts(fallback_codes, year, quarter - 1), fallback_codes)
            if fallback_codes else {}
        )
        facts: dict[str, FinancialFact] = {}
        issues: list[dict[str, str]] = []
        profile_updates: dict[str, dict[str, str]] = {}
        end = quarter_end(year, quarter)
        for identity in identities:
            fact = extract_company_fact(identity.corp_code, identity.company_id, year, quarter,
                                        current[identity.corp_code], previous.get(identity.corp_code, []),
                                        previous_fact=previous_facts.get(identity.company_id),
                                        allow_annual_average=False)
            if fact is None:
                fact = FinancialFact(
                    identity.company_id, year, quarter, end, None, None, None, "KRW", "CFS",
                    f"pending:{identity.corp_code}:{year}:Q{quarter}", end, is_pending=True,
                )
            if fact.currency != "KRW":
                if fact.currency == "USD" and usd_krw_rate is not None:
                    fact = fact.with_changes(
                        top_line=fact.top_line * usd_krw_rate if fact.top_line is not None else None,
                        operating_income=fact.operating_income * usd_krw_rate if fact.operating_income is not None else None,
                        net_income=fact.net_income * usd_krw_rate if fact.net_income is not None else None,
                        currency="KRW",
                    )
                else:
                    fact = fact.with_changes(is_pending=True)
                    issues.append({"company": identity.company_name, "field": "currency", "reason": f"unsupported {fact.currency}"})
            if fact.currency == "KRW" and not fact.fully_complete:
                fact, fallback_issue = self._resolve_missing_financials(
                    identity, fact, year, quarter,
                    previous_fact=previous_facts.get(identity.company_id),
                    previous_rows=previous.get(identity.corp_code, []),
                    tolerate_provider_errors=tolerate_provider_errors,
                    profile_updates=profile_updates,
                    allow_backfill_zero_top_line=allow_backfill_zero_top_line,
                    use_kis=use_kis,
                )
                if fallback_issue is not None:
                    issues.append(fallback_issue)
            fact = fact.with_changes(is_pending=fact.is_pending or not fact.fully_complete)
            existing_issues = {(item["company"], item["field"]) for item in issues}
            for field in ("top_line", "operating_income", "net_income"):
                if getattr(fact, field) is None and (identity.company_name, field) not in existing_issues:
                    issues.append({"company": identity.company_name, "field": field, "reason": "provider value missing"})
            facts[identity.company_id] = fact
        if persist_profiles and profile_updates:
            self.repository.upsert_company_profiles(profile_updates.values())
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

    def _ensure_quarter_fx_rate(self, universes: dict[str, list[CompanyIdentity]],
                                year: int, quarter: int, *, write: bool) -> QuarterFxRate:
        stored = self.repository.quarter_fx_rate(year, quarter, "USD", "KRW")
        if stored is not None:
            stored_rate = decimal_value(stored.get("rate"))
            if stored_rate is None or stored_rate <= 0:
                raise ValueError(f"{year}Q{quarter} stored USD/KRW snapshot is invalid")
            return QuarterFxRate(
                fiscal_year=int(stored["fiscal_year"]),
                fiscal_quarter=int(stored["fiscal_quarter"]),
                base_currency=str(stored["base_currency"]),
                quote_currency=str(stored["quote_currency"]),
                target_date=date.fromisoformat(str(stored["target_date"])),
                observed_on=date.fromisoformat(str(stored["observed_on"])),
                rate=stored_rate,
                source=str(stored.get("source") or "ecos"),
            )
        if self.fx is None:
            raise ProviderError(f"{year}Q{quarter} USD/KRW snapshot is missing and ECOS is not configured")
        reference_dates = [row.reference_date for members in universes.values() for row in members]
        if not reference_dates:
            raise ValueError(f"{year}Q{quarter} universe is empty")
        target_date = max(reference_dates)
        observed_on, rate = self.fx.latest_usd_krw(target_date)
        snapshot = QuarterFxRate(
            fiscal_year=year,
            fiscal_quarter=quarter,
            base_currency="USD",
            quote_currency="KRW",
            target_date=target_date,
            observed_on=observed_on,
            rate=rate,
        )
        if write:
            self.repository.upsert_quarter_fx_rate(snapshot.db_row())
        return snapshot

    def run_quarter(self, year: int, quarter: int, *, write: bool = False,
                    incremental: bool = True,
                    refresh_corp_codes: set[str] | None = None,
                    delisting_filings: Iterable[DelistingFiling] | None = None,
                    discover_delistings: bool = False,
                    trust_previous_backfill: bool = False,
                    allow_backfill_zero_top_line: bool = False,
                    use_kis_for_fresh: bool = True,
                    retry_pending: bool = True,
                    refresh_only: bool = False,
                    event_effective_cutoff: date | None = None,
                    deadline_seconds: int | None = None) -> dict[str, Any]:
        if not incremental:
            raise ValueError("automatic collection requires incremental mode")
        if trust_previous_backfill or allow_backfill_zero_top_line:
            raise ValueError("backfill policy is not available in automatic collection")
        if deadline_seconds is not None:
            with execution_deadline(deadline_seconds):
                return self.run_quarter(
                    year, quarter, write=write,
                    incremental=incremental, refresh_corp_codes=refresh_corp_codes,
                    delisting_filings=delisting_filings,
                    discover_delistings=discover_delistings,
                    trust_previous_backfill=trust_previous_backfill,
                    allow_backfill_zero_top_line=allow_backfill_zero_top_line,
                    use_kis_for_fresh=use_kis_for_fresh,
                    retry_pending=retry_pending,
                    refresh_only=refresh_only,
                    event_effective_cutoff=event_effective_cutoff,
                )
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
            fx_rate = self._ensure_quarter_fx_rate(universes, year, quarter, write=write)
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
            supplied_delistings = list(delisting_filings or [])
            delisting_checked_codes: set[str] = set()
            if incremental:
                stored_current = {
                    identity.company_id: stored[(identity.company_id, year, quarter)]
                    for identity in identities
                    if (identity.company_id, year, quarter) in stored
                }
                delisting_events = self._stored_delisting_events(
                    identities, year, quarter, supplied_delistings,
                    effective_cutoff=event_effective_cutoff,
                )
                if discover_delistings:
                    historical_candidates = [
                        identity for identity in identities
                        if stored_current.get(identity.company_id) is not None
                        and stored_current[identity.company_id].is_pending
                        and identity.corp_code not in delisting_events
                    ]
                    delisting_checked_codes.update(
                        identity.corp_code for identity in historical_candidates
                    )
                    discovered_events = self._discover_delisting_events(
                        historical_candidates, year, quarter, write=write,
                    )
                    delisting_events = self._delisting_event_map([
                        *delisting_events.values(), *discovered_events,
                    ])
                provider_refresh = refresh_corp_codes or set()
                selected = [
                    row for row in identities
                    if row.company_id not in manual_ids
                    and row.corp_code not in delisting_events
                    and (
                        row.corp_code in provider_refresh
                        if refresh_only else (
                            row.corp_code in provider_refresh
                            or row.company_id not in stored_current
                            or (
                                stored_current[row.company_id].is_pending
                                and (
                                    stored_current[row.company_id].fully_complete
                                    or stored_current[row.company_id].source_currency != "KRW"
                                )
                            )
                        )
                    )
                ]
                preserved = stored_current
            else:
                # 명시적 백필은 자동/수동 여부와 관계없이 공급자 원자료로 전부 교체한다.
                stored_current = {}
                delisting_events = self._stored_delisting_events(
                    identities, year, quarter, supplied_delistings,
                    effective_cutoff=event_effective_cutoff,
                )
                selected = [
                    row for row in identities if row.corp_code not in delisting_events
                ]
                preserved = {}
            # 저장된 직전 누적 원본을 재사용한다. 시간순 백필의 최초 경계에서
            # 누적 원본이 없을 때만 collect_financials가 직전 분기를 추가 호출한다.
            previous_facts = {
                identity.company_id: stored[(identity.company_id, *previous_key)]
                for identity in selected
                if (identity.company_id, *previous_key) in stored
            }
            fresh_facts, issues = (
                self.collect_financials(
                    selected, year, quarter, previous_facts,
                    usd_krw_rate=fx_rate.rate,
                    tolerate_provider_errors=incremental,
                    force_previous_cumulative=not incremental and not trust_previous_backfill,
                    persist_profiles=write,
                    allow_backfill_zero_top_line=allow_backfill_zero_top_line,
                    use_kis=use_kis_for_fresh,
                )
                if selected else ({}, [])
            )
            if discover_delistings:
                candidate_facts = {**preserved, **fresh_facts}
                undiscovered_pending = [
                    identity for identity in identities
                    if identity.corp_code not in delisting_events
                    and identity.corp_code not in delisting_checked_codes
                    and (candidate := candidate_facts.get(identity.company_id)) is not None
                    and candidate.is_pending
                ]
                discovered_events = self._discover_delisting_events(
                    undiscovered_pending, year, quarter, write=write,
                )
                delisting_events = self._delisting_event_map([
                    *delisting_events.values(), *discovered_events,
                ])

            identities_by_code = {row.corp_code: row for row in identities}
            current_candidates = {**preserved, **fresh_facts}
            delisting_facts: dict[str, FinancialFact] = {}
            for corp_code, event in delisting_events.items():
                identity = identities_by_code.get(corp_code)
                if identity is None or identity.company_id in manual_ids:
                    continue
                existing = current_candidates.get(identity.company_id)
                if existing is not None and not existing.is_pending:
                    # 결정공시로 이미 처리된 기업은 뒤이은 최종 상폐공시에
                    # 다시 반응하지 않으며, 정상 공시값도 덮어쓰지 않는다.
                    continue
                previous = self._previous_fact_for_delisting(
                    identity, year, quarter, stored,
                    usd_krw_rate=fx_rate.rate, write=write,
                )
                if previous is None:
                    issues.append({
                        "company": identity.company_name,
                        "field": "delisting_previous_quarter",
                        "reason": "previous-quarter financials unavailable",
                    })
                    continue
                delisting_facts[identity.company_id] = self._delisting_fact(
                    identity, previous, event, year, quarter,
                )
                self._progress(
                    "delisting_previous_quarter_applied",
                    company=identity.company_name,
                    receipt_no=event.receipt_no,
                )
            if delisting_facts:
                resolved_names = {
                    identities_by_code[corp_code].company_name
                    for corp_code in delisting_events
                    if identities_by_code.get(corp_code) is not None
                    and identities_by_code[corp_code].company_id in delisting_facts
                }
                issues = [item for item in issues if item.get("company") not in resolved_names]
            pending_fallbacks: dict[str, FinancialFact] = {}
            profile_updates: dict[str, dict[str, str]] = {}
            if incremental and retry_pending:
                selected_ids = {row.company_id for row in selected}
                for identity in identities:
                    fact = stored_current.get(identity.company_id)
                    if (
                        identity.company_id in manual_ids
                        or identity.corp_code in delisting_events
                        or identity.company_id in selected_ids
                        or fact is None
                        or not fact.is_pending
                    ):
                        continue
                    retried, fallback_issue = self._resolve_missing_financials(
                        identity, fact, year, quarter,
                        previous_fact=stored.get((identity.company_id, *previous_key)),
                        tolerate_provider_errors=True,
                        profile_updates=profile_updates,
                        stage="pending_retry",
                        allow_backfill_zero_top_line=allow_backfill_zero_top_line,
                    )
                    pending_fallbacks[identity.company_id] = retried
                    if fallback_issue is not None:
                        issues.append(fallback_issue)
                    else:
                        for field in ("top_line", "operating_income", "net_income"):
                            if getattr(retried, field) is None:
                                issues.append({"company": identity.company_name, "field": field, "reason": "provider value missing"})
            if write and profile_updates:
                self.repository.upsert_company_profiles(profile_updates.values())
            facts = {**preserved, **fresh_facts, **pending_fallbacks, **delisting_facts}
            changed_ids = set(fresh_facts) | set(pending_fallbacks) | set(delisting_facts)
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
                self.repository.upsert_company_quarters(company_rows)
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
                "retried_pending_companies": len(pending_fallbacks),
                "resolved_delisting_companies": len(delisting_facts),
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

    @staticmethod
    def _merge_kis_cumulative_only(
        fact: FinancialFact,
        history: dict[tuple[int, int], dict[str, Decimal | None]],
    ) -> FinancialFact:
        """기존 당해값을 보존하면서 비어 있는 KIS 누적 원본만 채운다."""
        if fact.source_currency != "KRW":
            return fact
        values = history.get(fact.key, {})
        changes = {
            f"source_{field}_cumulative": cumulative
            for field in ("top_line", "operating_income", "net_income")
            if getattr(fact, f"source_{field}_cumulative") is None
            and (cumulative := values.get(field)) is not None
        }
        return fact.with_changes(**changes) if changes else fact

    @staticmethod
    def _merge_kis_history(
        fact: FinancialFact,
        history: dict[tuple[int, int], dict[str, Decimal | None]],
        previous_fact: FinancialFact | None,
    ) -> FinancialFact:
        """KIS 누적 원자료로 자동수집의 null 필드만 보충한다."""
        current_values = history.get(fact.key, {})
        previous_values = (
            history.get((fact.fiscal_year, fact.fiscal_quarter - 1), {})
            if fact.fiscal_quarter > 1 else {}
        )
        changes: dict[str, Any] = {}
        for field in ("top_line", "operating_income", "net_income"):
            current = current_values.get(field)
            cumulative_field = f"source_{field}_cumulative"
            if current is not None and getattr(fact, cumulative_field) is None:
                changes[cumulative_field] = current
            if getattr(fact, field) is not None or current is None:
                continue
            if fact.fiscal_quarter == 1:
                standalone = current
            else:
                previous = previous_values.get(field)
                if (
                    previous is None
                    and previous_fact is not None
                    and previous_fact.source_currency == "KRW"
                ):
                    previous = getattr(previous_fact, cumulative_field)
                if previous is None:
                    continue
                standalone = current - previous
            changes[field] = standalone
        if not changes:
            return fact
        updated = fact.with_changes(**changes)
        return updated.with_changes(is_pending=not updated.fully_complete)

    def run_kis_pending(
        self,
        *,
        write: bool = True,
        today: date | None = None,
        deadline_seconds: int | None = None,
    ) -> dict[str, Any]:
        """회사당 KIS 한 번으로 응답 전체 기간의 대기 분기를 보충한다."""
        if deadline_seconds is not None:
            with execution_deadline(deadline_seconds):
                return self.run_kis_pending(write=write, today=today)
        current_day = today or date.today()
        state = self.repository.pipeline_state("daily_kis") or {}
        cursor = state.get("cursor") if isinstance(state.get("cursor"), dict) else {}
        already_processed = (
            {str(value) for value in cursor.get("company_ids", [])}
            if str(cursor.get("last_checked_date") or "") == current_day.isoformat()
            else set()
        )
        if self.kis is None:
            raise ValueError("KIS credentials are required for the KIS pending phase")

        all_pending_rows = self.repository.pending_rows()
        current_period = latest_completed_quarter(current_day)
        comparison_periods = {
            current_period,
            previous_period(*current_period),
        }
        pending_rows = [
            row for row in all_pending_rows
            if (int(row["market_year"]), int(row["market_quarter"])) in comparison_periods
        ]
        ignored_older_pending = len(all_pending_rows) - len(pending_rows)
        pending_by_company: dict[str, dict[tuple[int, int], dict[str, Any]]] = {}
        company_details: dict[str, tuple[str, str]] = {}
        for row in pending_rows:
            company_id = str(row["company_id"])
            key = (int(row["market_year"]), int(row["market_quarter"]))
            pending_by_company.setdefault(company_id, {})[key] = row
            company_details[company_id] = (
                str(row.get("company_name") or company_id),
                str(row.get("stock_code") or "").strip(),
            )

        company_ids = list(pending_by_company)
        reference_periods = {
            period
            for periods in pending_by_company.values()
            for period in periods
        }
        reference_periods.update(
            previous_period(year, quarter)
            for year, quarter in tuple(reference_periods)
        )
        stored_rows = (
            self.repository.company_periods(company_ids, sorted(reference_periods))
            if company_ids else []
        )
        stored = {
            (fact.company_id, fact.fiscal_year, fact.fiscal_quarter): fact
            for row in stored_rows
            if int(row.get("calculation_version") or 0) >= CALCULATION_VERSION
            and str(row.get("source") or "") != "manual"
            for fact in [_financial_from_db(row)]
        }
        changed: dict[tuple[str, int, int], FinancialFact] = {}
        recalculation_periods: set[tuple[int, int]] = set()
        cumulative_reference_updates: set[tuple[str, int, int]] = set()
        issues: list[dict[str, str]] = []
        processed = set(already_processed)
        called_companies = 0
        returned_periods = 0
        for company_id, pending_periods in pending_by_company.items():
            if company_id in already_processed:
                continue
            company_name, ticker = company_details[company_id]
            if not ticker:
                processed.add(company_id)
                issues.append({"company": company_name, "field": "stock_code", "reason": "KIS ticker missing"})
                continue
            self._progress("kis_pending_start", company=company_name, ticker=ticker)
            try:
                history = self.kis.financial_history(ticker)
                called_companies += 1
                returned_periods += len(history)
            except ProviderError as error:
                called_companies += 1
                processed.add(company_id)
                issues.append({"company": company_name, "field": "kis", "reason": str(error)})
                self._progress("kis_pending_failed", company=company_name)
                continue
            processed.add(company_id)
            company_changed = 0
            for year, quarter in pending_periods:
                key = (company_id, year, quarter)
                fact = stored.get(key)
                if fact is None:
                    issues.append({
                        "company": company_name,
                        "field": f"{year}Q{quarter}",
                        "reason": "pending company quarter was not found",
                    })
                    continue
                previous_key = previous_period(year, quarter)
                previous_db_key = (company_id, *previous_key)
                previous_fact = stored.get(previous_db_key)
                if previous_fact is not None:
                    updated_previous = self._merge_kis_cumulative_only(previous_fact, history)
                    if updated_previous != previous_fact:
                        stored[previous_db_key] = updated_previous
                        changed[previous_db_key] = updated_previous
                        cumulative_reference_updates.add(previous_db_key)
                        previous_fact = updated_previous
                updated = self._merge_kis_history(
                    fact,
                    history,
                    previous_fact,
                )
                if updated != fact:
                    stored[key] = updated
                    changed[key] = updated
                    if (
                        any(
                            getattr(updated, field) != getattr(fact, field)
                            for field in ("top_line", "operating_income", "net_income")
                        )
                        or updated.is_pending != fact.is_pending
                    ):
                        recalculation_periods.add((year, quarter))
                    company_changed += 1
            self._progress(
                "kis_pending_done", company=company_name,
                returned_periods=len(history), changed_periods=company_changed,
            )

        changed_periods = sorted({(year, quarter) for _, year, quarter in changed})
        if write and changed:
            self.repository.upsert_company_quarters(
                fact.db_row(calculation_version=CALCULATION_VERSION)
                for fact in changed.values()
            )
            for year, quarter in sorted(recalculation_periods):
                self.recalculate_quarter(year, quarter, write=True)

        unresolved = sum(
            1
            for company_id, periods in pending_by_company.items()
            for year, quarter in periods
            if (
                (candidate := stored.get((company_id, year, quarter))) is None
                or candidate.is_pending
            )
        )
        status = "ready" if unresolved == 0 and not issues else "incomplete"
        summary = {
            "date": current_day.isoformat(), "write": write, "mode": "kis_pending",
            "pending_companies": len(pending_by_company),
            "comparison_periods": [f"{year}Q{quarter}" for year, quarter in sorted(comparison_periods)],
            "ignored_older_pending_periods": ignored_older_pending,
            "called_companies": called_companies,
            "skipped_same_day_companies": len(already_processed & set(pending_by_company)),
            "returned_periods": returned_periods,
            "changed_company_periods": len(changed),
            "filled_reference_cumulative_periods": len(cumulative_reference_updates),
            "recalculated_periods": [
                f"{year}Q{quarter}" for year, quarter in sorted(recalculation_periods)
            ],
            "unresolved_company_periods": unresolved,
            "issues": issues, "status": status,
            "requests": {"kis": self.kis.request_count},
        }
        if write:
            self.repository.save_state("daily_kis", status, {
                "last_checked_date": current_day.isoformat(),
                "company_ids": sorted(processed),
            })
        return summary

    def run_daily(self, *, write: bool = True, today: date | None = None,
                  deadline_seconds: int | None = None) -> dict[str, Any]:
        """마지막 확인 이후의 신규 공시를 DART만으로 증분 처리한다."""
        if deadline_seconds is not None:
            with execution_deadline(deadline_seconds):
                return self.run_daily(write=write, today=today)
        current_day = today or date.today()
        state = self.repository.pipeline_state("daily_filings") or {}
        cursor = state.get("cursor") if isinstance(state.get("cursor"), dict) else {}
        try:
            return self._run_daily_from_cursor(current_day, cursor, write=write)
        except Exception as error:
            # 자동 실행은 실패 시 기존 체크포인트를 그대로 유지한다. 다음 실행이
            # 같은 공시 경계를 다시 읽어 공급자 장애로 자료가 건너뛰지 않게 한다.
            if write:
                self.repository.save_state(
                    "daily_filings", "failed", cursor, str(error)[:2000],
                )
            raise

    def _run_daily_from_cursor(
        self, current_day: date, cursor: dict[str, Any], *, write: bool,
    ) -> dict[str, Any]:
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
        delistings = self.dart.delisting_filings(checked_on, current_day)
        merger_code_loader = getattr(self.dart, "merger_decision_corp_codes", None)
        merger_event_loader = getattr(self.dart, "absorbed_merger_filings", None)
        merger_corp_codes = (
            merger_code_loader(checked_on, current_day)
            if callable(merger_code_loader) and callable(merger_event_loader)
            else set()
        )
        absorbed_mergers = [
            event
            for corp_code in merger_corp_codes
            for event in merger_event_loader(
                checked_on, current_day, corp_code=corp_code,
            )
        ]
        new_filings = [
            filing for filing in filings
            if not (filing.received_on == checked_on and filing.receipt_no in boundary_receipts)
        ]
        new_delistings = [
            filing for filing in [*delistings, *absorbed_mergers]
            if not (filing.received_on == checked_on and filing.receipt_no in boundary_receipts)
        ]
        if write and new_delistings:
            self.repository.upsert_delisting_events(
                filing.db_row() for filing in new_delistings
            )
        year, quarter = latest_completed_quarter(current_day)
        refresh_corp_codes = {
            filing.corp_code for filing in new_filings if filing_period(filing) == (year, quarter)
        }
        result = self.run_quarter(
            year, quarter, write=write, incremental=True,
            refresh_corp_codes=refresh_corp_codes,
            delisting_filings=new_delistings,
            allow_backfill_zero_top_line=False,
            use_kis_for_fresh=False,
            retry_pending=False,
            refresh_only=True,
            event_effective_cutoff=current_day,
        )
        result["filing_discovery"] = {
            "checked_from": checked_on.isoformat(),
            "checked_through": current_day.isoformat(),
            "new_receipts": len(new_filings),
            "refreshed_companies": len(refresh_corp_codes),
            "new_delisting_receipts": len(new_delistings),
            "new_absorbed_merger_receipts": len([
                filing for filing in new_delistings
                if filing.event_type == "absorbed_merger"
            ]),
        }
        result["stale_pending_retries"] = []
        if write:
            self.repository.save_state("daily_filings", result["status"], {
                "last_checked_date": current_day.isoformat(),
                "boundary_receipt_ids": sorted(
                    filing.receipt_no
                    for filing in [*filings, *delistings, *absorbed_mergers]
                    if filing.received_on == current_day
                ),
            })
        return result
