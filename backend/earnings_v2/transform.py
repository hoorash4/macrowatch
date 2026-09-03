from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any

from .models import FinancialFact


HUNDRED = Decimal("100")
MAX_SEASONAL_SAMPLES = 10
MIN_SEASONAL_SAMPLES = 2
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


def decimal_value(value: Any) -> Decimal | None:
    text = "" if value is None else str(value).replace(",", "").replace(" ", "").strip()
    if text in {"", "-", "—", "–"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


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
    # OpenDART와 KIS의 보완 경로를 같은 계약으로 맞춘다. 원천의 당기값은
    # 사용하지 않고, 연초부터의 누적값만 받아 직전 누적값을 차감한다.
    # 1분기는 당기와 누적 기간이 같고, 일부 과거 공시는 누적 칼럼을 비운 채
    # thstrm_amount에만 값을 제공한다. 누적 칼럼을 우선하되 비어 있으면
    # 당기 칼럼을 같은 1분기 누적값으로 사용한다.
    if quarter == 1:
        cumulative = decimal_value(row.get("thstrm_add_amount"))
        return cumulative if cumulative is not None else decimal_value(row.get("thstrm_amount"))
    # 연간보고서에는 연간 누적값이 thstrm_amount로 제공된다.
    source_field = "thstrm_amount" if quarter == 4 else "thstrm_add_amount"
    return decimal_value(row.get(source_field))


def _standalone(current: Decimal | None, previous: Decimal | None, quarter: int) -> Decimal | None:
    if current is None:
        return None
    if quarter == 1:
        return current
    return current - previous if previous is not None else None


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


def extract_company_fact(
    corp_code: str,
    company_id: str,
    year: int,
    quarter: int,
    current_rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]] | None = None,
    *,
    previous_fact: FinancialFact | None = None,
    consolidation_scope: str | None = None,
) -> FinancialFact | None:
    """원본 누적값을 보존하면서 한 범위의 단독 분기 실적을 만든다.

    정상 백필·증분 실행은 직전 분기에 저장한 누적 원본을 사용한다.
    ``previous_rows``는 과거 기업군에 없던 기업처럼 저장 원본이 없는
    경우에만 호출자가 채우는 제한적 공급자 폴백이다.
    """
    previous_rows = previous_rows or []
    scope, current = _preferred_statement_rows(current_rows, consolidation_scope)
    if scope is None:
        return None
    _, previous = _preferred_statement_rows(previous_rows)
    current_op = _metric_row(current, OP_IDS, OP_NAMES)
    current_net = _metric_row(current, NET_IDS, NET_NAMES)
    current_top = _top_line_row(current)
    previous_op = _metric_row(previous, OP_IDS, OP_NAMES)
    previous_net = _metric_row(previous, NET_IDS, NET_NAMES)
    previous_top = _top_line_row(previous)
    filing_id, filing_date = _filing_identity(current, year, quarter, corp_code)
    representative = current_op or current_net or current_top or current[0]
    source_currency = str(representative.get("currency") or "KRW").strip().upper()
    current_cumulative = {
        "top_line": _cumulative_amount(current_top, quarter),
        "operating_income": _cumulative_amount(current_op, quarter),
        "net_income": _cumulative_amount(current_net, quarter),
    }
    fallback_previous = {
        "top_line": _cumulative_amount(previous_top, quarter - 1),
        "operating_income": _cumulative_amount(previous_op, quarter - 1),
        "net_income": _cumulative_amount(previous_net, quarter - 1),
    } if quarter > 1 else {"top_line": None, "operating_income": None, "net_income": None}
    previous_cumulative = {}
    for field in current_cumulative:
        stored_value = _stored_cumulative(
            previous_fact, field, source_currency=source_currency,
        )
        previous_cumulative[field] = (
            stored_value if stored_value is not None else fallback_previous[field]
        )
    return FinancialFact(
        company_id=company_id,
        fiscal_year=year,
        fiscal_quarter=quarter,
        period_end=date(year, quarter * 3, 31 if quarter in {1, 4} else 30),
        top_line=_standalone(current_cumulative["top_line"], previous_cumulative["top_line"], quarter),
        operating_income=_standalone(current_cumulative["operating_income"], previous_cumulative["operating_income"], quarter),
        net_income=_standalone(current_cumulative["net_income"], previous_cumulative["net_income"], quarter),
        currency=source_currency,
        consolidation_scope=scope,
        source_filing_id=filing_id,
        filing_date=filing_date,
        source_currency=source_currency,
        source_top_line_cumulative=current_cumulative["top_line"],
        source_operating_income_cumulative=current_cumulative["operating_income"],
        source_net_income_cumulative=current_cumulative["net_income"],
    )


def profit_margin(profit: Decimal | None, top_line: Decimal | None) -> Decimal | None:
    """개별 기업 이익률. 매출이 0 이하이면 값은 보존하되 비율은 계산하지 않는다."""
    if profit is None or top_line is None or top_line <= 0:
        return None
    return profit / top_line * HUNDRED


