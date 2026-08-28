"""Recalculate and persist company earnings/price disparity rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json

from earnings.company_price_gap import (
    calculate_company_price_gaps,
    operating_income_from_rows,
    prices_from_rows,
)
from earnings.supabase_rest import SupabaseEarningsStore


def main() -> None:
    store = SupabaseEarningsStore.from_env()
    financial_rows = store.list_all_quarterly_financials()
    price_rows = store.list_all_quarterly_prices()
    operating_income = operating_income_from_rows(financial_rows)
    prices = prices_from_rows(price_rows)
    ops_by_company = defaultdict(list)
    prices_by_company = defaultdict(list)
    for row in operating_income:
        ops_by_company[row.company_id].append(row)
    for row in prices:
        prices_by_company[row.company_id].append(row)

    calculated_at = datetime.now(timezone.utc).isoformat()
    records = []
    for company_id in sorted(prices_by_company):
        for result in calculate_company_price_gaps(
            company_id=company_id,
            operating_income=ops_by_company.get(company_id, []),
            prices=prices_by_company[company_id],
        ):
            record = result.as_record()
            record.update({"calculated_at": calculated_at, "updated_at": calculated_at})
            records.append(record)
    stored = store.upsert_company_price_gaps(records)
    summary = {
        "ok": True,
        "financial_quarters": len(financial_rows),
        "price_quarters": len(price_rows),
        "companies_with_prices": len(prices_by_company),
        "stored_gap_rows": stored,
        "normal_gap_rows": sum(row["calculation_state"] == "normal" for row in records),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
