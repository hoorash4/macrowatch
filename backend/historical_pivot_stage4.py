"""Stage 4: 10-degree simplification over Stage 3's single wave line.

This file intentionally contains one Stage-4 state machine only.

Input:
- Stage 3 surviving markers/segments only
- chart geometry for screen-angle measurement

Responsibilities:
- track the active trend from the current confirmed anchor
- keep reversal points provisional until the following same-side point confirms
  or cancels them
- collapse improving same-direction extremes from the confirmed anchor
- ignore the first angle unconditionally
- apply the 10-degree threshold from the second angle onward
- never cross protected spike/sideways structure
"""
from __future__ import annotations

from datetime import date

from historical_pivot_shared import (
    ChartGeometry,
    PivotPoint,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    SAME_TREND_ANGLE_THRESHOLD_DEG,
    screen_origin_angle_degrees,
)


def _key(point: PivotPoint) -> tuple[date, float, str]:
    return point.day, point.value, point.pivot_type


def _direction(left: PivotPoint, right: PivotPoint) -> str | None:
    if right.value > left.value:
        return "up"
    if right.value < left.value:
        return "down"
    return None


def prune_same_trend_extremes(
    result: SimplifiedLineResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SAME_TREND_ANGLE_THRESHOLD_DEG,
) -> SimplifiedLineResult:
    """Simplify Stage 3's one line with confirmed-anchor 10-degree logic."""
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")

    point_map = {_key(point): point for point in result.markers}
    ordered = list(sorted(
        point_map.values(),
        key=lambda item: (item.day, item.pivot_type),
    ))
    if len(ordered) < 3:
        return result

    segment_endpoint_keys = {
        _key(point)
        for segment in result.segments
        for point in (segment.start, segment.end)
    }
    standalone_keys = {
        _key(point)
        for point in result.markers
        if _key(point) not in segment_endpoint_keys
    }
    sideways_keys = {
        _key(point)
        for sideways in result.sideways_segments
        for point in sideways.pivot_points
    }
    spike_keys = {
        _key(point)
        for segment in result.segments
        if segment.kind == "spike"
        for point in (segment.start, segment.end)
    }
    protected_keys = standalone_keys | sideways_keys | spike_keys

    hard_segments = tuple(
        segment
        for segment in result.segments
        if segment.kind in {"sideways", "spike"}
    )

    def crosses_hard_structure(start: PivotPoint, end: PivotPoint) -> bool:
        return any(
            start.day < point.day < end.day
            for point in ordered
            if _key(point) in protected_keys
        )

    # Build the actual connected Stage-3 line order.  Standalone marker-only
    # points are protected but never participate in trend/reversal state.
    line_points: list[PivotPoint] = []
    ordinary_segments = sorted(
        result.segments,
        key=lambda item: (item.start.day, item.end.day, item.kind),
    )
    for segment in ordinary_segments:
        if not line_points:
            line_points.append(segment.start)
        elif line_points[-1] != segment.start:
            line_points.append(segment.start)
        if line_points[-1] != segment.end:
            line_points.append(segment.end)

    if len(line_points) < 3:
        return result

    # Stage 4 edits only ordinary trend spans. Hard structure divides the line
    # into independent windows and is copied back unchanged.
    hard_endpoint_days = {
        point.day
        for segment in hard_segments
        for point in (segment.start, segment.end)
    }

    def same_window(left: PivotPoint, right: PivotPoint) -> bool:
        return not any(left.day < day < right.day for day in hard_endpoint_days)

    # Confirmed output vertices.  The first Stage-3 line point is the initial
    # confirmed anchor.
    confirmed: list[PivotPoint] = [line_points[0]]
    anchor = line_points[0]
    trend = _direction(line_points[0], line_points[1])
    if trend is None:
        return result

    # Current same-direction extreme and its angle ordinal from the anchor.
    extreme: PivotPoint | None = None
    angle_ordinal = 0

    # Provisional reversal state.
    reversal_candidate: PivotPoint | None = None
    rebound_extreme: PivotPoint | None = None
    same_side_retrace_seen = False

    def reset_run(new_anchor: PivotPoint, new_trend: str) -> None:
        nonlocal anchor, trend, extreme, angle_ordinal
        nonlocal reversal_candidate, rebound_extreme, same_side_retrace_seen
        anchor = new_anchor
        trend = new_trend
        extreme = None
        angle_ordinal = 0
        reversal_candidate = None
        rebound_extreme = None
        same_side_retrace_seen = False

    def improve_extreme(candidate: PivotPoint) -> None:
        """Accept a new same-direction record extreme under the 10-degree rule."""
        nonlocal extreme, angle_ordinal
        if extreme is None:
            extreme = candidate
            return

        improves = (
            candidate.value > extreme.value
            if trend == "up"
            else candidate.value < extreme.value
        )
        if not improves:
            return

        angle_ordinal += 1
        angle = screen_origin_angle_degrees(
            anchor,
            extreme,
            candidate,
            geometry,
        )

        # First angle is always ignored. Threshold starts at angle #2.
        if angle_ordinal >= 2 and angle > angle_threshold_deg:
            return

        # Protected structure may never be collapsed through.
        if crosses_hard_structure(anchor, candidate):
            return

        extreme = candidate

    index = 1
    while index < len(line_points):
        point = line_points[index]

        # Hard-structure endpoint: flush the active ordinary run and restart
        # from the protected endpoint using the next visible line direction.
        if _key(point) in protected_keys:
            if extreme is not None and confirmed[-1] != extreme:
                confirmed.append(extreme)
            if confirmed[-1] != point:
                confirmed.append(point)

            next_point = (
                line_points[index + 1]
                if index + 1 < len(line_points)
                else None
            )
            if next_point is not None:
                next_trend = _direction(point, next_point)
                if next_trend is not None:
                    reset_run(point, next_trend)
            index += 1
            continue

        wanted_extreme_type = "high" if trend == "up" else "low"
        candidate_type = "low" if trend == "up" else "high"

        if reversal_candidate is None:
            if point.pivot_type == wanted_extreme_type:
                improve_extreme(point)
                index += 1
                continue

            # Opposite-side point is only a provisional reversal candidate.
            reversal_candidate = point
            rebound_extreme = None
            same_side_retrace_seen = False
            index += 1
            continue

        # We already have a provisional reversal candidate.
        if trend == "up":
            if point.pivot_type == "high":
                # Old uptrend makes a new record high: reversal candidate dies.
                if extreme is None or point.value > extreme.value:
                    reversal_candidate = None
                    rebound_extreme = None
                    same_side_retrace_seen = False
                    improve_extreme(point)
                    index += 1
                    continue

                # First rebound high after the provisional low.
                if rebound_extreme is None:
                    rebound_extreme = point
                elif same_side_retrace_seen and point.value > rebound_extreme.value:
                    # Down reversal is confirmed at the previous up-run extreme.
                    turn = extreme or anchor
                    if confirmed[-1] != turn:
                        confirmed.append(turn)
                    reset_run(turn, "down")
                    # Reprocess this point inside the new down-run.
                    continue
                elif point.value > rebound_extreme.value:
                    rebound_extreme = point

                index += 1
                continue

            # Another low.
            if point.value < reversal_candidate.value:
                reversal_candidate = point
            elif rebound_extreme is not None and point.value > reversal_candidate.value:
                same_side_retrace_seen = True
            index += 1
            continue

        # trend == "down"
        if point.pivot_type == "low":
            # Old downtrend makes a new record low: reversal candidate dies.
            if extreme is None or point.value < extreme.value:
                reversal_candidate = None
                rebound_extreme = None
                same_side_retrace_seen = False
                improve_extreme(point)
                index += 1
                continue

            if rebound_extreme is None:
                rebound_extreme = point
            elif same_side_retrace_seen and point.value < rebound_extreme.value:
                # Up reversal confirmed at the previous down-run extreme.
                turn = extreme or anchor
                if confirmed[-1] != turn:
                    confirmed.append(turn)
                reset_run(turn, "up")
                continue
            elif point.value < rebound_extreme.value:
                rebound_extreme = point

            index += 1
            continue

        # Another high.
        if point.value > reversal_candidate.value:
            reversal_candidate = point
        elif rebound_extreme is not None and point.value < reversal_candidate.value:
            same_side_retrace_seen = True
        index += 1

    # Final active run always contributes its last accepted extreme.
    if extreme is not None and confirmed[-1] != extreme:
        confirmed.append(extreme)

    # Preserve the Stage-3 terminal line endpoint when it lies after the final
    # accepted extreme and was not merely a cancelled reversal candidate.
    terminal = line_points[-1]
    if (
        confirmed[-1] != terminal
        and _key(terminal) in protected_keys
    ):
        confirmed.append(terminal)

    # Deduplicate while preserving chronology.
    vertices: list[PivotPoint] = []
    for point in confirmed:
        if vertices and _key(vertices[-1]) == _key(point):
            continue
        vertices.append(point)

    if len(vertices) < 2:
        return result

    # Rebuild only ordinary trend connections between surviving vertices.
    rebuilt: list[SimplifiedLineSegment] = []
    hard_lookup = {
        (_key(segment.start), _key(segment.end)): segment
        for segment in hard_segments
    }

    for left, right in zip(vertices, vertices[1:]):
        hard = hard_lookup.get((_key(left), _key(right)))
        if hard is not None:
            rebuilt.append(hard)
            continue
        rebuilt.append(
            SimplifiedLineSegment(
                start=left,
                end=right,
                kind="trend",
            )
        )

    marker_map = {
        _key(point): point
        for point in vertices
    }
    for marker in result.markers:
        if _key(marker) in standalone_keys:
            marker_map[_key(marker)] = marker

    if not set(marker_map).issubset(point_map):
        raise RuntimeError("stage4 produced a point absent from stage3")

    surviving_sideways = tuple(
        sideways
        for sideways in result.sideways_segments
        if _key(sideways.start) in marker_map
        and _key(sideways.end) in marker_map
    )

    return SimplifiedLineResult(
        markers=tuple(sorted(
            marker_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(rebuilt),
        sideways_segments=surviving_sideways,
    )
