from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .models import USFinancialFact


METRIC_TAGS = {
    "top_line": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
}


def _entries(payload: dict[str, Any], metric: str) -> list[dict[str, Any]]:
    facts = payload.get("facts", {}).get("us-gaap", {})
    result: list[dict[str, Any]] = []
    for tag in METRIC_TAGS[metric]:
        fact = facts.get(tag, {}) if isinstance(facts, dict) else {}
        units = fact.get("units", {}).get("USD", {}) if isinstance(fact, dict) else {}
        if isinstance(units, list):
            result.extend(item for item in units if isinstance(item, dict))
    return result


def _entry_value(entries: list[dict[str, Any]], fy: int, fp: str, accession: str | None, *, annual: bool) -> tuple[Decimal | None, date | None, date | None, date | None]:
    candidates: list[tuple[date, date, date, Decimal]] = []
    for row in entries:
        if int(row.get("fy") or 0) != fy or str(row.get("fp") or "") != fp:
            continue
        if accession is not None and str(row.get("accn") or "") != accession:
            continue
        if str(row.get("form") or "").upper() not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
            continue
        try:
            start, end, filed = date.fromisoformat(str(row["start"])), date.fromisoformat(str(row["end"])), date.fromisoformat(str(row["filed"]))
            value = Decimal(str(row["val"]))
        except (KeyError, ValueError, ArithmeticError):
            continue
        days = (end - start).days + 1
        if annual != (days >= 300):
            continue
        candidates.append((filed, start, end, value))
    if not candidates:
        return None, None, None, None
    filed, start, end, value = max(candidates)
    return value, start, end, filed


def extract_new_sec_facts(company_id: str, payload: dict[str, Any], accessions: set[str]) -> list[USFinancialFact]:
    """Q1–Q3 use SEC's three-month facts; FY produces Q4 only after Q1–Q3 exist."""
    entries = {metric: _entries(payload, metric) for metric in METRIC_TAGS}
    contexts: set[tuple[int, str, str]] = set()
    for rows in entries.values():
        for row in rows:
            accession, fp = str(row.get("accn") or ""), str(row.get("fp") or "")
            fy = int(row.get("fy") or 0)
            if accession in accessions and fp in {"Q1", "Q2", "Q3", "FY"} and fy:
                contexts.add((fy, fp, accession))
    result: list[USFinancialFact] = []
    for fy, fp, accession in sorted(contexts):
        quarter = {"Q1": 1, "Q2": 2, "Q3": 3, "FY": 4}[fp]
        annual = fp == "FY"
        values: dict[str, Decimal | None] = {}
        starts: list[date] = []; ends: list[date] = []; filed_dates: list[date] = []
        for metric, rows in entries.items():
            value, start, end, filed = _entry_value(rows, fy, fp, accession, annual=annual)
            if annual and value is not None:
                prior = [_entry_value(rows, fy, label, None, annual=False)[0] for label in ("Q1", "Q2", "Q3")]
                value = value - sum((item for item in prior if item is not None), Decimal(0)) if all(item is not None for item in prior) else None
            values[metric] = value
            if start: starts.append(start)
            if end: ends.append(end)
            if filed: filed_dates.append(filed)
        if not ends:
            continue
        period_end, filing_date = max(ends), max(filed_dates)
        result.append(USFinancialFact(
            company_id=company_id, fiscal_year=fy, fiscal_quarter=quarter,
            period_start=min(starts) if starts else None, period_end=period_end,
            top_line=values["top_line"], operating_income=values["operating_income"], net_income=values["net_income"],
            source_filing_id=accession, filing_date=filing_date,
            is_pending=any(value is None for value in values.values()),
        ))
    return result
