from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re
from typing import Any, Iterable

from .dart_financials import DartBatchResult, DartFinancialCollector
from .ecos import EcosFxClient, EcosFxError
from .financials import profit_margin
from .growth import calculate_company_growth
from .krx import KrxOpenApiClient
from .market import aggregate_market_quarter, calculate_market_series
from .models import MarketQuarter, QuarterValue, UniverseCandidate, UniverseMember
from .open_dart import OpenDartV2Client
from .repository import EarningsV2Store
from .universe import MARKET_TARGETS, select_final_universe


QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
# Version 3 refreshes the one-year pilot exactly once for the individual-account
# top-line fallback and persisted profit-margin calculations. Subsequent runs
# reuse complete v3 rows and therefore make no duplicate financial calls.
EXTRACTION_VERSION = 3


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _receipt_date(receipt: str) -> date:
    if not re.fullmatch(r"\d{14}", receipt):
        raise ValueError("OpenDART response lacks a valid receipt number")
    return datetime.strptime(receipt[:8], "%Y%m%d").date()


def _quarter_from_record(row: dict[str, Any]) -> QuarterValue:
    return QuarterValue(
        company_id=str(row["company_id"]),
        fiscal_year=int(row["fiscal_year"]),
        fiscal_quarter=int(row["fiscal_quarter"]),
        market_year=int(row["market_year"]),
        market_quarter=int(row["market_quarter"]),
        period_start=_date(row.get("period_start")),
        period_end=_date(row["period_end"]),
        top_line=_decimal(row.get("top_line")),
        operating_income=_decimal(row.get("operating_income")),
        net_income=_decimal(row.get("net_income")),
        currency=str(row["currency"]),
        consolidation_scope=str(row["consolidation_scope"]),
        source=str(row.get("source") or "open_dart"),
        source_filing_id=str(row.get("source_filing_id") or ""),
        filing_date=_date(row.get("filing_date")),
        revision_reference_date=_date(row.get("revision_reference_date")),
        quality_status=str(row.get("quality_status") or "draft"),
        calculation_version=int(row.get("calculation_version") or 1),
        operating_margin_pct=_decimal(row.get("operating_margin_pct")),
        net_margin_pct=_decimal(row.get("net_margin_pct")),
        operating_income_yoy_pct=_decimal(row.get("operating_income_yoy_pct")),
        operating_income_yoy_state=str(row.get("operating_income_yoy_state") or "missing_prior"),
        net_income_yoy_pct=_decimal(row.get("net_income_yoy_pct")),
        net_income_yoy_state=str(row.get("net_income_yoy_state") or "missing_prior"),
        operating_income_qoq_sa_pct=_decimal(row.get("operating_income_qoq_sa_pct")),
        operating_income_qoq_state=str(row.get("operating_income_qoq_state") or "insufficient_history"),
        net_income_qoq_sa_pct=_decimal(row.get("net_income_qoq_sa_pct")),
        net_income_qoq_state=str(row.get("net_income_qoq_state") or "insufficient_history"),
    )


def _market_from_record(row: dict[str, Any]) -> MarketQuarter:
    return MarketQuarter(
        market_id=str(row["market_id"]),
        market_year=int(row["market_year"]),
        market_quarter=int(row["market_quarter"]),
        average_operating_income=_decimal(row.get("average_operating_income")),
        average_net_income=_decimal(row.get("average_net_income")),
        actual_company_count=int(row.get("actual_company_count") or 0),
        target_company_count=int(row["target_company_count"]),
        completion_status=str(row["completion_status"]),
        operating_margin_pct=_decimal(row.get("operating_margin_pct")),
        net_margin_pct=_decimal(row.get("net_margin_pct")),
        operating_income_yoy_pct=_decimal(row.get("operating_income_yoy_pct")),
        operating_income_yoy_state=str(row.get("operating_income_yoy_state") or "missing_prior"),
        net_income_yoy_pct=_decimal(row.get("net_income_yoy_pct")),
        net_income_yoy_state=str(row.get("net_income_yoy_state") or "missing_prior"),
        operating_income_qoq_sa_pct=_decimal(row.get("operating_income_qoq_sa_pct")),
        operating_income_qoq_state=str(row.get("operating_income_qoq_state") or "insufficient_history"),
        net_income_qoq_sa_pct=_decimal(row.get("net_income_qoq_sa_pct")),
        net_income_qoq_state=str(row.get("net_income_qoq_state") or "insufficient_history"),
    )


