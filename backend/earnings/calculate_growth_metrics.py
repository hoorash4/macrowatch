"""Recalculate and persist all quarterly earnings growth derivatives."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from earnings.growth_metrics import (
    CALCULATION_VERSION,
    calculate_growth_metrics,
    financials_from_rows,
)
from earnings.supabase_rest import SupabaseEarningsStore


def _compact_records(metrics: list, *, calculated_at: str) -> list[dict]:
    """Pivot both in-memory profit metrics into one compact DB row per quarter."""
    records: dict[tuple[str, int, int], dict] = {}
    for metric in metrics:
        key = (metric.company_id, metric.fiscal_year, metric.fiscal_quarter)
        record = records.setdefault(key, {
            "company_id": metric.company_id,
            "fiscal_year": metric.fiscal_year,
            "fiscal_quarter": metric.fiscal_quarter,
            "source_canonical_version": metric.source_canonical_version,
            "calculation_version": CALCULATION_VERSION,
            "calculated_at": calculated_at,
            "updated_at": calculated_at,
        })
        serialized = metric.as_record()
        prefix = metric.metric
        record[f"{prefix}_yoy_pct"] = serialized["yoy_pct"]
        record[f"{prefix}_yoy_state"] = metric.yoy_state
        record[f"{prefix}_yoy_delta_pp"] = serialized["yoy_delta_pp"]
        record[f"{prefix}_qoq_raw_pct"] = serialized["qoq_raw_pct"]
        record[f"{prefix}_qoq_state"] = metric.qoq_state
        record[f"{prefix}_qoq_seasonal_baseline_pct"] = serialized[
            "qoq_seasonal_baseline_pct"
        ]
        record[f"{prefix}_qoq_seasonally_adjusted_pct"] = serialized[
            "qoq_seasonally_adjusted_pct"
        ]
        record[f"{prefix}_qoq_seasonally_adjusted_delta_pp"] = serialized[
            "qoq_seasonally_adjusted_delta_pp"
        ]
        record[f"{prefix}_qoq_seasonal_sample_count"] = metric.qoq_seasonal_sample_count
    return list(records.values())


def main() -> None:
    store = SupabaseEarningsStore.from_env()
    source_rows = store.list_all_quarterly_financials()
    derived = calculate_growth_metrics(financials_from_rows(source_rows))
    calculated_at = datetime.now(timezone.utc).isoformat()
    all_records = _compact_records(derived, calculated_at=calculated_at)
    stored_versions = store.list_growth_metric_versions()
    source_versions = {
        (str(row["company_id"]), int(row["fiscal_year"]), int(row["fiscal_quarter"])):
        int(row["canonical_version"])
        for row in source_rows
    }
    companies = {key[0] for key in source_versions}
    dirty_companies = {
        company_id for company_id in companies
        if {
            key: version for key, version in source_versions.items() if key[0] == company_id
        } != {
            key: source_version
            for key, (source_version, calculation_version) in stored_versions.items()
            if key[0] == company_id and calculation_version == CALCULATION_VERSION
        }
    }
    records = [row for row in all_records if row["company_id"] in dirty_companies]
    stored = store.upsert_growth_metrics(records)
    pruned = store.prune_growth_metrics()
    summary = {
        "ok": True,
        "source_quarters": len(source_rows),
        "derived_metric_values": len(derived),
        "stored_quarter_rows": stored,
        "pruned_invalid_rows": pruned,
        "recalculated_companies": len(dirty_companies),
        "companies": len({row["company_id"] for row in source_rows}),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    annotation = json.dumps(summary, ensure_ascii=False).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::notice title=Earnings growth metrics::{annotation}", flush=True)


if __name__ == "__main__":
    main()
