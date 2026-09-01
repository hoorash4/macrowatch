from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable

from .financials import StatementAmount, financial_top_line, single_quarter_amount
from .open_dart import OpenDartV2Client


OP_IDS = ("dartoperatingincomeloss", "ifrsfulloperatingprofitloss")
NET_IDS = ("ifrsfullprofitloss", "dartprofitloss")
REVENUE_IDS = ("ifrsfullrevenue", "dartrevenue", "ifrsrevenue")
OP_NAMES = ("영업이익", "영업이익손실", "영업손익")
NET_NAMES = ("당기순이익", "당기순이익손실", "분기순이익", "반기순이익")
REVENUE_TOTAL_NAMES = (
    "매출액", "수익매출액", "영업수익", "영업수익합계", "총영업수익", "순영업수익",
)
REVENUE_TOKENS = ("수익", "매출", "보험료수익", "이자수익", "수수료수익")
REVENUE_EXCLUSIONS = ("비용", "원가", "손실", "차감", "지출")


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


def _decimal(value: Any) -> Decimal | None:
    text = str(value or "").replace(",", "").replace(" ", "").strip()
    if text in ("", "-", "—", "–"):
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _row_priority(row: dict[str, Any], ids: tuple[str, ...]) -> tuple[int, int]:
    account_id = _norm(row.get("account_id"))
    id_rank = ids.index(account_id) if account_id in ids else 100
    statement = str(row.get("sj_div") or "").upper()
    return id_rank, 0 if statement == "IS" else 1 if statement == "CIS" else 2


def _metric_row(
    rows: Iterable[dict[str, Any]],
    ids: tuple[str, ...],
    names: tuple[str, ...],
) -> dict[str, Any] | None:
    source = list(rows)
    by_id = [row for row in source if _norm(row.get("account_id")) in ids]
    if by_id:
        return min(by_id, key=lambda row: _row_priority(row, ids))
    accepted_names = set(names)
    by_name = [row for row in source if _norm(row.get("account_nm")) in accepted_names]
    return min(by_name, key=lambda row: _row_priority(row, ids)) if by_name else None


def _amount(row: dict[str, Any] | None, quarter: int, prior: dict[str, Any] | None) -> Decimal | None:
    if row is None:
        return None
    current = _decimal(row.get("thstrm_amount"))
    cumulative = _decimal(row.get("thstrm_add_amount"))
    previous_cumulative = _decimal(prior.get("thstrm_add_amount")) if prior else None
    if quarter == 4:
        cumulative = current if current is not None else cumulative
        current = None
    return single_quarter_amount(
        quarter,
        current_three_month=current,
        cumulative=cumulative,
        previous_cumulative=previous_cumulative,
    )


def _account_key(row: dict[str, Any]) -> tuple[str, str]:
    return _norm(row.get("account_id")), _norm(row.get("account_nm"))


def _is_revenue(name: str) -> bool:
    normalized = _norm(name)
    return (
        any(token in normalized for token in REVENUE_TOKENS)
        and not any(token in normalized for token in REVENUE_EXCLUSIONS)
    )


@dataclass(frozen=True)
class DartQuarterFinancials:
    top_line: Decimal
    operating_income: Decimal
    net_income: Decimal
    scope: str
    top_line_method: str
    source_filing_id: str
    currency: str


@dataclass(frozen=True)
class DartBatchResult:
    values: dict[str, DartQuarterFinancials]
    errors: dict[str, str]


