from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any

from earnings_common.dart_accounts import (
    OP_IDS, NET_IDS, OP_NAMES, NET_NAMES,
    normalize_label,
    _statement_rows,
    _preferred_statement_rows,
    _metric_row,
    _is_explicit_top_line,
    _top_line_row,
    _cumulative_amount,
    _current_period_amount,
    _standalone,
    _stored_cumulative,
    _filing_identity,
)

from earnings_common.models import FinancialFact
from earnings_common.values import HUNDRED, MAX_SEASONAL_SAMPLES, decimal_value, profit_margin, conventional_growth, update_seasonal_window


MIN_SEASONAL_SAMPLES = 2



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
    allow_annual_average: bool = True,
) -> FinancialFact | None:
    """원본 누적값을 보존하면서 한 범위의 단독 분기 실적을 만든다.

    Q1~Q3은 공시가 제공한 당해값을 보존하고 Q4만 직전 Q3 누적을
    차감한다. ``previous_rows``는 Q4의 저장 원본이 없는 경우에만
    호출자가 채우는 제한적 공급자 폴백이다.
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
    current_period = {
        "top_line": _current_period_amount(current_top),
        "operating_income": _current_period_amount(current_op),
        "net_income": _current_period_amount(current_net),
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
    used_annual_average = allow_annual_average and quarter == 4 and any(
        current_cumulative[field] is not None and previous_cumulative[field] is None
        for field in current_cumulative
    )
    source_filing_id = (
        f"annual_without_q3_average:{filing_id}"
        if used_annual_average else filing_id
    )
    return FinancialFact(
        company_id=company_id,
        fiscal_year=year,
        fiscal_quarter=quarter,
        period_end=date(year, quarter * 3, 31 if quarter in {1, 4} else 30),
        top_line=_standalone(
            current_cumulative["top_line"], previous_cumulative["top_line"], quarter,
            current_period["top_line"], allow_annual_average=allow_annual_average,
        ),
        operating_income=_standalone(
            current_cumulative["operating_income"], previous_cumulative["operating_income"], quarter,
            current_period["operating_income"], allow_annual_average=allow_annual_average,
        ),
        net_income=_standalone(
            current_cumulative["net_income"], previous_cumulative["net_income"], quarter,
            current_period["net_income"], allow_annual_average=allow_annual_average,
        ),
        currency=source_currency,
        consolidation_scope=scope,
        source_filing_id=source_filing_id,
        filing_date=filing_date,
        source_currency=source_currency,
        source_top_line_cumulative=current_cumulative["top_line"],
        source_operating_income_cumulative=current_cumulative["operating_income"],
        source_net_income_cumulative=current_cumulative["net_income"],
    )


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


