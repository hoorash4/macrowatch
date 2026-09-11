"""Reuse observations already stored by other MacroWatch collectors.

This is a read-model mirror only: it never calls the upstream provider and never mutates the
source tables.  It lets the economic-chart page use one table without duplicating collection.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from common import SupabaseRest
from signals.economic_chart_pipeline import _insert_missing

PAGE = 1000
TABLE = "economic_chart_points"


def _paged_rows(db: SupabaseRest, table: str, select: str, order: str, filters: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {"select": select, "order": f"{order}.asc", "limit": str(PAGE), "offset": str(offset), **filters}
        rows = db.request("GET", table, params=params) or []
        out.extend(row for row in rows if isinstance(row, dict))
        if len(rows) < PAGE:
            break
        offset += PAGE
    return out


def mirror_existing_series(db: SupabaseRest, start: date) -> dict[str, int]:
    counts: dict[str, int] = {}

    rrp_rows = _paged_rows(
        db, "liquidity_observations", "observation_date,value", "observation_date",
        {"country": "eq.US", "series": "eq.rrp", "observation_date": f"gte.{start.isoformat()}"},
    )
    counts["RRP"] = _insert_missing(db, [{
        "series_code": "RRP", "observation_date": str(row["observation_date"]),
        "value": float(row["value"]), "frequency": "D", "source": "MACROWATCH:liquidity_observations/US/rrp",
    } for row in rrp_rows if row.get("observation_date") and row.get("value") is not None], start)

    tga_rows = _paged_rows(
        db, "liquidity_observations", "observation_date,value", "observation_date",
        {"country": "eq.US", "series": "eq.tga", "observation_date": f"gte.{start.isoformat()}"},
    )
    counts["TGA"] = _insert_missing(db, [{
        "series_code": "TGA", "observation_date": str(row["observation_date"]),
        "value": float(row["value"]), "frequency": "W", "source": "MACROWATCH:liquidity_observations/US/tga",
    } for row in tga_rows if row.get("observation_date") and row.get("value") is not None], start)

    fx_rows = _paged_rows(
        db, "korea_foreign_flow_raw", "observation_date,usdkrw_rate", "observation_date",
        {"observation_date": f"gte.{start.isoformat()}", "usdkrw_rate": "not.is.null"},
    )
    counts["USDKRW"] = _insert_missing(db, [{
        "series_code": "USDKRW", "observation_date": str(row["observation_date"]),
        "value": float(row["usdkrw_rate"]), "frequency": "D", "source": "MACROWATCH:korea_foreign_flow_raw/usdkrw_rate",
    } for row in fx_rows if row.get("observation_date") and row.get("usdkrw_rate") is not None], start)

    return counts


def main() -> None:
    start = date.today() - timedelta(days=3660)
    counts = mirror_existing_series(SupabaseRest(), start)
    print({"mode": "existing_series_seed", "start": start.isoformat(), "inserted": counts})


if __name__ == "__main__":
    main()