@dataclass(frozen=True)
class KoreaQuarterContext:
    market_id: str
    year: int
    quarter: int
    reference_date: date
    members: tuple[UniverseMember, ...]
    corp_code_by_company: dict[str, str]
    company_name_by_company: dict[str, str]


class KoreaEarningsPipeline:
    """Point-in-time universe discovery, batch collection, calculation and persistence."""

    def __init__(
        self,
        *,
        krx: KrxOpenApiClient,
        dart: OpenDartV2Client,
        fx: EcosFxClient,
        store: EarningsV2Store,
    ) -> None:
        self.krx = krx
        self.dart = dart
        self.fx = fx
        self.financials = DartFinancialCollector(dart)
        self.store = store

    def _discover_quarter(
        self,
        market_id: str,
        year: int,
        quarter: int,
        corp_map: dict[str, tuple[str, str]],
    ) -> KoreaQuarterContext:
        month, day = QUARTER_END[quarter]
        reference_date, securities = self.krx.last_trading_day(
            market_id, date(year, month, day),
        )
        target = MARKET_TARGETS[market_id]
        selected = [security for security in securities if security.stock_code in corp_map][:target]
        companies = []
        identifiers = []
        candidates = []
        corp_code_by_company = {}
        company_name_by_company = {}
        exchange = "KOSPI" if market_id == "kr_largecap" else "KOSDAQ"

        for security in selected:
            corp_code, official_name = corp_map[security.stock_code]
            company_id = f"kr:{corp_code}"
            company_name = official_name or security.name
            corp_code_by_company[company_id] = corp_code
            company_name_by_company[company_id] = company_name
            companies.append({
                "company_id": company_id,
                "country": "KR",
                "company_name": company_name,
                "reporting_currency": "KRW",
                "entity_kind": "general",
            })
            identifiers.extend([
                {
                    "company_id": company_id,
                    "identifier_type": "dart_corp_code",
                    "identifier_value": corp_code,
                    "is_primary": True,
                },
                {
                    "company_id": company_id,
                    "identifier_type": "krx_code",
                    "identifier_value": security.stock_code,
                    "exchange": exchange,
                    "is_primary": True,
                },
            ])
            candidates.append(UniverseCandidate(
                company_id=company_id,
                company_name=company_name,
                currency="KRW",
                market_cap=security.market_cap,
                reference_date=reference_date,
            ))

        self.store.upsert_companies(companies)
        self.store.upsert_identifiers(identifiers)
        members = select_final_universe(
            market_id=market_id,
            market_year=year,
            market_quarter=quarter,
            candidates=candidates,
            selection_method="direct_market_cap",
            target_count=target,
        )
        self.store.replace_universe(market_id, year, quarter, members)
        return KoreaQuarterContext(
            market_id=market_id,
            year=year,
            quarter=quarter,
            reference_date=reference_date,
            members=tuple(members),
            corp_code_by_company=corp_code_by_company,
            company_name_by_company=company_name_by_company,
        )

    def _load_histories(self, company_ids: list[str]) -> dict[str, list[QuarterValue]]:
        histories: dict[str, list[QuarterValue]] = {company_id: [] for company_id in company_ids}
        for record in self.store.get_company_quarters_many(company_ids):
            row = _quarter_from_record(record)
            histories.setdefault(row.company_id, []).append(row)
        return histories

    def _collect_context(
        self,
        context: KoreaQuarterContext,
        histories: dict[str, list[QuarterValue]],
    ) -> set[str]:
        current_by_company = {
            row.company_id: row
            for company_id in context.corp_code_by_company
            for row in histories.get(company_id, [])
            if row.fiscal_year == context.year and row.fiscal_quarter == context.quarter
        }
        pending_ids = [
            member.company_id
            for member in context.members
            if current_by_company.get(member.company_id) is None
            or current_by_company[member.company_id].quality_status != "complete"
            or current_by_company[member.company_id].calculation_version < EXTRACTION_VERSION
        ]
        pending_codes = [context.corp_code_by_company[company_id] for company_id in pending_ids]
        batch = (
            self.financials.collect(pending_codes, context.year, context.quarter)
            if pending_codes else DartBatchResult(values={}, errors={})
        )
        missing: list[dict[str, str]] = []
        touched: set[str] = set()
        month, day = QUARTER_END[context.quarter]
        quarter_end = date(context.year, month, day)

        def record_missing(company_id: str, reason: str) -> None:
            missing.append({
                "company_id": company_id,
                "company_name": context.company_name_by_company[company_id],
                "reason": reason,
            })
            existing = current_by_company.get(company_id)
            if existing is None:
                return
            # A superseded extractor must not leave an old, known-bad fact
            # marked complete when the replacement cannot be verified.
            invalidated = replace(
                existing,
                top_line=None,
                operating_income=None,
                net_income=None,
                quality_status="review_required",
                calculation_version=EXTRACTION_VERSION,
                operating_income_yoy_pct=None,
                operating_income_yoy_state="missing_prior",
                net_income_yoy_pct=None,
                net_income_yoy_state="missing_prior",
                operating_income_qoq_sa_pct=None,
                operating_income_qoq_state="insufficient_history",
                net_income_qoq_sa_pct=None,
                net_income_qoq_state="insufficient_history",
            )
            histories[company_id] = [
                row for row in histories.get(company_id, []) if row.key != invalidated.key
            ] + [invalidated]
            touched.add(company_id)

        for company_id in pending_ids:
            corp_code = context.corp_code_by_company[company_id]
            financial = batch.values.get(corp_code)
            if financial is None:
                record_missing(
                    company_id,
                    batch.errors.get(corp_code, "OpenDART required values unavailable"),
                )
                continue
            source_currency = financial.currency or "KRW"
            if source_currency == "KRW":
                exchange_rate = Decimal("1")
            elif source_currency == "USD":
                try:
                    # Financial facts belong to the calendar quarter.  The
                    # market-cap reference date may be an earlier exchange
                    # trading day, so FX must anchor to the actual quarter end.
                    exchange_rate = self.fx.usd_krw_on_or_before(quarter_end)
                except EcosFxError as error:
                    record_missing(company_id, str(error))
                    continue
            else:
                record_missing(company_id, f"unsupported reporting currency: {source_currency}")
                continue
            try:
                filing_date = _receipt_date(financial.source_filing_id)
            except ValueError as error:
                record_missing(company_id, str(error))
                continue
            quality_status = "complete" if financial.complete else "review_required"
            if quality_status != "complete":
                missing.append({
                    "company_id": company_id,
                    "company_name": context.company_name_by_company[company_id],
                    "reason": batch.errors.get(
                        corp_code,
                        "OpenDART required values partially unavailable",
                    ),
                })

            def converted(value: Decimal | None) -> Decimal | None:
                return value * exchange_rate if value is not None else None

            top_line = converted(financial.top_line)
            operating_income = converted(financial.operating_income)
            net_income = converted(financial.net_income)
            row = QuarterValue(
                company_id=company_id,
                fiscal_year=context.year,
                fiscal_quarter=context.quarter,
                market_year=context.year,
                market_quarter=context.quarter,
                period_end=quarter_end,
                top_line=top_line,
                operating_income=operating_income,
                net_income=net_income,
                currency="KRW",
                consolidation_scope=financial.scope,
                source="open_dart",
                source_filing_id=financial.source_filing_id,
                filing_date=filing_date,
                quality_status=quality_status,
                calculation_version=EXTRACTION_VERSION,
                operating_margin_pct=profit_margin(operating_income, top_line),
                net_margin_pct=profit_margin(net_income, top_line),
            )
            histories[company_id] = [
                existing for existing in histories.get(company_id, [])
                if existing.key != row.key
            ] + [row]
            touched.add(company_id)

        print(json.dumps({
            "market_id": context.market_id,
            "year": context.year,
            "quarter": context.quarter,
            "universe": len(context.members),
            "reused": len(context.members) - len(pending_ids),
            "requested": len(pending_ids),
            "collected": len(pending_ids) - len(missing),
            "provider_requests": batch.request_counts,
            "missing": missing,
        }, ensure_ascii=False), flush=True)
        return touched

    def _store_company_series(
        self,
        histories: dict[str, list[QuarterValue]],
        touched_companies: set[str],
    ) -> dict[str, list[QuarterValue]]:
        calculated_histories = {
            company_id: calculate_company_growth(history)
            for company_id, history in histories.items()
        }
        rows_to_store = [
            row
            for company_id in touched_companies
            for row in calculated_histories[company_id]
        ]
        if rows_to_store:
            self.store.upsert_company_quarters(rows_to_store)
        return calculated_histories

    def _build_market_rows(
        self,
        contexts: list[KoreaQuarterContext],
        histories: dict[str, list[QuarterValue]],
    ) -> tuple[list[dict[str, Any]], dict[str, list[MarketQuarter]]]:
        coverage = []
        market_rows: dict[str, list[MarketQuarter]] = {}
        for context in contexts:
            company_values = []
            complete_count = 0
            for member in context.members:
                row = next((
                    candidate
                    for candidate in histories.get(member.company_id, [])
                    if candidate.fiscal_year == context.year
                    and candidate.fiscal_quarter == context.quarter
                ), None)
                if row is None:
                    company_values.append((None, None, None, "missing"))
                else:
                    company_values.append((
                        row.top_line,
                        row.operating_income,
                        row.net_income,
                        row.quality_status,
                    ))
                    if row.quality_status == "complete":
                        complete_count += 1
            market_rows.setdefault(context.market_id, []).append(aggregate_market_quarter(
                market_id=context.market_id,
                market_year=context.year,
                market_quarter=context.quarter,
                company_values=company_values,
                historical=False,
            ))
            coverage.append({
                "market_id": context.market_id,
                "year": context.year,
                "quarter": context.quarter,
                "reference_date": context.reference_date.isoformat(),
                "target": MARKET_TARGETS[context.market_id],
                "universe": len(context.members),
                "financials": complete_count,
                "missing_count": len(context.members) - complete_count,
            })
        return coverage, market_rows

    def _store_market_series(self, market_rows: dict[str, list[MarketQuarter]]) -> None:
        for market_id, current_rows in market_rows.items():
            existing = {
                row.key: row
                for row in map(_market_from_record, self.store.get_market_quarters(market_id))
            }
            for row in current_rows:
                existing[row.key] = row
            self.store.upsert_market_quarters(calculate_market_series(existing.values()))

    def run(
        self,
        quarters: Iterable[tuple[str, int, int]],
        *,
        source: str,
        operation: str,
    ) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc)
        self.store.save_pipeline_state(
            source=source,
            operation=operation,
            cursor={},
            status="running",
        )
        try:
            corp_map = self.dart.corp_code_map()
            contexts = [
                self._discover_quarter(market_id, year, quarter, corp_map)
                for market_id, year, quarter in quarters
            ]
            company_ids = list(dict.fromkeys(
                member.company_id for context in contexts for member in context.members
            ))
            histories = self._load_histories(company_ids)
            touched_companies = set().union(*(
                self._collect_context(context, histories) for context in contexts
            ))
            calculated_histories = self._store_company_series(histories, touched_companies)
            coverage, market_rows = self._build_market_rows(contexts, calculated_histories)
            self._store_market_series(market_rows)

            complete = all(
                item["universe"] == item["target"]
                and item["financials"] == item["target"]
                for item in coverage
            )
            status = "ready" if complete else "incomplete"
            summary = {
                "status": status,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "coverage": coverage,
            }
            self.store.save_pipeline_state(
                source=source,
                operation=operation,
                cursor={"coverage": coverage},
                status=status,
                last_success_at=datetime.now(timezone.utc),
            )
            return summary
        except Exception as error:
            self.store.save_pipeline_state(
                source=source,
                operation=operation,
                cursor={},
                status="failed",
                last_error=str(error)[:2000],
            )
            raise
