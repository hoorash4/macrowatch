from __future__ import annotations

from datetime import date
from typing import Any
from .models import FinancialFact, MarketFact
from .values import decimal_value


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
