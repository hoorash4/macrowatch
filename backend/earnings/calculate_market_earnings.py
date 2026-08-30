"""Persist compact market earnings aggregates, breadth and contribution data."""

from __future__ import annotations

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
from earnings.market_universe import (
    backfill_before_earliest_snapshot,
    quarterly_universes_from_rows,
)
from earnings.supabase_rest import SupabaseEarningsStore


INDEX_CURRENCIES = {
    "KOSPI100": "KRW",
    "KOSDAQ50": "KRW",
    "SP100": "USD",
    "NASDAQ100": "USD",
}

# Long-cycle analysis needs the post-dot-com era rather than a rolling ten-year
# window. Historical collectors may use different source methods by era, but
# market aggregates are kept from 2002Q1 onward whenever source data exists.
HISTORY_START_YEAR = 2002


def main() -> None:
    store = SupabaseEarningsStore.from_env()
    source_rows = store.list_all_quarterly_financials()
    financials = [
        row for row in financials_from_rows(source_rows)
        if (row.market_year if row.market_year is not None else row.fiscal_year) >= HISTORY_START_YEAR
    ]
    snapshot_rows = store.list_quarterly_index_snapshots()
    universes_by_index = quarterly_universes_from_rows(snapshot_rows)

    observations = [OperatingIncomeObservation(
        company_id=row.company_id,
        period=MarketQuarter(
            row.market_year if row.market_year is not None else row.fiscal_year,
            row.market_quarter if row.market_quarter is not None else row.fiscal_quarter,
        ),
        operating_income=row.values.get("operating_income"),
        currency=row.currency,
        consolidation_scope=row.consolidation_scope,
    ) for row in financials]

    calculated_at = datetime.now(timezone.utc).isoformat()
    metric_records: list[dict] = []
    breadth_records: list[dict] = []
    for index_id, currency in INDEX_CURRENCIES.items():
        quarterly_universes = universes_by_index.get(index_id, {})
        if not quarterly_universes:
            continue

        available_periods = {
            MarketQuarter(
                row.market_year if row.market_year is not None else row.fiscal_year,
                row.market_quarter if row.market_quarter is not None else row.fiscal_quarter,
            )
            for row in financials
            if row.currency == currency
        }
        quarterly_universes = backfill_before_earliest_snapshot(
            quarterly_universes,
            available_periods,
        )

        for result in calculate_market_metric_history(
            index_id=index_id,
            currency=currency,
            universes_by_period=quarterly_universes,
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
            universes_by_period={
                period: universe.company_ids
                for period, universe in quarterly_universes.items()
            },
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
        "history_start_year": HISTORY_START_YEAR,
        "indices": len(universes_by_index),
        "quarterly_snapshots": sum(len(rows) for rows in universes_by_index.values()),
        "source_quarters": len(source_rows),
        "stored_metric_rows": stored_metrics,
        "stored_breadth_rows": stored_breadth,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