def conventional_growth(current: Decimal | None, previous: Decimal | None) -> tuple[Decimal | None, str]:
    if current is None or previous is None:
        return None, "missing_prior"
    if previous == 0:
        return None, "from_zero"
    if previous < 0 < current:
        return None, "black_turn"
    if previous > 0 > current:
        return None, "red_turn"
    # 적자 지속도 방향을 유지한 채 계산한다. -100 -> -70은 +30%,
    # -100 -> -130은 -30%다. 부호 전환만 비율 대신 상태로 남긴다.
    return (current - previous) / abs(previous) * HUNDRED, "normal"


def _ordinal(year: int, quarter: int) -> int:
    return year * 4 + quarter - 1


def calculate_financial_point(
    row: FinancialFact,
    *,
    previous: FinancialFact | None,
    prior_year: FinancialFact | None,
    seasonal_samples: dict[str, list[Decimal]] | None = None,
) -> tuple[FinancialFact, dict[str, Decimal | None]]:
    """한 분기만 계산하고, 다음 계절창에 넣을 원시 QoQ를 함께 반환한다.

    ``seasonal_samples``에는 현재 분기보다 앞선 동일 계절 전환값만 들어온다.
    따라서 신규 공시 처리에서는 과거 분기 전체를 다시 계산할 필요가 없다.
    """
    samples_by_metric = seasonal_samples or {}
    updates: dict[str, Any] = {
        "operating_margin_pct": profit_margin(row.operating_income, row.top_line),
        "net_margin_pct": profit_margin(row.net_income, row.top_line),
    }
    raw_samples: dict[str, Decimal | None] = {}
    for field, prefix in (("operating_income", "operating_income"), ("net_income", "net_income")):
        compatible_year = (
            prior_year is not None
            and row.currency == prior_year.currency
        )
        yoy = conventional_growth(getattr(row, field), getattr(prior_year, field) if compatible_year else None)
        if prior_year is not None and not compatible_year:
            yoy = (None, "currency_mismatch")
        updates[f"{prefix}_yoy_pct"], updates[f"{prefix}_yoy_state"] = yoy

        consecutive = (
            previous is not None
            and _ordinal(row.fiscal_year, row.fiscal_quarter)
            - _ordinal(previous.fiscal_year, previous.fiscal_quarter) == 1
        )
        compatible = (
            consecutive
            and row.currency == previous.currency
        )
        raw = conventional_growth(getattr(row, field), getattr(previous, field) if compatible else None)
        if previous is not None and consecutive and not compatible:
            raw = (None, "currency_mismatch")
        raw_samples[prefix] = raw[0] if raw[1] == "normal" else None
        if raw[1] != "normal" or raw[0] is None:
            qoq = raw
        else:
            samples = samples_by_metric.get(prefix, [])[-MAX_SEASONAL_SAMPLES:]
            qoq = (
                (raw[0] - Decimal(str(median(samples))), "normal")
                if len(samples) >= MIN_SEASONAL_SAMPLES
                else (None, "insufficient_history")
            )
        updates[f"{prefix}_qoq_sa_pct"], updates[f"{prefix}_qoq_state"] = qoq
    return row.with_changes(is_pending=row.is_pending or not row.fully_complete, **updates), raw_samples


def calculate_financial_series(rows: Iterable[FinancialFact]) -> list[FinancialFact]:
    ordered = sorted(rows, key=lambda row: row.key)
    by_key = {row.key: row for row in ordered}
    result: list[FinancialFact] = []
    windows: dict[tuple[str, int], list[Decimal]] = defaultdict(list)
    for row in ordered:
        samples = {
            prefix: windows[(prefix, row.fiscal_quarter)]
            for prefix in ("operating_income", "net_income")
        }
        calculated, raw = calculate_financial_point(
            row,
            previous=by_key.get(previous_period_key(row.fiscal_year, row.fiscal_quarter)),
            prior_year=by_key.get((row.fiscal_year - 1, row.fiscal_quarter)),
            seasonal_samples=samples,
        )
        result.append(calculated)
        for prefix, value in raw.items():
            if value is not None:
                window = windows[(prefix, row.fiscal_quarter)]
                window.append(value)
                del window[:-MAX_SEASONAL_SAMPLES]
    return result


def previous_period_key(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def update_seasonal_window(
    sample_years: Iterable[int],
    sample_values: Iterable[Decimal],
    *,
    year: int,
    value: Decimal | None,
) -> tuple[list[int], list[Decimal]]:
    """같은 연도의 표본을 교체하고 최근 10개만 남긴다.

    정정으로 정상 QoQ가 아니게 된 경우 ``value=None``을 전달하면 해당
    연도 표본이 제거된다. 원본 분기 실적은 이 캐시 정리와 무관하게 보존한다.
    """
    samples = {int(sample_year): sample_value for sample_year, sample_value in zip(sample_years, sample_values)}
    if value is None:
        samples.pop(year, None)
    else:
        samples[year] = value
    retained = sorted(samples.items())[-MAX_SEASONAL_SAMPLES:]
    return [sample_year for sample_year, _ in retained], [sample_value for _, sample_value in retained]

