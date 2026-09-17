"""One-off diagnosis for why Pivot AI marked anomalies on a historical indicator."""
from __future__ import annotations

import argparse
import base64
import os
from typing import Any

import requests

from common import SupabaseRest, require_env
from historical_pivot_ai_backfill import (
    display_window,
    load_case,
    load_index_rows,
    load_indicator_rows,
    render_chart,
)


def fetch_one(db: SupabaseRest, case_code: str, index_code: str, series_code: str) -> dict[str, Any]:
    rows = db.request(
        "GET",
        "historical_indicator_ai_analysis",
        params={
            "select": "case_code,index_code,series_code,display_start,display_end,cycle_start,cycle_peak,cycle_trough,regimes,pivots,anomalies,analyzed_at",
            "case_code": f"eq.{case_code}",
            "index_code": f"eq.{index_code}",
            "series_code": f"eq.{series_code}",
            "limit": "1",
        },
    ) or []
    if not rows:
        raise RuntimeError("No stored Pivot AI analysis found.")
    return rows[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--series", required=True)
    args = parser.parse_args()

    db = SupabaseRest()
    case, cycle = load_case(db, args.case, args.index)
    index_code = str(cycle["index_code"])
    core_end = str(cycle.get("trough_date") or cycle.get("peak_date"))
    start, end = display_window(str(cycle["start_date"]), core_end)
    index_rows = load_index_rows(db, index_code, start, end)
    indicator_rows = load_indicator_rows(db, args.series, start, end)
    image = render_chart(index_rows, indicator_rows, cycle, start, end, index_code, args.series)
    prior = fetch_one(db, args.case, index_code, args.series)

    url = require_env("SUPABASE_URL").rstrip("/") + "/functions/v1/pivot-ai-diagnose"
    key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    payload = {
        "case_code": args.case,
        "index_code": index_code,
        "series_code": args.series,
        "display_window": {"start_date": start, "end_date": end},
        "cycle": {
            "start_date": cycle["start_date"],
            "peak_date": cycle.get("peak_date"),
            "trough_date": cycle.get("trough_date"),
        },
        "prior_analysis": prior,
        "chart_image_data_url": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
    }
    response = requests.post(
        url,
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    if not response.ok:
        raise RuntimeError(f"pivot-ai-diagnose {response.status_code}: {response.text[:1500]}")
    result = response.json()
    print("DIAGNOSIS_BEGIN")
    print(result.get("diagnosis", result))
    print("DIAGNOSIS_END")


if __name__ == "__main__":
    main()
