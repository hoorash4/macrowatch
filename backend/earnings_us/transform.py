from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .models import USFinancialFact, market_period


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
        ("RevenuesExcludingInterestAndDividends",),
        ("InterestIncomeExpenseNet", "NoninterestIncome"),
        ("CostsAndExpenses", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"),
        ("CostsAndExpenses", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
        ("OperatingExpenses", "OperatingIncomeLoss"),
    ),
    "operating_income": (
        ("OperatingIncomeLoss",),
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",),
        ("ProfitLoss", "IncomeTaxExpenseBenefit"),
        ("NetIncomeLoss", "IncomeTaxExpenseBenefit"),
    ),
    "net_income": (
        ("NetIncomeLoss",),
        ("ProfitLoss",),
        ("NetIncomeLossAvailableToCommonStockholdersBasic",),
        ("NetIncomeLossIncludingPortionAttributableToNonredeemableNoncontrollingInterest",),
    ),
}


def _normalized_sec_row(
    row: dict[str, Any], annual_ends: list[date], relabel_keys: set[tuple[int, str]],
) -> dict[str, Any]:
    """Repair demonstrably conflicting 10-Q labels from their physical periods."""
    form = str(row.get("form") or "").upper()
    fp = str(row.get("fp") or "")
    fy = int(row.get("fy") or 0)
    if form not in {"10-Q", "10-Q/A"} or (fp != "FY" and (fy, fp) not in relabel_keys):
        return row
    try:
        start = date.fromisoformat(str(row["start"]))
        end = date.fromisoformat(str(row["end"]))
    except (KeyError, ValueError):
        return row
    days = (end - start).days + 1
    previous_ends = [annual_end for annual_end in annual_ends if annual_end < end]
    if 221 <= days <= 299:
        fp = "Q3"
    elif 131 <= days <= 220:
        fp = "Q2"
    elif 60 <= days <= 130 and previous_ends:
        elapsed = (end - max(previous_ends)).days
        fp = "Q1" if elapsed <= 120 else "Q2" if elapsed <= 220 else "Q3"
    else:
        return row
    return {**row, "fp": fp}


def _entry_groups(
    payload: dict[str, Any], metric: str, annual_ends: list[date] | None = None,
) -> list[list[list[dict[str, Any]]]]:
    """Return single-tag or composite SEC fact bases in preference order."""
    facts = payload.get("facts", {}).get("us-gaap", {})
    result: list[list[list[dict[str, Any]]]] = []
    for basis in METRIC_BASES[metric]:
        components: list[list[dict[str, Any]]] = []
        for tag in basis:
            fact = facts.get(tag, {}) if isinstance(facts, dict) else {}
            units = fact.get("units", {}).get("USD", {}) if isinstance(fact, dict) else {}
            rows = [item for item in units if isinstance(item, dict)] if isinstance(units, list) else []
            physical_ends: dict[tuple[int, str], set[date]] = {}
            for item in rows:
                try:
                    start = date.fromisoformat(str(item["start"]))
                    end = date.fromisoformat(str(item["end"]))
                    filed = date.fromisoformat(str(item["filed"]))
                    key = (int(item.get("fy") or 0), str(item.get("fp") or ""))
                except (KeyError, ValueError):
                    continue
                if (
                    str(item.get("form") or "").upper() in {"10-Q", "10-Q/A"}
                    and key[0] and key[1] in {"Q1", "Q2", "Q3"}
                    and 60 <= (end - start).days + 1 <= 130
                    and 0 <= (filed - end).days <= 180
                ):
                    physical_ends.setdefault(key, set()).add(end)
            relabel_keys = {key for key, ends in physical_ends.items() if len(ends) > 1}
            components.append([
                _normalized_sec_row(item, annual_ends or [], relabel_keys) for item in rows
            ])
        result.append(components)
    return result


