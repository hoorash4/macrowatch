from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable

from .financials import single_quarter_amount
from .open_dart import OpenDartV2Client, OpenDartV2Error
from .xbrl_financials import extract_single_quarter_metrics


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


class DartFinancialCollector:
    """Use one batch request, then XBRL only for fields absent from that batch."""

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

        xbrl_codes = [
            company for company in companies
            if not values.get(company) or not values[company].complete
        ]
        receipts_by_code = {}
        prior_receipts_by_code = {}
        for code in xbrl_codes:
            partial = values.get(code)
            receipts_by_code[code] = (
                partial.source_filing_id if partial is not None else next((
                    str(row.get("rcept_no") or "") for row in current[code]
                    if row.get("rcept_no")
                ), "")
            )
            prior_receipts_by_code[code] = next((
                str(row.get("rcept_no") or "") for row in previous.get(code, [])
                if row.get("rcept_no")
            ), "")
        requested_receipts = [
            receipt for code in xbrl_codes
            for receipt in (
                receipts_by_code[code],
                prior_receipts_by_code[code] if quarter == 4 else "",
            )
            if receipt
        ]
        archives, archive_errors = (
            self.client.xbrl_archives(requested_receipts)
            if requested_receipts else ({}, {})
        )

        for code in xbrl_codes:
            # Preserve every valid batch fact. A single in-memory XBRL archive
            # replaces repeated CFS/OFS full-statement API calls.
            partial = values.get(code)
            try:
                receipt = receipts_by_code[code]
                prior_receipt = prior_receipts_by_code[code]
                if receipt not in archives:
                    raise OpenDartV2Error(archive_errors.get(receipt, "XBRL archive unavailable"))
                archive = archives[receipt]
                prior_archive = (
                    archives.get(prior_receipt)
                    if quarter == 4 and prior_receipt else None
                )
                xbrl = extract_single_quarter_metrics(
                    archive,
                    year,
                    quarter,
                    prior_quarter_payload=prior_archive,
                )
                fallback = DartQuarterFinancials(
                    top_line=xbrl.top_line,
                    operating_income=xbrl.operating_income,
                    net_income=xbrl.net_income,
                    scope=partial.scope if partial is not None else "CFS",
                    source_filing_id=receipt,
                    currency=partial.currency if partial is not None else "KRW",
                )
                candidate = partial.fill_missing_from(fallback) if partial is not None else fallback
                values[code] = candidate
            except Exception as error:
                errors[code] = str(error)[:180]
                continue
            if not values.get(code) or not values[code].complete:
                errors[code] = (
                    diagnostics.get(code)
                    or "OpenDART required values unavailable"
                )
        return DartBatchResult(values=values, errors=errors)
