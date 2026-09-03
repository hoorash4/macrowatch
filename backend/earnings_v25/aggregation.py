from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal

from .models import CompanyIdentity, FinancialFact, MarketFact
from .transform import calculate_financial_point, calculate_financial_series


ZERO = Decimal(0)


def _aggregate_margin(profit: Decimal | None, top_line: Decimal | None) -> Decimal | None:
    """시장 이익률은 기업별 비율 평균이 아니라 합산 금액으로 계산한다."""
    if profit is None or top_line is None or top_line == 0:
        return None
    return profit / top_line * Decimal(100)


def _totals(rows: Iterable[FinancialFact]) -> tuple[Decimal, Decimal, Decimal]:
    rows = list(rows)
    return (
        sum((row.top_line for row in rows if row.top_line is not None), ZERO),
        sum((row.operating_income for row in rows if row.operating_income is not None), ZERO),
        sum((row.net_income for row in rows if row.net_income is not None), ZERO),
    )


def _complete(fact: FinancialFact | None) -> bool:
    return fact is not None and fact.fully_complete and not fact.is_pending


def aggregate_market(
    market_id: str,
    year: int,
    quarter: int,
    current_members: Iterable[CompanyIdentity],
    current_facts: Mapping[str, FinancialFact],
    target_count: int,
    *,
    comparison_members: Iterable[CompanyIdentity] = (),
    comparison_facts: Mapping[str, FinancialFact] | None = None,
) -> MarketFact:
    """현재 분기의 확정값 또는 발표 진행 중인 잠정값을 만든다.

    확정값은 현재 기업군의 실제 실적만 합산한다. 잠정값은 비교 기업군의
    완성된 총합에서 시작해, 유지 기업은 자기 값으로, 신규진입 기업은
    순위순으로 대응한 탈락 기업 값으로 대체한다. 이 매칭은 잠정 경로에만
    쓰이며 현재 기업군이 모두 완료되면 계산에서 완전히 사라진다.
    """
    members = sorted(current_members, key=lambda row: row.rank)
    if len(members) != target_count:
        raise ValueError(f"{market_id} universe is {len(members)}/{target_count}")
    reference_date = members[0].reference_date if members else date(year, quarter * 3, 1)
    reported = sum(_complete(current_facts.get(row.company_id)) for row in members)
    pending = target_count - reported

    if pending == 0:
        selected = [current_facts[row.company_id] for row in members]
        top, operating, net = _totals(selected)
        status = "complete"
    else:
        previous_members = sorted(comparison_members, key=lambda row: row.rank)
        previous_facts = comparison_facts or {}
        if len(previous_members) != target_count:
            # 최초 기준 분기에는 비교 바구니가 없다. 확보된 실제값만으로
            # 잠정 합계를 만들며, 없는 자리를 임의 값으로 채우지 않는다.
            provisional = [
                current_facts[row.company_id]
                for row in members if _complete(current_facts.get(row.company_id))
            ]
        else:
            current_ids = {row.company_id for row in members}
            previous_ids = {row.company_id for row in previous_members}
            entrants = [row for row in members if row.company_id not in previous_ids]
            exits = [row for row in previous_members if row.company_id not in current_ids]
            if len(entrants) != len(exits):
                raise ValueError("fixed-size universe must have equal entrant and exit counts")
            exit_for_entrant = {
                entrant.company_id: departed.company_id
                for entrant, departed in zip(entrants, exits, strict=True)
            }

            provisional = []
            for member in members:
                current = current_facts.get(member.company_id)
                if _complete(current):
                    provisional.append(current)
                    continue
                placeholder_id = member.company_id if member.company_id in previous_ids else exit_for_entrant[member.company_id]
                placeholder = previous_facts.get(placeholder_id)
                if _complete(placeholder):
                    provisional.append(placeholder)
        if not provisional:
            return MarketFact(
                market_id, year, quarter, reference_date,
                None, None, None, None, None,
                reported, pending, target_count, "collecting",
            )
        top, operating, net = _totals(provisional)
        status = "provisional"

    return MarketFact(
        market_id, year, quarter, reference_date,
        top, operating, net,
        _aggregate_margin(operating, top), _aggregate_margin(net, top),
        reported, pending, target_count, status,
    )


def calculate_market_series(rows: Iterable[MarketFact]) -> list[MarketFact]:
    """시장 총합의 YoY와 계절조정 QoQ를 기업 계산식과 동일하게 적용한다."""
    ordered = sorted(rows, key=lambda row: row.key)
    synthetic = [
        FinancialFact(
            company_id=row.market_id,
            fiscal_year=row.market_year,
            fiscal_quarter=row.market_quarter,
            period_end=row.reference_date,
            top_line=row.top_line_total,
            operating_income=row.operating_income_total,
            net_income=row.net_income_total,
            currency="UNIT",
            consolidation_scope="CFS",
            source_filing_id="market",
            filing_date=row.reference_date,
            is_pending=row.completion_status == "collecting",
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


def calculate_market_point(
    row: MarketFact,
    *,
    previous: MarketFact | None,
    prior_year: MarketFact | None,
    seasonal_samples: dict[str, list[Decimal]] | None = None,
) -> tuple[MarketFact, dict[str, Decimal | None]]:
    """시장 합계 한 분기만 기업과 동일한 산식으로 계산한다."""
    def synthetic(source: MarketFact | None) -> FinancialFact | None:
        if source is None:
            return None
        return FinancialFact(
            company_id=source.market_id,
            fiscal_year=source.market_year,
            fiscal_quarter=source.market_quarter,
            period_end=source.reference_date,
            top_line=source.top_line_total,
            operating_income=source.operating_income_total,
            net_income=source.net_income_total,
            currency="UNIT",
            consolidation_scope="CFS",
            source_filing_id="market",
            filing_date=source.reference_date,
            is_pending=source.completion_status == "collecting",
        )

    metric, raw_samples = calculate_financial_point(
        synthetic(row),
        previous=synthetic(previous),
        prior_year=synthetic(prior_year),
        seasonal_samples=seasonal_samples,
    )
    return row.with_changes(
        operating_income_yoy_pct=metric.operating_income_yoy_pct,
        operating_income_yoy_state=metric.operating_income_yoy_state,
        net_income_yoy_pct=metric.net_income_yoy_pct,
        net_income_yoy_state=metric.net_income_yoy_state,
        operating_income_qoq_sa_pct=metric.operating_income_qoq_sa_pct,
        operating_income_qoq_state=metric.operating_income_qoq_state,
        net_income_qoq_sa_pct=metric.net_income_qoq_sa_pct,
        net_income_qoq_state=metric.net_income_qoq_state,
    ), raw_samples
