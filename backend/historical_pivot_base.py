"""Historical Insight staged pivot pipeline compatibility facade.

The implementation is physically separated into five stage modules. Each downstream
stage receives only the untouched raw-graph context it needs plus the immediately
previous stage result. Earlier candidate sets are not carried forward.

Stage 1: RDP + spike correction/protection
Stage 2: sideways classification/protection boundary
Stage 3: upper/lower merge into one line
Stage 4: 10-degree simplification
Stage 5: consecutive-point cleanup

This module keeps the existing public imports/call signatures stable for the rest of
MacroWatch while delegating the actual work to the stage modules.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

from common import SupabaseRest

from historical_pivot_shared import (
    BUFFER_MONTHS,
    SAME_TREND_ANGLE_THRESHOLD_DEG,
    SIDEWAYS_ANGLE_THRESHOLD_DEG,
    SPIKE_ANGLE_THRESHOLD_DEG,
    SUPABASE_REST_PAGE_SIZE,
    BasePivotResult,
    ChartGeometry,
    EnvelopePoint,
    PIVOT_POLICIES,
    PivotPoint,
    PivotPolicy,
    SeriesPoint,
    SidewaysSegment,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    SpikeAugmentedPivotResult,
    SpikePeak,
    buffer_bounds,
    classify_sideways_reference_line,
    normalize_rows,
    screen_angle_degrees,
    screen_origin_angle_degrees,
    screen_segment_angle_degrees,
    shift_months,
)
from historical_pivot_stage1 import (
    _first_pivot_after,
    _has_pivot_between,
    _perpendicular_distance,
    augment_spike_entry_points,
    calculate_base_pivots,
    fixed_count_rdp,
    plateau_extrema,
)
from historical_pivot_stage2 import Stage2Result, finalize_sideways_protection
from historical_pivot_stage3 import simplify_pivot_lines as merge_pivot_lines_stage3
from historical_pivot_stage4 import prune_same_trend_extremes
from historical_pivot_stage5 import prune_unconfirmed_retracements


def simplify_pivot_lines(
    augmented: SpikeAugmentedPivotResult,
    geometry: ChartGeometry,
) -> SimplifiedLineResult:
    """Compatibility entry point: execute Stages 2->5 without exposing older data."""
    stage2 = finalize_sideways_protection(augmented, geometry)
    stage3 = merge_pivot_lines_stage3(stage2, geometry)
    stage4 = prune_same_trend_extremes(stage3, geometry)
    return prune_unconfirmed_retracements(stage4)


def _single_row(
    db: SupabaseRest,
    table: str,
    params: dict[str, str],
) -> dict[str, Any]:
    rows = db.request("GET", table, params=params) or []
    if len(rows) != 1:
        raise RuntimeError(f"expected exactly one {table} row, got {len(rows)}")
    return rows[0]



def _get_rows_paginated(
    db: SupabaseRest,
    table: str,
    params: dict[str, str],
    *,
    page_size: int = SUPABASE_REST_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Read every REST row without relying on Supabase's per-request row cap."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")

    base_params = dict(params)
    base_params.pop("limit", None)
    base_params.pop("offset", None)
    rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        page_params = {
            **base_params,
            "limit": str(page_size),
            "offset": str(offset),
        }
        page = db.request("GET", table, params=page_params) or []
        if not isinstance(page, list):
            raise RuntimeError(f"expected a list from {table}, got {type(page).__name__}")
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += len(page)



def load_case_series(
    db: SupabaseRest,
    *,
    case_code: str,
    index_code: str,
    series_code: str,
) -> tuple[tuple[dict[str, Any], ...], str, date, date]:
    cycle = _single_row(db, "historical_case_market_cycles", {
        "select": "start_date,trough_date",
        "case_code": f"eq.{case_code}",
        "index_code": f"eq.{index_code}",
        "limit": "2",
    })
    if not cycle.get("start_date") or not cycle.get("trough_date"):
        raise RuntimeError("Historical cycle requires both START and TROUGH")
    cycle_start = date.fromisoformat(str(cycle["start_date"])[:10])
    cycle_trough = date.fromisoformat(str(cycle["trough_date"])[:10])
    buffer_start, buffer_end = buffer_bounds(cycle_start, cycle_trough)

    rows = _get_rows_paginated(db, "economic_chart_points", {
        "select": "observation_date,value,frequency",
        "series_code": f"eq.{series_code}",
        "observation_date": f"gte.{buffer_start.isoformat()}",
        "and": f"(observation_date.lte.{buffer_end.isoformat()})",
        "order": "observation_date.asc",
    })
    if not rows:
        raise RuntimeError(f"No economic-chart rows for {series_code} in buffer range")
    frequencies = {str(row.get("frequency") or "") for row in rows}
    if len(frequencies) != 1:
        raise RuntimeError(
            f"{series_code} has inconsistent frequencies in buffer range: {sorted(frequencies)}"
        )
    frequency = next(iter(frequencies))
    if frequency not in PIVOT_POLICIES:
        raise RuntimeError(
            f"{series_code} frequency {frequency} is not part of this base pipeline"
        )
    return tuple(rows), frequency, buffer_start, buffer_end



def calculate_case_series(
    db: SupabaseRest,
    *,
    case_code: str,
    index_code: str,
    series_code: str,
) -> tuple[BasePivotResult, date, date]:
    rows, frequency, buffer_start, buffer_end = load_case_series(
        db,
        case_code=case_code,
        index_code=index_code,
        series_code=series_code,
    )
    return calculate_base_pivots(rows, frequency), buffer_start, buffer_end



def frontend_payload(
    result: BasePivotResult,
    *,
    case_code: str,
    index_code: str,
    series_code: str,
    buffer_start: date,
    buffer_end: date,
) -> dict[str, Any]:
    """Serialize marker-only output; raw chart points remain owned by the frontend data path."""
    return {
        "case_code": case_code,
        "index_code": index_code,
        "series_code": series_code,
        "frequency": result.frequency,
        "buffer_start": buffer_start.isoformat(),
        "buffer_end": buffer_end.isoformat(),
        "markers": [
            {
                "pivot_date": marker.day.isoformat(),
                "pivot_value": marker.value,
                "pivot_type": marker.pivot_type,
            }
            for marker in result.display_markers
        ],
    }



def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate base Historical Insight pivot markers without changing source rows."
    )
    parser.add_argument("--case-code", required=True)
    parser.add_argument("--index-code", required=True)
    parser.add_argument("--series-code", required=True)
    args = parser.parse_args()

    result, buffer_start, buffer_end = calculate_case_series(
        SupabaseRest(),
        case_code=args.case_code,
        index_code=args.index_code,
        series_code=args.series_code,
    )
    print(json.dumps(frontend_payload(
        result,
        case_code=args.case_code,
        index_code=args.index_code,
        series_code=args.series_code,
        buffer_start=buffer_start,
        buffer_end=buffer_end,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

