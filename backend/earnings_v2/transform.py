from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any

from .models import FinancialFact, MarketFact


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
FINANCIAL_TOP_LINES = {"순영업이익", "순영업수익", "영업수익"}


def normalize_label(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


def decimal_value(value: Any) -> Decimal | None:
    text = str(value or "").replace(",", "").replace(" ", "").strip()
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
    normalized = normalize_label(name)
    if normalized in FINANCIAL_TOP_LINES:
        return True
    # 매출·매출액·수익만으로 구성된 명칭만 허용한다. 금융수익·보험수익처럼
    # 다른 의미가 붙은 하위 계정은 매출로 합산하지 않는다.
    remainder = normalized
    for token in ("매출액", "매출", "수익"):
        remainder = remainder.replace(token, "")
    return bool(normalized) and not remainder


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
    if quarter == 1:
        return decimal_value(row.get("thstrm_add_amount")) or decimal_value(row.get("thstrm_amount"))
    if quarter in {2, 3}:
        return decimal_value(row.get("thstrm_add_amount"))
    return decimal_value(row.get("thstrm_amount")) or decimal_value(row.get("thstrm_add_amount"))


def _standalone(
    current_row: dict[str, Any] | None,
    previous_row: dict[str, Any] | None,
    quarter: int,
) -> Decimal | None:
    current = _cumulative_amount(current_row, quarter)
    if current is None:
        return None
    if quarter == 1:
        return current
    previous = _cumulative_amount(previous_row, quarter - 1)
    return current - previous if previous is not None else None


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
    previous_rows: list[dict[str, Any]],
) -> FinancialFact | None:
    """한 범위의 CFS 또는 OFS만 사용해 단독 분기 실적을 만든다."""
    candidates: list[FinancialFact] = []
    for scope in ("CFS", "OFS"):
        current = _statement_rows(current_rows, scope)
        previous = _statement_rows(previous_rows, scope)
        if not current:
            continue
        current_op = _metric_row(current, OP_IDS, OP_NAMES)
        current_net = _metric_row(current, NET_IDS, NET_NAMES)
        current_top = _top_line_row(current)
        previous_op = _metric_row(previous, OP_IDS, OP_NAMES)
        previous_net = _metric_row(previous, NET_IDS, NET_NAMES)
        previous_top = _top_line_row(previous)
        filing_id, filing_date = _filing_identity(current, year, quarter, corp_code)
        representative = current_op or current_net or current_top or current[0]
        currency = str(representative.get("currency") or "KRW").strip().upper()
        fact = FinancialFact(
            company_id=company_id,
            fiscal_year=year,
            fiscal_quarter=quarter,
            period_end=date(year, quarter * 3, 31 if quarter in {1, 4} else 30),
            top_line=_standalone(current_top, previous_top, quarter),
            operating_income=_standalone(current_op, previous_op, quarter),
            net_income=_standalone(current_net, previous_net, quarter),
            currency=currency,
            consolidation_scope=scope,
            source_filing_id=filing_id,
            filing_date=filing_date,
        )
        candidates.append(fact)
    if not candidates:
        return None
    # 동일 확보 개수면 연결을 우선한다. 서로 다른 범위의 필드를 합치지 않는다.
    return max(
        candidates,
        key=lambda row: (
            sum(value is not None for value in (row.top_line, row.operating_income, row.net_income)),
            1 if row.consolidation_scope == "CFS" else 0,
        ),
    )


def profit_margin(profit: Decimal | None, top_line: Decimal | None) -> Decimal | None:
    if profit is None or top_line is None or top_line == 0:
        return None
    return profit / top_line * HUNDRED


def conventional_growth(current: Decimal | None, previous: Decimal | None) -> tuple[Decimal | None, str]:
    if current is None or previous is None:
        return None, "missing_prior"
    if previous == 0:
        return None, "from_zero"
    if previous < 0:
        if current >= 0:
            return None, "black_turn"
        if current > previous:
            return None, "loss_narrowing"
        if current < previous:
            return None, "loss_widening"
        return None, "loss_unchanged"
    if current < 0:
        return None, "red_turn"
    return (current - previous) / previous * HUNDRED, "normal"


def _ordinal(year: int, quarter: int) -> int:
    return year * 4 + quarter - 1


