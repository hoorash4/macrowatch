"""Storage/derivation helpers for official KCS intra-month export snapshots."""
from __future__ import annotations

from datetime import date
from typing import Any

from common import SupabaseRest
from signals.economic_chart_pipeline import _insert_missing
from sources.korea_export_intramonth import ExportSnapshot, independent_segment_rows

RAW_TABLE = "korea_export_intramonth_snapshots"


def _raw_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("reference_month") or ""), str(row.get("stage") or "")


def insert_missing_snapshots(db: SupabaseRest, snapshots: list[ExportSnapshot], start_month: date) -> int:
    existing_rows = db.request("GET", RAW_TABLE, params={
        "select": "reference_month,stage",
        "reference_month": f"gte.{start_month.isoformat()}",
        "limit": "10000",
    }) or []
    existing = {_raw_key(row) for row in existing_rows}
    rows = []
    for snapshot in snapshots:
        key = (snapshot.reference_month.isoformat(), snapshot.stage)
        if key in existing:
            continue
        rows.append({
            "reference_month": snapshot.reference_month.isoformat(),
            "stage": snapshot.stage,
            "period_end": snapshot.period_end.isoformat(),
            "cumulative_export_musd": snapshot.cumulative_export_musd,
            "cumulative_workdays": snapshot.cumulative_workdays,
            "published_on": snapshot.published_on.isoformat() if snapshot.published_on else None,
            "source_url": snapshot.source_url,
        })
    if rows:
        db.upsert(RAW_TABLE, rows, conflict="reference_month,stage")
    return len(rows)


def read_snapshots(db: SupabaseRest, start_month: date) -> list[ExportSnapshot]:
    rows = db.request("GET", RAW_TABLE, params={
        "select": "reference_month,stage,period_end,cumulative_export_musd,cumulative_workdays,published_on,source_url",
        "reference_month": f"gte.{start_month.isoformat()}",
        "order": "reference_month.asc,stage.asc",
        "limit": "10000",
    }) or []
    result: list[ExportSnapshot] = []
    for row in rows:
        try:
            result.append(ExportSnapshot(
                stage=str(row["stage"]),
                reference_month=date.fromisoformat(str(row["reference_month"])[:10]),
                period_end=date.fromisoformat(str(row["period_end"])[:10]),
                cumulative_export_musd=float(row["cumulative_export_musd"]),
                cumulative_workdays=float(row["cumulative_workdays"]),
                published_on=date.fromisoformat(str(row["published_on"])[:10]) if row.get("published_on") else None,
                source_url=str(row["source_url"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def derive_missing_segments(db: SupabaseRest, start_month: date) -> int:
    snapshots = read_snapshots(db, start_month)
    rows = independent_segment_rows(snapshots)
    return _insert_missing(db, rows, start_month) if rows else 0
