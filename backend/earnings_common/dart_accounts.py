"""DART account selection and raw amount interpretation."""
from __future__ import annotations
import re
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any
from .models import FinancialFact
from .values import decimal_value

OP_IDS = {"dartoperatingincomeloss", "ifrsfulloperatingprofitloss"}
NET_IDS = {"ifrsfullprofitloss", "dartprofitloss"}
REVENUE_IDS = {
    "ifrsfullrevenue", "ifrsfullrevenuefromcontractswithcustomers",
    "dartrevenue", "ifrsrevenue",
}
OP_NAMES = {"영업이익", "영업이익손실", "영업손익", "영업손실"}
NET_NAMES = {
    "당기순이익", "당기순이익손실", "당기순손익", "당기순손실",
    "분기순이익", "분기순이익손실", "반기순이익", "반기순이익손실",
}
TOP_LINE_NAMES = {"매출액", "매출", "수익", "영업수익"}

def normalize_label(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())

def _statement_rows(rows: Iterable[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if str(row.get("fs_div") or "").upper() == scope
        and str(row.get("sj_div") or "").upper() in {"IS", "CIS"}
    ]

def _preferred_statement_rows(
    rows: Iterable[dict[str, Any]],
    required_scope: str | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """연결을 우선하고 연결 손익계산서가 없을 때만 별도를 선택한다."""
    scopes = (required_scope,) if required_scope in {"CFS", "OFS"} else ("CFS", "OFS")
    materialized = list(rows)
    for scope in scopes:
        selected = _statement_rows(materialized, scope)
        if selected:
            return scope, selected
    return None, []

def _metric_row(
    rows: Iterable[dict[str, Any]],
    accepted_ids: set[str],
    accepted_names: set[str],
) -> dict[str, Any] | None:
    candidates = []
    for row in rows:
        account_id = normalize_label(row.get("account_id"))
        name = normalize_label(row.get("account_nm"))
        if name not in accepted_names:
            continue
        statement_rank = 0 if str(row.get("sj_div") or "").upper() == "IS" else 1
        candidates.append((0 if account_id in accepted_ids else 1, statement_rank, row))
    return min(candidates, key=lambda item: item[:-1])[-1] if candidates else None

def _is_explicit_top_line(name: str) -> bool:
    # 공백, 괄호, 로마 숫자 같은 표시용 문자를 제거한 뒤 총액 계정명과
    # 정확히 일치할 때만 허용한다. 금융수익·보험수익·이자수익 같은
    # 구성 항목은 표준 Revenue ID가 붙어 있어도 매출로 추론하지 않는다.
    return normalize_label(name) in TOP_LINE_NAMES

def _top_line_row(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for row in rows:
        account_id = normalize_label(row.get("account_id"))
        explicit = _is_explicit_top_line(str(row.get("account_nm") or ""))
        # 표준 ID만으로 이름이 다른 하위 수익 계정을 매출로 승격하지 않는다.
        # 사용자가 확정한 대로 명칭 자체가 허용된 총액 계정이어야 한다.
        if not explicit:
            continue
        try:
            order = int(str(row.get("ord") or "999999").replace(",", ""))
        except ValueError:
            order = 999999
        candidates.append((0 if account_id in REVENUE_IDS else 1, order, row))
    return min(candidates, key=lambda item: item[:-1])[-1] if candidates else None

def _cumulative_amount(row: dict[str, Any] | None, quarter: int) -> Decimal | None:
    if row is None:
        return None
    # 누적 칼럼을 읽는다. 당해값은 _current_period_amount에서 별도로 읽는다.
    # 1분기는 당기와 누적 기간이 같고, 일부 과거 공시는 누적 칼럼을 비운 채
    # thstrm_amount에만 값을 제공한다. 누적 칼럼을 우선하되 비어 있으면
    # 당기 칼럼을 같은 1분기 누적값으로 사용한다.
    if quarter == 1:
        cumulative = decimal_value(row.get("thstrm_add_amount"))
        return cumulative if cumulative is not None else decimal_value(row.get("thstrm_amount"))
    # 연간보고서에는 연간 누적값이 thstrm_amount로 제공된다.
    source_field = "thstrm_amount" if quarter == 4 else "thstrm_add_amount"
    return decimal_value(row.get(source_field))

def _current_period_amount(row: dict[str, Any] | None) -> Decimal | None:
    return decimal_value(row.get("thstrm_amount")) if row is not None else None

def _standalone(
    current: Decimal | None,
    previous: Decimal | None,
    quarter: int,
    reported_current: Decimal | None,
    *,
    allow_annual_average: bool,
) -> Decimal | None:
    if quarter == 1:
        return reported_current if reported_current is not None else current
    # 분·반기보고서는 당해 분기와 누적을 모두 제공한다. Q2·Q3은 누적
    # 차감으로 다시 만들지 않고 공시가 제공한 당해값을 그대로 보존한다.
    if quarter in {2, 3}:
        return reported_current
    if current is None:
        return None
    if previous is not None:
        return current - previous
    # 4분기 연간보고서는 단독 분기 칼럼이 없다. 연간 / 4 근사치는
    # 과거 백필에서만 허용하고 자동 수집은 null/관리자 검토로 남긴다.
    return current / Decimal(4) if allow_annual_average else None

def _stored_cumulative(
    previous_fact: FinancialFact | None,
    field: str,
    *,
    source_currency: str,
) -> Decimal | None:
    if previous_fact is None:
        return None
    if previous_fact.source_currency != source_currency:
        return None
    return getattr(previous_fact, f"source_{field}_cumulative")

def _filing_identity(rows: list[dict[str, Any]], year: int, quarter: int, corp_code: str) -> tuple[str, date]:
    receipts = sorted({str(row.get("rcept_no") or "") for row in rows if re.fullmatch(r"\d{14}", str(row.get("rcept_no") or ""))})
    if receipts:
        return receipts[-1], date.fromisoformat(f"{receipts[-1][:4]}-{receipts[-1][4:6]}-{receipts[-1][6:8]}")
    # 주요계정 API가 접수번호를 생략하는 경우에도 요청 단위를 재현할 수 있는
    # 결정적 식별자를 쓴다. 날짜는 보고 대상 기간 종료일이며 행은 검토 상태가 된다.
    month = quarter * 3
    end = date(year, month, 31 if month in {3, 12} else 30)
    return f"dart-major:{corp_code}:{year}:Q{quarter}", end
