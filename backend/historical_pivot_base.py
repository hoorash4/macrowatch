"""Historical Insight staged pivot pipeline compatibility facade.

Stage 1: finish upper/lower RDP sets only
Stage 2: classify rapid moves, spikes, and sideways structures
Stage 3: merge upper/lower RDP into one wave line while preserving rapid candidates
Stage 4: rapid-move post-processing/final protection
Stage 5: general trend/reversal/10-degree state machine
Stage 6: final consecutive-direction cleanup
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any, Sequence

from common import SupabaseRest

from historical_pivot_shared import (
    BUFFER_MONTHS,
    SAME_TREND_ANGLE_THRESHOLD_DEG,
    SIDEWAYS_ANGLE_THRESHOLD_DEG,
    SUPABASE_REST_PAGE_SIZE,
    ChartGeometry,
    PivotPoint,
    RapidMoveCandidate,
    SeriesPoint,
    SidewaysSegment,
    SimplifiedLineResult,
    SimplifiedLineSegment,
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
    BasePivotResult,
    EnvelopePoint,
    PIVOT_POLICIES,
    PivotPolicy,
    _perpendicular_distance,
    build_envelope,
    calculate_base_pivots,
    fixed_count_rdp,
    plateau_extrema,
)
from historical_pivot_stage2 import (
    RAPID_MOVE_MIN_VISUAL_Y_SHARE,
    SPIKE_ANGLE_THRESHOLD_DEG,
    SPIKE_FOLLOWUP_POINTS,
    SPIKE_MAX_BC_TO_AB_Y_RATIO,
    SPIKE_MIN_RETRACEMENT_RATIO,
    SPIKE_MIN_VISUAL_Y_SHARE,
    Stage2Result,
    classify_special_structures,
    finalize_sideways_protection,
)
from historical_pivot_stage3 import simplify_pivot_lines as merge_pivot_lines_stage3
from historical_pivot_stage4 import finalize_rapid_moves
from historical_pivot_stage5 import prune_same_trend_extremes
from historical_pivot_stage6 import prune_unconfirmed_retracements


# Compatibility type name used by older callers.  Special-structure output is
# now Stage 2, not Stage 1.
SpikeAugmentedPivotResult = Stage2Result


def _has_pivot_between(
    pivots: Sequence[PivotPoint],
    start: date,
    end: date,
) -> bool:
    return any(start <= item.day <= end for item in pivots)


def _first_pivot_after(
    pivots: Sequence[PivotPoint],
    after: date,
) -> PivotPoint | None:
    return next(
        (
            item
            for item in sorted(pivots, key=lambda point: point.day)
            if item.day > after
        ),
        None,
    )


def augment_spike_entry_points(
    base: BasePivotResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SPIKE_ANGLE_THRESHOLD_DEG,
    min_visual_y_share: float = SPIKE_MIN_VISUAL_Y_SHARE,
    min_retracement_ratio: float = SPIKE_MIN_RETRACEMENT_RATIO,
    max_bc_to_ab_y_ratio: float = SPIKE_MAX_BC_TO_AB_Y_RATIO,
    followup_points: int = SPIKE_FOLLOWUP_POINTS,
) -> Stage2Result:
    """Compatibility wrapper for callers that previously invoked Stage-1 spike logic."""
    return classify_special_structures(
        base,
        geometry,
        spike_angle_threshold_deg=angle_threshold_deg,
        spike_min_visual_y_share=min_visual_y_share,
        spike_min_retracement_ratio=min_retracement_ratio,
        spike_max_bc_to_ab_y_ratio=max_bc_to_ab_y_ratio,
        spike_followup_points=followup_points,
    )


def simplify_pivot_lines(
    source: BasePivotResult | Stage2Result,
    geometry: ChartGeometry,
) -> SimplifiedLineResult:
    """Compatibility entry point: execute the current pipeline through Stage 6."""
    stage2 = (
        source
        if isinstance(source, Stage2Result)
        else classify_special_structures(source, geometry)
    )
    stage3 = merge_pivot_lines_stage3(stage2, geometry)
    stage4 = finalize_rapid_moves(stage3, geometry)
    stage5 = prune_same_trend_extremes(stage4, geometry)
    return prune_unconfirmed_retracements(stage5)


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