def _entry_value(entries: list[dict[str, Any]], fy: int, fp: str, accession: str | None, *, annual: bool) -> tuple[Decimal | None, date | None, date | None, date | None]:
    candidates: list[tuple[date, date, date, Decimal]] = []
    for row in entries:
        if int(row.get("fy") or 0) != fy or str(row.get("fp") or "") != fp:
            continue
        if accession is not None and str(row.get("accn") or "") != accession:
            continue
        form = str(row.get("form") or "").upper()
        allowed_forms = {"10-K", "10-K/A"} if annual or fp in {"FY", "Q4"} else {"10-Q", "10-Q/A"}
        if form not in allowed_forms:
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


def _cumulative_entry_value(
    entries: list[dict[str, Any]], fy: int, fp: str, accession: str | None,
) -> tuple[Decimal | None, date | None, date | None, date | None]:
    """Return a fiscal YTD fact for Q2 or Q3 when no standalone fact exists."""
    bounds = {"Q2": (131, 220), "Q3": (221, 299)}
    if fp not in bounds:
        return None, None, None, None
    minimum, maximum = bounds[fp]
    candidates: list[tuple[date, date, date, Decimal]] = []
    for row in entries:
        if int(row.get("fy") or 0) != fy or str(row.get("fp") or "") != fp:
            continue
        if accession is not None and str(row.get("accn") or "") != accession:
            continue
        if str(row.get("form") or "").upper() not in {"10-Q", "10-Q/A"}:
            continue
        try:
            start = date.fromisoformat(str(row["start"]))
            end = date.fromisoformat(str(row["end"]))
            filed = date.fromisoformat(str(row["filed"]))
            value = Decimal(str(row["val"]))
        except (KeyError, ValueError, ArithmeticError):
            continue
        if minimum <= (end - start).days + 1 <= maximum:
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


def _cumulative_basis_value(
    components: list[list[dict[str, Any]]], fy: int, fp: str, accession: str | None,
) -> tuple[Decimal | None, date | None, date | None, date | None]:
    values = [_cumulative_entry_value(rows, fy, fp, accession) for rows in components]
    if not values or any(item[0] is None for item in values):
        return None, None, None, None
    return (
        sum((item[0] for item in values if item[0] is not None), Decimal(0)),
        min(item[1] for item in values if item[1] is not None),
        max(item[2] for item in values if item[2] is not None),
        max(item[3] for item in values if item[3] is not None),
    )


def _physical_prior_basis_value(
    components: list[list[dict[str, Any]]], fp: str, fiscal_start: date, before_end: date,
    *, cumulative: bool,
) -> tuple[Decimal | None, date | None, date | None, date | None]:
    """Find a prior fiscal-period fact by physical dates, ignoring unstable SEC ``fy`` labels."""
    values: list[tuple[Decimal | None, date | None, date | None, date | None]] = []
    for rows in components:
        candidates: list[tuple[date, date, date, Decimal]] = []
        for row in rows:
            if str(row.get("fp") or "") != fp or str(row.get("form") or "").upper() not in {"10-Q", "10-Q/A"}:
                continue
            try:
                start = date.fromisoformat(str(row["start"]))
                end = date.fromisoformat(str(row["end"]))
                filed = date.fromisoformat(str(row["filed"]))
                value = Decimal(str(row["val"]))
            except (KeyError, ValueError, ArithmeticError):
                continue
            days = (end - start).days + 1
            valid_duration = 131 <= days <= 299 if cumulative else 60 <= days <= 130
            if start == fiscal_start and end < before_end and valid_duration:
                candidates.append((end, filed, start, value))
        if not candidates:
            values.append((None, None, None, None))
            continue
        end, filed, start, value = max(candidates)
        values.append((value, start, end, filed))
    if not values or any(item[0] is None for item in values):
        return None, None, None, None
    return (
        sum((item[0] for item in values if item[0] is not None), Decimal(0)),
        min(item[1] for item in values if item[1] is not None),
        max(item[2] for item in values if item[2] is not None),
        max(item[3] for item in values if item[3] is not None),
    )