def calculate_financial_series(rows: Iterable[FinancialFact]) -> list[FinancialFact]:
    ordered = sorted(rows, key=lambda row: row.key)
    by_key = {row.key: row for row in ordered}
    result: list[FinancialFact] = []
    for index, row in enumerate(ordered):
        updates: dict[str, Any] = {
            "operating_margin_pct": profit_margin(row.operating_income, row.top_line),
            "net_margin_pct": profit_margin(row.net_income, row.top_line),
        }
        for field, prefix in (("operating_income", "operating_income"), ("net_income", "net_income")):
            prior_year = by_key.get((row.fiscal_year - 1, row.fiscal_quarter))
            compatible_year = prior_year is not None and row.currency == prior_year.currency and row.consolidation_scope == prior_year.consolidation_scope
            yoy = conventional_growth(getattr(row, field), getattr(prior_year, field) if compatible_year else None)
            if prior_year is not None and not compatible_year:
                yoy = (None, "currency_mismatch" if row.currency != prior_year.currency else "scope_mismatch")
            updates[f"{prefix}_yoy_pct"], updates[f"{prefix}_yoy_state"] = yoy

            previous = ordered[index - 1] if index else None
            consecutive = previous is not None and _ordinal(row.fiscal_year, row.fiscal_quarter) - _ordinal(previous.fiscal_year, previous.fiscal_quarter) == 1
            compatible = consecutive and row.currency == previous.currency and row.consolidation_scope == previous.consolidation_scope
            raw = conventional_growth(getattr(row, field), getattr(previous, field) if compatible else None)
            if previous is not None and consecutive and not compatible:
                raw = (None, "currency_mismatch" if row.currency != previous.currency else "scope_mismatch")
            if raw[1] != "normal" or raw[0] is None:
                qoq = raw
            else:
                samples: list[Decimal] = []
                # 목표 시점 이전의 동일 분기 전환만 사용한다. 최근 최대 10년이며
                # 두 표본보다 적으면 계절조정값을 만들지 않는다.
                for candidate_index in range(1, index):
                    candidate = ordered[candidate_index]
                    prior = ordered[candidate_index - 1]
                    if candidate.fiscal_quarter != row.fiscal_quarter:
                        continue
                    if _ordinal(candidate.fiscal_year, candidate.fiscal_quarter) - _ordinal(prior.fiscal_year, prior.fiscal_quarter) != 1:
                        continue
                    value, state = conventional_growth(getattr(candidate, field), getattr(prior, field))
                    if state == "normal" and value is not None:
                        samples.append(value)
                samples = samples[-MAX_SEASONAL_SAMPLES:]
                qoq = (raw[0] - Decimal(str(median(samples))), "normal") if len(samples) >= MIN_SEASONAL_SAMPLES else (None, "insufficient_history")
            updates[f"{prefix}_qoq_sa_pct"], updates[f"{prefix}_qoq_state"] = qoq
        quality = "complete" if row.fully_complete else "review_required"
        result.append(row.with_changes(quality_status=quality, **updates))
    return result


def aggregate_market(
    market_id: str,
    year: int,
    quarter: int,
    rows: Iterable[FinancialFact],
    target_count: int,
    *,
    historical: bool = False,
) -> MarketFact:
    values = [row for row in rows if row.profit_complete]
    actual = len(values)
    average_op = sum((row.operating_income for row in values if row.operating_income is not None), Decimal(0)) / actual if actual else None
    average_net = sum((row.net_income for row in values if row.net_income is not None), Decimal(0)) / actual if actual else None
    all_top_lines = actual == target_count and all(row.top_line is not None for row in values)
    top_sum = sum((row.top_line for row in values if row.top_line is not None), Decimal(0)) if all_top_lines else None
    op_sum = sum((row.operating_income for row in values if row.operating_income is not None), Decimal(0)) if all_top_lines else None
    net_sum = sum((row.net_income for row in values if row.net_income is not None), Decimal(0)) if all_top_lines else None
    status = "complete" if actual == target_count else ("historical_partial" if historical else "incomplete")
    return MarketFact(
        market_id, year, quarter, average_op, average_net,
        profit_margin(op_sum, top_sum), profit_margin(net_sum, top_sum),
        actual, target_count, status,
    )


def calculate_market_series(rows: Iterable[MarketFact]) -> list[MarketFact]:
    ordered = sorted(rows, key=lambda row: row.key)
    synthetic = [
        FinancialFact(
            company_id=row.market_id, fiscal_year=row.market_year, fiscal_quarter=row.market_quarter,
            period_end=date(row.market_year, row.market_quarter * 3, 1), top_line=None,
            operating_income=row.average_operating_income, net_income=row.average_net_income,
            currency="UNIT", consolidation_scope="CFS", source_filing_id="market", filing_date=date(row.market_year, 1, 1),
        )
        for row in ordered
    ]
    metrics = calculate_financial_series(synthetic)
    return [
        source.with_changes(
            operating_income_yoy_pct=metric.operating_income_yoy_pct,
            operating_income_yoy_state=metric.operating_income_yoy_state,
            net_income_yoy_pct=metric.net_income_yoy_pct,
            net_income_yoy_state=metric.net_income_yoy_state,
            operating_income_qoq_sa_pct=metric.operating_income_qoq_sa_pct,
            operating_income_qoq_state=metric.operating_income_qoq_state,
            net_income_qoq_sa_pct=metric.net_income_qoq_sa_pct,
            net_income_qoq_state=metric.net_income_qoq_state,
        )
        for source, metric in zip(ordered, metrics, strict=True)
    ]