def _group_by_company(
    rows: Iterable[dict[str, Any]],
    companies: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    grouped = {code: [] for code in companies}
    for row in rows:
        code = str(row.get("corp_code") or "").strip()
        if code in grouped:
            grouped[code].append(row)
    return grouped


def _extract_scope(
    rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
    quarter: int,
    scope: str,
) -> tuple[DartQuarterFinancials | None, str]:
    current = [row for row in rows if str(row.get("fs_div") or "").upper() == scope]
    previous = [row for row in prior_rows if str(row.get("fs_div") or "").upper() == scope]
    if not current:
        return None, f"{scope}(no_rows)"
    op_row = _metric_row(current, OP_IDS, OP_NAMES)
    net_row = _metric_row(current, NET_IDS, NET_NAMES)
    revenue_row = _metric_row(current, REVENUE_IDS, REVENUE_TOTAL_NAMES)
    prior_op = _metric_row(previous, OP_IDS, OP_NAMES)
    prior_net = _metric_row(previous, NET_IDS, NET_NAMES)
    prior_revenue = _metric_row(previous, REVENUE_IDS, REVENUE_TOTAL_NAMES)
    op = _amount(op_row, quarter, prior_op)
    net = _amount(net_row, quarter, prior_net)
    top = _amount(revenue_row, quarter, prior_revenue)
    method = "reported_total"

    # Financial companies often omit the standard revenue ID. Prefer one
    # verified total; otherwise sum positive income leaves above operating
    # income. Costs, losses and subtotals beneath operating income are excluded.
    if top is None and op_row is not None:
        try:
            op_order = int(str(op_row.get("ord") or "999999").replace(",", ""))
        except ValueError:
            op_order = 999999
        prior_by_id = {_norm(row.get("account_id")): row for row in previous if _norm(row.get("account_id"))}
        prior_by_name = {_norm(row.get("account_nm")): row for row in previous}
        candidates: list[StatementAmount] = []
        for row in current:
            if str(row.get("sj_div") or "").upper() not in {"IS", "CIS"}:
                continue
            try:
                order = int(str(row.get("ord") or "999999").replace(",", ""))
            except ValueError:
                continue
            name = str(row.get("account_nm") or "")
            if order >= op_order or not _is_revenue(name):
                continue
            account_id, account_name = _account_key(row)
            prior = prior_by_id.get(account_id) if account_id else None
            amount = _amount(row, quarter, prior or prior_by_name.get(account_name))
            if amount is None:
                continue
            candidates.append(StatementAmount(
                account_name=name,
                amount=amount,
                is_total=_norm(name) in REVENUE_TOTAL_NAMES,
                is_revenue=True,
            ))
        top, method = financial_top_line(candidates)

    if top is None or op is None or net is None:
        return None, (
            f"{scope}(top_line={'yes' if top is not None else 'no'},"
            f"op={'yes' if op is not None else 'no'},net={'yes' if net is not None else 'no'})"
        )
    representative = op_row or net_row or revenue_row or current[0]
    return DartQuarterFinancials(
        top_line=top,
        operating_income=op,
        net_income=net,
        scope=scope,
        top_line_method=method,
        source_filing_id=str(representative.get("rcept_no") or ""),
        currency=str(representative.get("currency") or "KRW").upper(),
    ), ""


def extract_quarter(
    rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
    quarter: int,
) -> tuple[DartQuarterFinancials | None, str]:
    diagnostics = []
    for scope in ("CFS", "OFS"):
        value, diagnostic = _extract_scope(rows, prior_rows, quarter, scope)
        if value is not None:
            return value, ""
        diagnostics.append(diagnostic)
    return None, ", ".join(diagnostics)


class DartFinancialCollector:
    """Use the official batch endpoint first and full statements only as fallback."""

    def __init__(self, client: OpenDartV2Client) -> None:
        self.client = client

    def collect(self, corp_codes: Iterable[str], year: int, quarter: int) -> DartBatchResult:
        companies = list(dict.fromkeys(corp_codes))
        current = _group_by_company(self.client.multi_accounts(companies, year, quarter), companies)
        values: dict[str, DartQuarterFinancials] = {}
        errors: dict[str, str] = {}
        diagnostics: dict[str, str] = {}

        # Quarterly and half-year reports expose the standalone three-month
        # income-statement amount in thstrm_amount. Resolve those values from
        # the current filing alone before requesting any prior-period data.
        for code in companies:
            value, diagnostic = extract_quarter(current[code], [], quarter)
            if value is not None:
                values[code] = value
            else:
                diagnostics[code] = diagnostic

        unresolved = [code for code in companies if code not in values]
        previous_quarter = quarter - 1 if quarter > 1 else None
        previous = {code: [] for code in companies}
        if unresolved and previous_quarter:
            # Q4 requires annual cumulative minus Q3 cumulative. For Q2/Q3,
            # this is only a compatibility fallback when a filing omitted the
            # documented standalone three-month field.
            previous.update(_group_by_company(
                self.client.multi_accounts(unresolved, year, previous_quarter),
                unresolved,
            ))
            for code in unresolved:
                value, diagnostic = extract_quarter(current[code], previous[code], quarter)
                if value is not None:
                    values[code] = value
                else:
                    diagnostics[code] = diagnostic

        for code in (company for company in companies if company not in values):
            # Only unresolved companies pay the full-statement cost. CFS is
            # evaluated first; OFS is used only as a complete, separate scope.
            fallback_current: list[dict[str, Any]] = []
            fallback_previous: list[dict[str, Any]] = []
            try:
                for scope in ("CFS", "OFS"):
                    fallback_current.extend(self.client.all_accounts(code, year, quarter, scope))
                    if previous_quarter:
                        fallback_previous.extend(
                            self.client.all_accounts(code, year, previous_quarter, scope)
                        )
                    value, diagnostic = extract_quarter(
                        fallback_current,
                        fallback_previous,
                        quarter,
                    )
                    if value is not None:
                        values[code] = value
                        break
            except Exception as error:
                errors[code] = str(error)[:180]
                continue
            if code not in values:
                errors[code] = (
                    diagnostic
                    or diagnostics.get(code)
                    or "OpenDART required values unavailable"
                )
        return DartBatchResult(values=values, errors=errors)
