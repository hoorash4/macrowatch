"""Manual-only initialization of long auxiliary history for the inflation model."""

from __future__ import annotations

import json
from datetime import date

from common import SupabaseRest, require_env
from inflation_pipeline import (
    BLS_CORE_CPI_EX_SHELTER,
    CACHE_COLLECTOR,
    COMMODITY_GROUPS,
    FRED_SERIES,
    SOURCE_START,
    fetch_bls_series,
    fetch_fred_series,
    fetch_yahoo_series,
    inflation_cache_points,
    run_automatic,
)
from signals.automatic_source_cache import mark_initialized, store as store_source_cache


LEGACY_INDEX_CACHE_SERIES = (
    "FRED:cpi", "FRED:core_cpi", "FRED:pce", "FRED:core_pce",
    "FRED:headline_ppi", "FRED:core_ppi",
)


def initialize(today: date | None = None, db: SupabaseRest | None = None) -> None:
    end = today or date.today()
    client = db or SupabaseRest()
    fred = fetch_fred_series(require_env("FRED_API_KEY"), end, {name: SOURCE_START for name in FRED_SERIES})
    fred["core_cpi_ex_shelter"] = fetch_bls_series(BLS_CORE_CPI_EX_SHELTER, end, SOURCE_START)
    prices = {
        symbol: fetch_yahoo_series(symbol, end, SOURCE_START)
        for symbols in COMMODITY_GROUPS.values()
        for symbol in symbols
    }
    points = inflation_cache_points(fred, prices)
    if any(not values for values in points.values()):
        raise RuntimeError("Inflation auxiliary backfill returned incomplete history")
    store_source_cache(client, CACHE_COLLECTOR, points)
    for series in LEGACY_INDEX_CACHE_SERIES:
        client.request("DELETE", "automatic_source_points", params={
            "collector": f"eq.{CACHE_COLLECTOR}", "series_code": f"eq.{series}",
        }, prefer="return=minimal")
    mark_initialized(client, CACHE_COLLECTOR, end)
    print(json.dumps({
        "mode": "backfill",
        "stage": "inflation-model-auxiliary-sources",
        "start": SOURCE_START.isoformat(),
        "end": end.isoformat(),
        "series": len(points),
    }, ensure_ascii=False, sort_keys=True))


def main() -> None:
    initialize()
    run_automatic()


if __name__ == "__main__":
    main()