def _first_basis_value(
    groups: list[list[list[dict[str, Any]]]], fy: int, fp: str,
    accession: str | None, *, annual: bool,
) -> tuple[Decimal | None, date | None, date | None, date | None]:
    for components in groups:
        result = _basis_value(components, fy, fp, accession, annual=annual)
        if result[0] is not None:
            return result
    return None, None, None, None


def _first_cumulative_basis_value(
    groups: list[list[list[dict[str, Any]]]], fy: int, fp: str, accession: str | None,
) -> tuple[Decimal | None, date | None, date | None, date | None]:
    for components in groups:
        result = _cumulative_basis_value(components, fy, fp, accession)
        if result[0] is not None:
            return result
    return None, None, None, None


def _metric_value(
    groups: list[list[list[dict[str, Any]]]],
    fy: int,
    fp: str,
    accession: str,
    *,
    annual: bool,
) -> tuple[Decimal | None, date | None, date | None, date | None]:
    """Prefer direct facts, then derive quarters from SEC fiscal YTD facts."""
    if annual:
        # Some 10-K XBRL includes the standalone fourth quarter under the FY
        # context. It is more direct than subtracting three earlier quarters.
        direct = _first_basis_value(groups, fy, fp, accession, annual=False)
        if direct[0] is not None:
            return direct
    for components in groups:
        value, start, end, filed = _basis_value(components, fy, fp, accession, annual=annual)
        if value is None:
            continue
        if not annual:
            return value, start, end, filed
        q3_ytd = _physical_prior_basis_value(components, "Q3", start, end, cumulative=True)
        if q3_ytd[0] is None:
            q3_ytd = _cumulative_basis_value(components, fy, "Q3", None)
        if q3_ytd[0] is not None:
            return value - q3_ytd[0], start, end, filed
        prior = [_basis_value(components, fy, label, None, annual=False)[0] for label in ("Q1", "Q2", "Q3")]
        if all(item is not None for item in prior):
            return value - sum(prior, Decimal(0)), start, end, filed
    if not annual and fp in {"Q2", "Q3"}:
        previous_fp = "Q1" if fp == "Q2" else "Q2"
        for components in groups:
            current_ytd = _cumulative_basis_value(components, fy, fp, accession)
            previous = _physical_prior_basis_value(
                components, previous_fp, current_ytd[1], current_ytd[2], cumulative=fp == "Q3",
            ) if current_ytd[1] is not None and current_ytd[2] is not None else (None, None, None, None)
            if previous[0] is None:
                previous = (
                    _basis_value(components, fy, "Q1", None, annual=False)
                    if fp == "Q2"
                    else _cumulative_basis_value(components, fy, previous_fp, None)
                )
            if current_ytd[0] is not None and previous[0] is not None:
                return current_ytd[0] - previous[0], current_ytd[1], current_ytd[2], current_ytd[3]
        current_ytd = _first_cumulative_basis_value(groups, fy, fp, accession)
        previous = (
            _first_basis_value(groups, fy, "Q1", None, annual=False)
            if fp == "Q2"
            else _first_cumulative_basis_value(groups, fy, previous_fp, None)
        )
        if current_ytd[0] is not None and previous[0] is not None:
            return current_ytd[0] - previous[0], current_ytd[1], current_ytd[2], current_ytd[3]
    if annual:
        annual_value = _first_basis_value(groups, fy, fp, accession, annual=True)
        q3_ytd = (None, None, None, None)
        if annual_value[1] is not None and annual_value[2] is not None:
            for components in groups:
                q3_ytd = _physical_prior_basis_value(
                    components, "Q3", annual_value[1], annual_value[2], cumulative=True,
                )
                if q3_ytd[0] is not None:
                    break
        if q3_ytd[0] is None:
            q3_ytd = _first_cumulative_basis_value(groups, fy, "Q3", None)
        if annual_value[0] is not None and q3_ytd[0] is not None:
            return annual_value[0] - q3_ytd[0], annual_value[1], annual_value[2], annual_value[3]
        prior = [
            _first_basis_value(groups, fy, label, None, annual=False)[0]
            for label in ("Q1", "Q2", "Q3")
        ]
        if annual_value[0] is not None and all(item is not None for item in prior):
            return annual_value[0] - sum(prior, Decimal(0)), annual_value[1], annual_value[2], annual_value[3]
    return None, None, None, None


