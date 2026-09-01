from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable

from .financials import single_quarter_amount
from .open_dart import OpenDartV2Client


OP_IDS = ("dartoperatingincomeloss", "ifrsfulloperatingprofitloss")
NET_IDS = ("ifrsfullprofitloss", "dartprofitloss")
REVENUE_IDS = (
    "ifrsfullrevenue",
    "ifrsfullrevenuefromcontractswithcustomers",
    "dartrevenue",
    "ifrsrevenue",
)
OP_NAMES = ("영업이익", "영업이익손실", "영업손익", "영업손실")
NET_NAMES = (
    "당기순이익", "당기순이익손실", "당기순손익", "당기순손실",
    "분기순이익", "분기순이익손실", "반기순이익", "반기순이익손실",
)
FINANCIAL_TOP_LINE_NAMES = ("순영업이익", "순영업수익", "영업수익")
TOP_LINE_WORDS = re.compile(r"^(?:매출액|매출|수익)+$")


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
    source = [
        row for row in rows
        if str(row.get("sj_div") or "").upper() in {"IS", "CIS"}
    ]
    accepted_names = set(names)
    # A standard ID is useful corroboration, but must never override an
    # unrelated label.  This prevents malformed or duplicated XBRL rows from
    # silently becoming operating profit or net income.
    by_id = [
        row for row in source
        if _norm(row.get("account_id")) in ids
        and _norm(row.get("account_nm")) in accepted_names
    ]
    if by_id:
        return min(by_id, key=lambda row: _row_priority(row, ids))
    by_name = [row for row in source if _norm(row.get("account_nm")) in accepted_names]
    return min(by_name, key=lambda row: _row_priority(row, ids)) if by_name else None


def _top_line_name_rank(name: str) -> int | None:
    normalized = _norm(name)
    if TOP_LINE_WORDS.fullmatch(normalized):
        return 0
    try:
        return 1 + FINANCIAL_TOP_LINE_NAMES.index(normalized)
    except ValueError:
        return None


