"""Persist compact market earnings aggregates, breadth and contribution data."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json

from earnings.growth_metrics import financials_from_rows
from earnings.market_breadth import (
    MarketQuarter,
    OperatingIncomeObservation,
    calculate_market_earnings_history,
)
from earnings.market_metrics import (
    CALCULATION_VERSION,
    calculate_market_metric_history,
)
from earnings.supabase_rest import SupabaseEarningsStore


INDEX_CURRENCIES = {
    "KOSPI100": "KRW",
    "KOSDAQ50": "KRW",
    "SP100": "USD",
    "NASDAQ100": "USD",
}


def main() -> None:
    store = SupabaseEarningsStore.from_env()
    source_rows = store.list_all_quarterly_financials()
    financials = financials_from_rows(source_rows)
    memberships = store.list_current_index_memberships()
    universe_by_index: dict[str, set[str]] = defaultdict(set)
    for membership in memberships:
        universe_by_index[str(membership["index_id"])].add(str(membership["company_id"]))

    observations = [OperatingIncomeObservation(
        company_id=row.company_id,
        period=MarketQuarter(row.fiscal_year, row.fiscal_quarter),
        operating_income=row.values.get("operating_income"),
        currency=row.currency,
        consolidation_scope=row.consolidation_scope,
    ) for row in financials]

    calculated_at = datetime.now(timezone.utc).isoformat()
    metric_records: list[dict] = []
    breadth_records: list[dict] = []
    for index_id, currency in INDEX_CURRENCIES.items():
        universe = universe_by_index.get(index_id, set())
        if not universe:
            continue
        for result in calculate_market_metric_history(
            index_id=index_id,
            currency=currency,
            universe_company_ids=universe,
            financials=financials,
        ):
            record = result.as_record()
            record.update({
                "calculation_version": CALCULATION_VERSION,
                "calculated_at": calculated_at,
                "updated_at": calculated_at,
            })
            metric_records.append(record)
        currency_observations = [
            row for row in observations if row.currency == currency
        ]
        for result in calculate_market_earnings_history(
            index_id=index_id,
            universe_company_ids=universe,
            observations=currency_observations,
        ):
            record = result.as_record()
            record.update({
                "calculation_version": CALCULATION_VERSION,
                "calculated_at": calculated_at,
                "updated_at": calculated_at,
            })
            breadth_records.append(record)

    stored_metrics = store.upsert_market_earnings_metrics(metric_records)
    stored_breadth = store.upsert_market_earnings_breadth(breadth_records)
    print(json.dumps({
        "ok": True,
        "indices": len(universe_by_index),
        "source_quarters": len(source_rows),
        "stored_metric_rows": stored_metrics,
        "stored_breadth_rows": stored_breadth,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