def _annual_period_ends(entries: dict[str, list[list[list[dict[str, Any]]]]]) -> list[date]:
    """Return physical year ends independently of mutable SEC fiscal-year labels."""
    result: set[date] = set()
    for groups in entries.values():
        for components in groups:
            for rows in components:
                for row in rows:
                    if str(row.get("fp") or "") not in {"FY", "Q4"} or str(row.get("form") or "").upper() not in {"10-K", "10-K/A"}:
                        continue
                    try:
                        start = date.fromisoformat(str(row["start"]))
                        end = date.fromisoformat(str(row["end"]))
                    except (KeyError, ValueError):
                        continue
                    if (end - start).days + 1 >= 300:
                        result.add(end)
    return sorted(result)


def _physical_fiscal_year(period_end: date, quarter: int, annual_ends: list[date], fallback: int) -> int:
    """Build a stable fiscal key from the represented period, not mutable SEC ``fy`` metadata."""
    if quarter == 4:
        return market_period(period_end)[0]
    following = [end for end in annual_ends if period_end <= end <= period_end.fromordinal(period_end.toordinal() + 370)]
    return market_period(min(following))[0] if following else fallback


def extract_new_sec_facts(company_id: str, payload: dict[str, Any], accessions: set[str]) -> list[USFinancialFact]:
    """Q1–Q3 use SEC's three-month facts; FY produces Q4 only after Q1–Q3 exist."""
    raw_entries = {metric: _entry_groups(payload, metric) for metric in METRIC_BASES}
    annual_ends = _annual_period_ends(raw_entries)
    entries = {metric: _entry_groups(payload, metric, annual_ends) for metric in METRIC_BASES}
    contexts: set[tuple[int, str, str]] = set()
    for groups in entries.values():
        for components in groups:
            for rows in components:
                for row in rows:
                    accession, fp = str(row.get("accn") or ""), str(row.get("fp") or "")
                    fy = int(row.get("fy") or 0)
                    try:
                        row_end = date.fromisoformat(str(row.get("end") or ""))
                    except ValueError:
                        row_end = None
                    # A later 10-Q often repeats the previous fiscal year-end's
                    # standalone three-month comparison.  It is useful when
                    # deriving another fact, but it is not a new Q1-Q3 filing
                    # context and must not overwrite the real prior quarter.
                    if (
                        str(row.get("form") or "").upper() in {"10-Q", "10-Q/A"}
                        and row_end in annual_ends
                    ):
                        continue
                    if accession in accessions and fp in {"Q1", "Q2", "Q3", "Q4", "FY"} and fy:
                        contexts.add((fy, fp, accession))
    result: dict[tuple[date, int], USFinancialFact] = {}
    for fy, fp, accession in sorted(contexts):
        quarter = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 4}[fp]
        annual = fp in {"Q4", "FY"}
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
        fact = USFinancialFact(
            company_id=company_id,
            fiscal_year=_physical_fiscal_year(period_end, quarter, annual_ends, fy),
            fiscal_quarter=quarter,
            period_start=min(starts) if starts else None, period_end=period_end,
            top_line=values["top_line"], operating_income=values["operating_income"], net_income=values["net_income"],
            source_filing_id=accession, filing_date=filing_date,
            is_pending=any(value is None for value in values.values()),
        )
        physical_key = (period_end, quarter)
        current = result.get(physical_key)
        if current is None or (fact.fully_complete, fact.filing_date) > (current.fully_complete, current.filing_date):
            result[physical_key] = fact
    return sorted(result.values(), key=lambda fact: (fact.period_end, fact.fiscal_quarter, fact.filing_date))

