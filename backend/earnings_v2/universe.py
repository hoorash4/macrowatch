from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from .models import UniverseCandidate, UniverseMember


MARKET_TARGETS = {
    "kr_largecap": 100,
    "kr_kosdaq": 50,
    "us_largecap": 100,
    "us_nasdaq": 100,
}
MARKET_CURRENCIES = {
    "kr_largecap": "KRW",
    "kr_kosdaq": "KRW",
    "us_largecap": "USD",
    "us_nasdaq": "USD",
}


def select_final_universe(
    *,
    market_id: str,
    market_year: int,
    market_quarter: int,
    candidates: Iterable[UniverseCandidate],
    selection_method: str,
    target_count: int | None = None,
) -> list[UniverseMember]:
    """Keep only eligible, correct-currency, company-deduplicated winners."""
    if market_id not in MARKET_TARGETS:
        raise ValueError(f"Unsupported market_id: {market_id}")
    expected_currency = MARKET_CURRENCIES[market_id]
    limit = target_count or MARKET_TARGETS[market_id]
    best_by_company: dict[str, UniverseCandidate] = {}
    for candidate in candidates:
        if not candidate.eligible or candidate.currency != expected_currency:
            continue
        existing = best_by_company.get(candidate.company_id)
        if existing is None or candidate.market_cap > existing.market_cap:
            best_by_company[candidate.company_id] = candidate

    ranked = sorted(best_by_company.values(), key=lambda item: (-item.market_cap, item.company_id))[:limit]
    return [
        UniverseMember(
            market_id=market_id,
            market_year=market_year,
            market_quarter=market_quarter,
            reference_date=candidate.reference_date,
            company_id=candidate.company_id,
            market_cap_rank=index,
            market_cap=candidate.market_cap,
            currency=candidate.currency,
            selection_method=("new_listing_override" if candidate.is_new_listing else selection_method),
        )
        for index, candidate in enumerate(ranked, start=1)
    ]


def market_cap(close: Decimal, point_in_time_shares: Decimal) -> Decimal:
    if close < 0 or point_in_time_shares < 0:
        raise ValueError("close and shares must be non-negative")
    return close * point_in_time_shares

