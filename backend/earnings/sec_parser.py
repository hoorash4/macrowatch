"""Normalize SEC company facts into compact, complete fiscal-quarter rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


METRIC_ALIASES = {
    "revenue": (
        # Banks and broker-dealers commonly use this total after interest
        # expense instead of an industrial-company sales tag.
        "RevenuesNetOfInterestExpense",
        "TotalRevenuesNetOfInterestExpense",
        "TotalRevenueNetOfInterestExpense",
        "OperatingRevenues",
        "RegulatedAndUnregulatedOperatingRevenue",
        "SalesAndOtherOperatingRevenue",
        "RevenuesAndOtherIncome",
        "TotalRevenuesAndOtherIncome",
        "NetSales",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "operating_income": (
        "OperatingIncomeLoss",
        "IncomeLossBeforeIncomeTaxes",
        "IncomeFromContinuingOperationsBeforeTaxes",
        "IncomeLossFromContinuingOperationsBeforeTaxes",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "net_income": (
        "NetIncomeLoss",
        "NetIncome",
        "NetIncomeAttributableToParent",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ),
}


@dataclass(frozen=True)
class Fact:
    metric: str
    alias_priority: int
    fiscal_year: int
    fiscal_period: str
    start: date
    end: date
    filed: date
    accession: str
    form: str
    value: Decimal

    @property
    def duration_days(self) -> int:
        return (self.end - self.start).days + 1


def _parsed_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _metric_facts(payload: dict[str, Any], metric: str) -> list[Fact]:
    taxonomies = payload.get("facts", {})
    if not isinstance(taxonomies, dict):
        return []
    result: list[Fact] = []
    for priority, alias in enumerate(METRIC_ALIASES[metric]):
        for taxonomy in taxonomies.values():
            if not isinstance(taxonomy, dict):
                continue
            units = taxonomy.get(alias, {}).get("units", {})
            rows = units.get("USD", []) if isinstance(units, dict) else []
            for raw in rows if isinstance(rows, list) else []:
                if not isinstance(raw, dict):
                    continue
                start, end, filed = (
                    _parsed_date(raw.get("start")),
                    _parsed_date(raw.get("end")),
                    _parsed_date(raw.get("filed")),
                )
                value = _decimal(raw.get("val"))
                form = str(raw.get("form") or "").upper()
                fiscal_period = str(raw.get("fp") or "").upper()
                accession = str(raw.get("accn") or "").strip()
                try:
                    fiscal_year = int(raw.get("fy"))
                except (TypeError, ValueError):
                    continue
                if (
                    start is None or end is None or filed is None or value is None
                    or start > end
                    or form not in {"10-Q", "10-Q/A", "10-K", "10-K/A"}
                    or fiscal_period not in {"Q1", "Q2", "Q3", "Q4", "FY"}
                    or not accession
                ):
                    continue
                result.append(Fact(
                    metric, priority, fiscal_year, fiscal_period,
                    start, end, filed, accession, form, value,
                ))
    return result


def _best(candidates: Iterable[Fact]) -> Fact | None:
    values = list(candidates)
    if not values:
        return None
    # Comparative values can carry the current filing's fy/fp. The latest
    # period end identifies the current fiscal period; latest filing then lets
    # amendments replace the original observation.
    return max(values, key=lambda fact: (
        fact.end, -fact.alias_priority, fact.filed,
        fact.form.endswith("/A"), fact.accession,
    ))


def _quarter_fact(facts: list[Fact], fiscal_year: int, quarter: int) -> Fact | None:
    fp = f"Q{quarter}"
    return _best(
        fact for fact in facts
        if fact.fiscal_year == fiscal_year and fact.fiscal_period == fp
        and 60 <= fact.duration_days <= 125
    )


def _annual_fact(facts: list[Fact], fiscal_year: int) -> Fact | None:
    return _best(
        fact for fact in facts
        if fact.fiscal_year == fiscal_year and fact.fiscal_period == "FY"
        and 300 <= fact.duration_days <= 440
    )


def _quarter_number(period_end: date) -> int:
    return (period_end.month - 1) // 3 + 1


def _number_text(value: Decimal) -> str:
    return format(value, "f")


def _filing_url(cik: str, accession: str) -> str:
    cik_number = str(int(cik))
    compact = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_number}/{compact}/{accession}-index.html"


def canonical_sec_quarters(
    payload: dict[str, Any],
    *,
    cik: str,
    as_of_year: int,
    years: int = 10,
) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    """Return complete quarters only; missing core metrics remain explicit gaps."""
    first_year = as_of_year - years + 1
    by_metric = {metric: _metric_facts(payload, metric) for metric in METRIC_ALIASES}
    rows: list[dict[str, Any]] = []
    gaps: list[tuple[int, int]] = []

    # A January/February fiscal year-end labels periods by the year in which
    # the fiscal year ends. During calendar 2026, for example, CRWD and TJX
    # already file fiscal 2027 quarters. Scan one fiscal label ahead while
    # keeping the missing-period contract anchored to calendar as_of_year.
    for fiscal_year in range(first_year, as_of_year + 2):
        selected: dict[int, dict[str, Fact]] = {quarter: {} for quarter in range(1, 5)}
        for metric, facts in by_metric.items():
            for quarter in range(1, 4):
                fact = _quarter_fact(facts, fiscal_year, quarter)
                if fact is not None:
                    selected[quarter][metric] = fact
            direct_q4 = _quarter_fact(facts, fiscal_year, 4)
            if direct_q4 is not None:
                selected[4][metric] = direct_q4
                continue
            annual = _annual_fact(facts, fiscal_year)
            earlier = [selected[quarter].get(metric) for quarter in range(1, 4)]
            if annual is not None and all(earlier):
                q4_start = max(fact.end for fact in earlier if fact is not None) + timedelta(days=1)
                if q4_start > annual.end:
                    continue
                selected[4][metric] = Fact(
                    metric=metric,
                    alias_priority=annual.alias_priority,
                    fiscal_year=fiscal_year,
                    fiscal_period="Q4",
                    start=q4_start,
                    end=annual.end,
                    filed=annual.filed,
                    accession=annual.accession,
                    form=annual.form,
                    value=annual.value - sum((fact.value for fact in earlier if fact is not None), Decimal(0)),
                )

        for quarter in range(1, 5):
            metrics = selected[quarter]
            if set(metrics) != set(METRIC_ALIASES):
                gaps.append((fiscal_year, quarter))
                continue
            representative = max(metrics.values(), key=lambda fact: (fact.filed, fact.accession))
            period_start = min(fact.start for fact in metrics.values())
            period_end = max(fact.end for fact in metrics.values())
            if period_start > period_end:
                gaps.append((fiscal_year, quarter))
                continue
            market_year = period_end.year
            market_quarter = _quarter_number(period_end)
            rows.append({
                "filing": {
                    # One annual filing can supply a derived Q4 and SEC
                    # comparative facts can occasionally map one accession to
                    # more than one fiscal key. Keep the official accession in
                    # the URL while making the database filing key unambiguous.
                    "source_filing_id": f"{cik}:{representative.accession}:{fiscal_year}Q{quarter}",
                    "filing_kind": "amendment" if representative.form.endswith("/A")
                    else "annual" if quarter == 4 else "quarterly",
                    "fiscal_year": fiscal_year,
                    "fiscal_quarter": quarter,
                    "market_year": market_year,
                    "market_quarter": market_quarter,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "filing_date": representative.filed.isoformat(),
                    "is_correction": representative.form.endswith("/A"),
                    "source_url": _filing_url(cik, representative.accession),
                    "metadata": {"form": representative.form},
                },
                "quarter": {
                    "fiscal_year": fiscal_year,
                    "fiscal_quarter": quarter,
                    "market_year": market_year,
                    "market_quarter": market_quarter,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "revenue": _number_text(metrics["revenue"].value),
                    "operating_income": _number_text(metrics["operating_income"].value),
                    "net_income": _number_text(metrics["net_income"].value),
                    "currency": "USD",
                    "consolidation_scope": "NA",
                    "source_updated_at": representative.filed.isoformat() + "T00:00:00Z",
                },
            })
    # SEC comparative columns can be re-filed with the newer report's fy/fp,
    # producing two fiscal keys for one economic quarter. The filing closest
    # to the period end is the original period identity; keep exactly one row
    # per company/period_end to match the canonical database invariant.
    by_period_end: dict[str, dict[str, Any]] = {}
    for row in rows:
        period_end = date.fromisoformat(row["filing"]["period_end"])
        filing_date = date.fromisoformat(row["filing"]["filing_date"])
        delay = (filing_date - period_end).days
        preference = (delay < 0, abs(delay), filing_date.toordinal())
        current = by_period_end.get(period_end.isoformat())
        if current is None or preference < current["_preference"]:
            by_period_end[period_end.isoformat()] = {"row": row, "_preference": preference}
    rows = sorted(
        (item["row"] for item in by_period_end.values()),
        key=lambda row: (row["filing"]["fiscal_year"], row["filing"]["fiscal_quarter"]),
    )
    complete_keys = {
        (row["filing"]["fiscal_year"], row["filing"]["fiscal_quarter"])
        for row in rows
    }
    gaps = [
        (fiscal_year, quarter)
        for fiscal_year in range(first_year, as_of_year + 1)
        for quarter in range(1, 5)
        if (fiscal_year, quarter) not in complete_keys
    ]
    return rows, gaps
