"""Normalize OpenDART account rows without performing database writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable


REPORT_QUARTERS = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}

# Standard XBRL IDs take priority. Names are controlled fallbacks for companies
# that publish extension accounts or omit a standard ID.
ACCOUNT_ID_PRIORITIES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "ifrs-full_revenue",
        "ifrs-full_revenuefromcontractswithcustomers",
        "dart_revenue",
    ),
    "operating_income": ("dart_operatingincomeloss",),
    "net_income": (
        "ifrs-full_profitloss",
        "ifrs-full_profitlossattributabletoownersofparent",
    ),
}

ACCOUNT_NAME_ALIASES: dict[str, set[str]] = {
    "revenue": {"매출액", "영업수익", "수익매출액", "수익"},
    "operating_income": {"영업이익", "영업이익손실", "영업손익"},
    "net_income": {"당기순이익", "당기순이익손실", "분기순이익", "반기순이익"},
}

# Earnings Momentum stores only the three income-statement totals below.
REQUIRED_METRICS = ("revenue", "operating_income", "net_income")


@dataclass(frozen=True)
class DartAccountFact:
    corp_code: str
    receipt_number: str
    business_year: int
    report_code: str
    fiscal_quarter: int
    metric: str
    account_id: str
    account_name: str
    statement_type: str
    consolidation_scope: str
    period_start: date | None
    period_end: date | None
    current_amount: Decimal | None
    cumulative_amount: Decimal | None
    currency: str | None


def parse_amount(raw_value: Any) -> Decimal | None:
    """Parse DART numbers while retaining exact decimal precision."""
    if raw_value is None:
        return None
    text = str(raw_value).strip().replace(",", "").replace(" ", "")
    if text in {"", "-", "—", "–", "null", "None"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"Invalid OpenDART amount: {raw_value!r}") from error
    return -value if negative else value


def _normalized_account_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()


def metric_for_account(account_id: str, account_name: str) -> tuple[str, int] | None:
    normalized_id = account_id.strip().lower()
    for metric, identifiers in ACCOUNT_ID_PRIORITIES.items():
        if normalized_id in identifiers:
            return metric, identifiers.index(normalized_id)
    normalized_name = _normalized_account_name(account_name)
    for metric, aliases in ACCOUNT_NAME_ALIASES.items():
        if normalized_name in aliases:
            return metric, 100
    return None


def _parse_period(raw_value: Any) -> tuple[date | None, date | None]:
    values = re.findall(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(raw_value or ""))
    parsed = [date(int(year), int(month), int(day)) for year, month, day in values]
    if not parsed:
        return None, None
    return (parsed[0], parsed[-1]) if len(parsed) > 1 else (None, parsed[0])


def parse_account_rows(payload: dict[str, Any]) -> list[DartAccountFact]:
    """Extract only revenue, operating income, and net income."""
    facts: list[DartAccountFact] = []
    rows = payload.get("list") or []
    if not isinstance(rows, list):
        raise ValueError("OpenDART payload.list must be an array.")
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        report_code = str(raw.get("reprt_code") or "").strip()
        if report_code not in REPORT_QUARTERS:
            continue
        account_id = str(raw.get("account_id") or "").strip()
        account_name = str(raw.get("account_nm") or "").strip()
        match = metric_for_account(account_id, account_name)
        if not match:
            continue
        metric, _priority = match
        period_start, period_end = _parse_period(raw.get("thstrm_dt"))
        scope = str(raw.get("fs_div") or "NA").strip().upper()
        if scope not in {"CFS", "OFS"}:
            scope = "NA"
        currency = str(raw.get("currency") or "").strip().upper() or None
        facts.append(DartAccountFact(
            corp_code=str(raw.get("corp_code") or "").strip(),
            receipt_number=str(raw.get("rcept_no") or "").strip(),
            business_year=int(str(raw.get("bsns_year") or "0")),
            report_code=report_code,
            fiscal_quarter=REPORT_QUARTERS[report_code],
            metric=metric,
            account_id=account_id,
            account_name=account_name,
            statement_type=str(raw.get("sj_div") or "").strip().upper(),
            consolidation_scope=scope,
            period_start=period_start,
            period_end=period_end,
            current_amount=parse_amount(raw.get("thstrm_amount")),
            cumulative_amount=parse_amount(raw.get("thstrm_add_amount")),
            currency=currency,
        ))
    return facts


def _fact_priority(fact: DartAccountFact) -> tuple[int, int]:
    identifiers = ACCOUNT_ID_PRIORITIES[fact.metric]
    normalized_id = fact.account_id.lower()
    id_priority = identifiers.index(normalized_id) if normalized_id in identifiers else 100
    statement_priority = 0 if fact.statement_type == "IS" else 1 if fact.statement_type == "CIS" else 2
    return id_priority, statement_priority


def select_preferred_accounts(
    facts: Iterable[DartAccountFact],
) -> dict[str, dict[str, DartAccountFact]]:
    """Choose one non-mixed CFS-or-OFS account set for each company.

    A complete CFS set wins.  If CFS is present but incomplete while OFS has
    all three required metrics, OFS wins as one complete set instead of mixing
    the two scopes.  An incomplete set is returned only so the caller can
    decide which full-statement fallback is still required.
    """
    grouped: dict[str, list[DartAccountFact]] = {}
    for fact in facts:
        grouped.setdefault(fact.corp_code, []).append(fact)

    selected: dict[str, dict[str, DartAccountFact]] = {}
    for corp_code, company_facts in grouped.items():
        candidates_by_scope: dict[str, dict[str, DartAccountFact]] = {}
        for scope in ("CFS", "OFS", "NA"):
            by_metric: dict[str, list[DartAccountFact]] = {}
            for fact in company_facts:
                if fact.consolidation_scope == scope:
                    by_metric.setdefault(fact.metric, []).append(fact)
            if by_metric:
                candidates_by_scope[scope] = {
                    metric: sorted(candidates, key=_fact_priority)[0]
                    for metric, candidates in by_metric.items()
                }

        complete_scope = next(
            (scope for scope in ("CFS", "OFS", "NA")
             if all(metric in candidates_by_scope.get(scope, {}) for metric in REQUIRED_METRICS)),
            None,
        )
        fallback_scope = next(
            (scope for scope in ("CFS", "OFS", "NA") if scope in candidates_by_scope),
            None,
        )
        scope = complete_scope or fallback_scope
        selected[corp_code] = candidates_by_scope.get(scope or "", {})
    return selected


def standalone_quarter_value(
    fact: DartAccountFact,
    *,
    previous_cumulative: Decimal | None = None,
) -> Decimal | None:
    """Return the standalone-quarter value used by Earnings Momentum.

    OpenDART documents ``thstrm_amount`` as the three-month income-statement
    amount for interim reports, so it is preferred for Q2/Q3. When a standalone
    Q4 value is unavailable, each collected income-statement metric uses the
    same FY cumulative minus 9M cumulative rule.
    """
    quarter = fact.fiscal_quarter
    if quarter == 1:
        return fact.cumulative_amount if fact.cumulative_amount is not None else fact.current_amount
    if quarter in {2, 3}:
        if fact.current_amount is not None:
            return fact.current_amount
        if fact.cumulative_amount is not None and previous_cumulative is not None:
            return fact.cumulative_amount - previous_cumulative
        return None
    if quarter == 4:
        annual_amount = fact.current_amount if fact.current_amount is not None else fact.cumulative_amount
        if annual_amount is None or previous_cumulative is None:
            return None
        return annual_amount - previous_cumulative
    return None
