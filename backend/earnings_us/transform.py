from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .models import USFinancialFact


METRIC_BASES = {
    "top_line": (
        ("Revenues",),
        ("RevenueFromContractWithCustomerExcludingAssessedTax",),
        ("RevenueFromContractWithCustomerIncludingAssessedTax",),
        ("SalesRevenueNet",),
        ("SalesRevenueGoodsNet",),
        ("SalesRevenueServicesNet",),
        ("OperatingRevenues",),
        ("RegulatedAndUnregulatedOperatingRevenue",),
        ("RevenuesNetOfInterestExpense",),
        ("InterestIncomeExpenseNet", "NoninterestIncome"),
    ),
    "operating_income": (
        ("OperatingIncomeLoss",),
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",),
        ("ProfitLoss", "IncomeTaxExpenseBenefit"),
    ),
    "net_income": (
        ("NetIncomeLoss",),
        ("ProfitLoss",),
        ("NetIncomeLossAvailableToCommonStockholdersBasic",),
        ("NetIncomeLossIncludingPortionAttributableToNonredeemableNoncontrollingInterest",),
    ),
}


def _entry_groups(payload: dict[str, Any], metric: str) -> list[list[list[dict[str, Any]]]]:
    """Return single-tag or composite SEC fact bases in preference order."""
    facts = payload.get("facts", {}).get("us-gaap", {})
    result: list[list[list[dict[str, Any]]]] = []
    for basis in METRIC_BASES[metric]:
        components: list[list[dict[str, Any]]] = []
        for tag in basis:
            fact = facts.get(tag, {}) if isinstance(facts, dict) else {}
            units = fact.get("units", {}).get("USD", {}) if isinstance(fact, dict) else {}
            components.append([item for item in units if isinstance(item, dict)] if isinstance(units, list) else [])
        result.append(components)
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
        if annual and days < 300:
            continue
        if not annual and not 60 <= days <= 130:
            continue
        candidates.append((filed, start, end, value))
    if not candidates:
        return None, None, None, None
    filed, start, end, value = max(candidates)
    return value, start, end, filed


def _basis_value(
    components: list[list[dict[str, Any]]], fy: int, fp: str,
    accession: str | None, *, annual: bool,
) -> tuple[Decimal | None, date | None, date | None, date | None]:
    values = [_entry_value(rows, fy, fp, accession, annual=annual) for rows in components]
    if not values or any(item[0] is None for item in values):
        return None, None, None, None
    return (
        sum((item[0] for item in values if item[0] is not None), Decimal(0)),
        min(item[1] for item in values if item[1] is not None),
        max(item[2] for item in values if item[2] is not None),
        max(item[3] for item in values if item[3] is not None),
    )


def _metric_value(
    groups: list[list[list[dict[str, Any]]]],
    fy: int,
    fp: str,
    accession: str,
    *,
    annual: bool,
) -> tuple[Decimal | None, date | None, date | None, date | None]:
    """Use the first available metric basis and never mix bases inside Q4."""
    for components in groups:
        value, start, end, filed = _basis_value(components, fy, fp, accession, annual=annual)
        if value is None:
            continue
        if not annual:
            return value, start, end, filed
        prior = [_basis_value(components, fy, label, None, annual=False)[0] for label in ("Q1", "Q2", "Q3")]
        if all(item is not None for item in prior):
            return value - sum(prior, Decimal(0)), start, end, filed
    return None, None, None, None


def extract_new_sec_facts(company_id: str, payload: dict[str, Any], accessions: set[str]) -> list[USFinancialFact]:
    """Q1–Q3 use SEC's three-month facts; FY produces Q4 only after Q1–Q3 exist."""
    entries = {metric: _entry_groups(payload, metric) for metric in METRIC_BASES}
    contexts: set[tuple[int, str, str]] = set()
    for groups in entries.values():
        for components in groups:
            for rows in components:
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
        for metric, groups in entries.items():
            value, start, end, filed = _metric_value(groups, fy, fp, accession, annual=annual)
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