def _top_line_row(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for row in rows:
        name_rank = _top_line_name_rank(str(row.get("account_nm") or ""))
        account_id = _norm(row.get("account_id"))
        if name_rank is None and account_id not in REVENUE_IDS:
            continue
        statement = str(row.get("sj_div") or "").upper()
        if statement not in {"IS", "CIS"}:
            continue
        try:
            order = int(str(row.get("ord") or "999999").replace(",", ""))
        except ValueError:
            order = 999999
        candidates.append((
            0 if account_id in REVENUE_IDS else 1,
            name_rank if name_rank is not None else 100,
            0 if statement == "IS" else 1,
            order,
            row,
        ))
    return min(candidates, key=lambda item: item[:-1])[-1] if candidates else None


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


@dataclass(frozen=True)
class DartQuarterFinancials:
    top_line: Decimal | None
    operating_income: Decimal | None
    net_income: Decimal | None
    scope: str
    source_filing_id: str
    currency: str

    @property
    def complete(self) -> bool:
        return all(value is not None for value in (
            self.top_line,
            self.operating_income,
            self.net_income,
        ))

    @property
    def available_count(self) -> int:
        return sum(value is not None for value in (
            self.top_line,
            self.operating_income,
            self.net_income,
        ))

    def fill_missing_from(self, other: "DartQuarterFinancials") -> "DartQuarterFinancials":
        if self.scope != other.scope:
            raise ValueError("CFS and OFS financial facts must never be mixed")
        return DartQuarterFinancials(
            top_line=self.top_line if self.top_line is not None else other.top_line,
            operating_income=(
                self.operating_income
                if self.operating_income is not None
                else other.operating_income
            ),
            net_income=self.net_income if self.net_income is not None else other.net_income,
            scope=self.scope,
            source_filing_id=self.source_filing_id or other.source_filing_id,
            currency=self.currency or other.currency,
        )


@dataclass(frozen=True)
class DartBatchResult:
    values: dict[str, DartQuarterFinancials]
    errors: dict[str, str]
    request_counts: dict[str, int] = field(default_factory=dict)


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
    revenue_row = _top_line_row(current)
    prior_op = _metric_row(previous, OP_IDS, OP_NAMES)
    prior_net = _metric_row(previous, NET_IDS, NET_NAMES)
    prior_revenue = _top_line_row(previous)
    op = _amount(op_row, quarter, prior_op)
    net = _amount(net_row, quarter, prior_net)
    top = _amount(revenue_row, quarter, prior_revenue)

    representative = op_row or net_row or revenue_row or current[0]
    value = DartQuarterFinancials(
        top_line=top,
        operating_income=op,
        net_income=net,
        scope=scope,
        source_filing_id=str(representative.get("rcept_no") or ""),
        currency=str(representative.get("currency") or "KRW").upper(),
    )
    diagnostic = (
        f"{scope}(top_line={'yes' if top is not None else 'no'},"
        f"op={'yes' if op is not None else 'no'},net={'yes' if net is not None else 'no'})"
    )
    return value, "" if value.complete else diagnostic


def extract_quarter(
    rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
    quarter: int,
) -> tuple[DartQuarterFinancials | None, str]:
    diagnostics = []
    partials = []
    for scope in ("CFS", "OFS"):
        value, diagnostic = _extract_scope(rows, prior_rows, quarter, scope)
        if value is not None and value.complete:
            return value, ""
        if value is not None:
            partials.append(value)
        diagnostics.append(diagnostic)
    # Keep the best partial result instead of throwing away valid fields.
    # Equal coverage preserves CFS priority because it was appended first.
    best = max(partials, key=lambda value: value.available_count) if partials else None
    return best, ", ".join(diagnostics)


def _needs_previous(rows: list[dict[str, Any]], quarter: int, scope: str) -> bool:
    if quarter == 1:
        return False
    scoped = [
        row for row in rows
        if str(row.get("fs_div") or "").upper() == scope
        and str(row.get("sj_div") or "").upper() in {"IS", "CIS"}
    ]
    selected = (
        _top_line_row(scoped),
        _metric_row(scoped, OP_IDS, OP_NAMES),
        _metric_row(scoped, NET_IDS, NET_NAMES),
    )
    for row in selected:
        if row is None:
            continue
        if quarter == 4:
            return True
        if _decimal(row.get("thstrm_amount")) is None and _decimal(row.get("thstrm_add_amount")) is not None:
            return True
    return False


def _top_line_needs_previous(
    rows: list[dict[str, Any]],
    quarter: int,
    scope: str,
) -> bool:
    if quarter == 1:
        return False
    scoped = [
        row for row in rows
        if str(row.get("fs_div") or scope).upper() == scope
        and str(row.get("sj_div") or "").upper() in {"IS", "CIS"}
    ]
    selected = _top_line_row(scoped)
    if selected is None:
        return False
    if quarter == 4:
        return True
    return (
        _decimal(selected.get("thstrm_amount")) is None
        and _decimal(selected.get("thstrm_add_amount")) is not None
    )


def _individual_top_line(
    rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
    quarter: int,
    scope: str,
) -> Decimal | None:
    current = [
        row for row in rows
        if str(row.get("fs_div") or scope).upper() == scope
        and str(row.get("sj_div") or "").upper() in {"IS", "CIS"}
    ]
    previous = [
        row for row in prior_rows
        if str(row.get("fs_div") or scope).upper() == scope
        and str(row.get("sj_div") or "").upper() in {"IS", "CIS"}
    ]
    return _amount(
        _top_line_row(current),
        quarter,
        _top_line_row(previous),
    )


class DartFinancialCollector:
    """Use batch facts first, then fetch only missing company top lines."""

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
            if value is None or not value.complete:
                diagnostics[code] = diagnostic

        unresolved = [code for code in companies if not values.get(code) or not values[code].complete]
        previous_quarter = quarter - 1 if quarter > 1 else None
        previous = {code: [] for code in companies}
        needs_batch_previous = [
            code for code in unresolved
            if previous_quarter
            and any(
                _needs_previous(current[code], quarter, scope)
                for scope in ("CFS", "OFS")
            )
        ]
        if needs_batch_previous and previous_quarter:
            # Q4 requires annual cumulative minus Q3 cumulative. For Q2/Q3,
            # this is only a compatibility fallback when a filing omitted the
            # documented standalone three-month field.
            previous.update(_group_by_company(
                self.client.multi_accounts(needs_batch_previous, year, previous_quarter),
                needs_batch_previous,
            ))
            for code in needs_batch_previous:
                value, diagnostic = extract_quarter(current[code], previous[code], quarter)
                if value is not None:
                    values[code] = value
                if value is None or not value.complete:
                    diagnostics[code] = diagnostic

        top_line_codes = [
            code for code in companies
            if (partial := values.get(code)) is not None
            and partial.top_line is None
            and partial.operating_income is not None
            and partial.net_income is not None
        ]
        current_requests = [
            (code, year, quarter, values[code].scope)
            for code in top_line_codes
        ]
        individual_current, current_errors = self.client.all_accounts_many(
            current_requests,
            workers=4,
        ) if current_requests else ({}, {})

        previous_requests = []
        for request in current_requests:
            rows = individual_current.get(request, [])
            if _top_line_needs_previous(rows, quarter, request[3]):
                previous_requests.append((request[0], year, quarter - 1, request[3]))
        individual_previous, previous_errors = self.client.all_accounts_many(
            previous_requests,
            workers=4,
        ) if previous_requests else ({}, {})

        for code in top_line_codes:
            partial = values[code]
            request = (code, year, quarter, partial.scope)
            previous_request = (code, year, quarter - 1, partial.scope)
            if request in current_errors:
                errors[code] = current_errors[request]
                continue
            if previous_request in previous_errors:
                errors[code] = previous_errors[previous_request]
                continue
            top_line = _individual_top_line(
                individual_current.get(request, []),
                individual_previous.get(previous_request, []),
                quarter,
                partial.scope,
            )
            if top_line is not None:
                values[code] = partial.fill_missing_from(DartQuarterFinancials(
                    top_line=top_line,
                    operating_income=None,
                    net_income=None,
                    scope=partial.scope,
                    source_filing_id=partial.source_filing_id,
                    currency=partial.currency,
                ))

        for code in companies:
            if not values.get(code) or not values[code].complete:
                errors.setdefault(
                    code,
                    diagnostics.get(code) or "OpenDART required values unavailable",
                )
        return DartBatchResult(
            values=values,
            errors=errors,
            request_counts={
                "batch_current": 1 if companies else 0,
                "batch_current_companies": len(companies),
                "batch_previous": 1 if needs_batch_previous else 0,
                "batch_previous_companies": len(needs_batch_previous),
                "individual_current": len(current_requests),
                "individual_previous": len(previous_requests),
                "individual_errors": len(current_errors) + len(previous_errors),
            },
        )
