"""Stage 5: 10-degree cleanup on Stage 4's finalized line.

Stage 5 uses exactly one state machine and only Stage 4 output.

The trend direction is the ACTUAL line direction (value rising/falling), not the
original high/low label.

For an active run:
- same-direction new extreme keeps the old trend alive
- an opposite excursion is provisional
- if the old trend makes a new extreme, the provisional reversal is cancelled
- if the opposite excursion completes a full reversal pattern, the current
  extreme becomes the new confirmed anchor
- first same-direction extreme update from an anchor ignores angle
- second and later updates stop before an angle > 10 degrees
- protection never stops judgment; protected points only split final output
- Stage 5 only deletes; it never creates a point absent from Stage 4
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from historical_pivot_shared import (
    ChartGeometry,
    LinePoint,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    SAME_TREND_ANGLE_THRESHOLD_DEG,
    PIVOT_X_GAP_PROTECTION_SHARE,
    screen_origin_angle_degrees,
    screen_x_span_share,
)


def _key(point: LinePoint) -> tuple[date, float]:
    return point.day, point.value


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


@dataclass
class _RunState:
    anchor: LinePoint
    direction: int  # +1 rising, -1 falling
    extreme: LinePoint
    angle_ordinal: int = 0
    collapse_end: LinePoint | None = None

    # Opposite move that has turned away from the active extreme but has not
    # yet broken the active anchor.
    pullback: LinePoint | None = None

    # When an anchor is broken, the prior trend is kept only long enough to
    # detect an immediate failed reversal. Once the new trend survives a
    # pullback and extends again, these rollback fields are cleared.
    rollback_anchor: LinePoint | None = None
    rollback_direction: int | None = None


def _discover_initial_state(
    points: Sequence[LinePoint],
) -> tuple[_RunState, int] | None:
    """Start from the first actual segment of the merged line."""
    ordered = list(points)
    for index in range(len(ordered) - 1):
        direction = _sign(
            float(ordered[index + 1].value) - float(ordered[index].value)
        )
        if direction == 0:
            continue
        return (
            _RunState(
                anchor=ordered[index],
                direction=direction,
                extreme=ordered[index + 1],
            ),
            index + 2,
        )
    return None


def _can_extend(state: _RunState, point: LinePoint) -> bool:
    return (
        point.value > state.extreme.value
        if state.direction > 0
        else point.value < state.extreme.value
    )


def _breaks_anchor(state: _RunState, point: LinePoint) -> bool:
    return (
        point.value < state.anchor.value
        if state.direction > 0
        else point.value > state.anchor.value
    )


def _farther_pullback(state: _RunState, point: LinePoint) -> bool:
    if state.pullback is None:
        return True
    return (
        point.value < state.pullback.value
        if state.direction > 0
        else point.value > state.pullback.value
    )


def _record_collapse(
    intervals: list[tuple[LinePoint, LinePoint]],
    state: _RunState,
) -> None:
    if (
        state.collapse_end is not None
        and state.anchor.day < state.collapse_end.day
    ):
        intervals.append((state.anchor, state.collapse_end))


def _process_window(
    points: Sequence[LinePoint],
    geometry: ChartGeometry,
    threshold: float,
    *,
    reset_keys: set[tuple[date, float]] | None = None,
) -> list[tuple[LinePoint, LinePoint]]:
    """Inspect only the merged chronological wave.

    The active anchor remains the anchor while its trend continues.

    - Turning direction alone does not change the anchor.
    - If an opposite move crosses the active anchor, the previous trend extreme
      becomes the new opposite-trend anchor.
    - That reversal remains provisional until a pullback holds the new anchor
      and the new trend then extends its extreme.
    - If the new anchor is broken before that confirmation, the reversal failed
      and the prior anchor/trend is restored.
    - Same-trend intermediate points collapse into anchor -> latest extreme.
    - The existing 10-degree extension rule is left unchanged.
    """
    ordered = list(points)
    reset_keys = reset_keys or set()
    intervals: list[tuple[LinePoint, LinePoint]] = []

    discovered = _discover_initial_state(ordered)
    if discovered is None:
        return []

    state, index = discovered

    while index < len(ordered):
        point = ordered[index]

        # A finalized rapid endpoint starts a fresh post-rapid wave inspection.
        # Protected output behavior is unchanged; this affects only wave state.
        if _key(point) in reset_keys and index + 1 < len(ordered):
            direction = _sign(ordered[index + 1].value - point.value)
            if direction != 0:
                next_point = ordered[index + 1]
                state = _RunState(
                    anchor=point,
                    direction=direction,
                    extreme=next_point,
                )
                # If the seeded next point is itself a finalized rapid
                # endpoint, do not consume it here. It must immediately become
                # the next wave anchor in this same wave inspection.
                index += 1 if _key(next_point) in reset_keys else 2
                continue

        # A true reversal requires crossing the active anchor, not merely
        # turning away from the current extreme.
        if _breaks_anchor(state, point):
            if (
                state.rollback_anchor is not None
                and state.rollback_direction is not None
            ):
                # The new trend failed before confirmation. Restore the prior
                # anchor/trend and absorb the failed reversal into it.
                old_anchor = state.rollback_anchor
                old_direction = state.rollback_direction
                state = _RunState(
                    anchor=old_anchor,
                    direction=old_direction,
                    extreme=point,
                    collapse_end=point,
                )
                index += 1
                continue

            # The active trend survived from its anchor through its latest
            # extreme. Freeze that completed wave before starting the confirmed
            # opposite trend from the latest extreme.
            _record_collapse(intervals, state)

            old_anchor = state.anchor
            old_direction = state.direction
            new_anchor = state.extreme
            state = _RunState(
                anchor=new_anchor,
                direction=-old_direction,
                extreme=point,
                rollback_anchor=old_anchor,
                rollback_direction=old_direction,
            )
            index += 1
            continue

        if _can_extend(state, point):
            state.angle_ordinal += 1
            angle = screen_origin_angle_degrees(
                state.anchor,
                state.extreme,
                point,
                geometry,
            )

            # Keep the existing 10-degree behavior exactly: first extension is
            # free; second and later extensions stop the collapse when the
            # anchor-based angle exceeds the threshold.
            if state.angle_ordinal >= 2 and angle > threshold:
                _record_collapse(intervals, state)
                if state.pullback is not None:
                    state = _RunState(
                        anchor=state.pullback,
                        direction=_sign(point.value - state.pullback.value),
                        extreme=point,
                    )
                else:
                    state = _RunState(
                        anchor=state.extreme,
                        direction=_sign(point.value - state.extreme.value),
                        extreme=point,
                    )
                index += 1
                continue

            state.extreme = point
            state.collapse_end = point

            # Surviving a pullback and then extending the trend confirms a
            # provisional reversal anchor.
            if state.pullback is not None:
                state.rollback_anchor = None
                state.rollback_direction = None
            state.pullback = None
            index += 1
            continue

        # The point is inside the active anchor/extreme range. It is only a
        # pullback. Keep the farther pullback, but do not change the anchor.
        if _farther_pullback(state, point):
            state.pullback = point
        index += 1

    _record_collapse(intervals, state)
    return intervals



def prune_same_trend_extremes(
    result: SimplifiedLineResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SAME_TREND_ANGLE_THRESHOLD_DEG,
) -> SimplifiedLineResult:
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")

    line_map: dict[tuple[date, float], LinePoint] = {}
    for segment in result.segments:
        line_map[_key(segment.start)] = segment.start
        line_map[_key(segment.end)] = segment.end
    points = tuple(sorted(
        line_map.values(),
        key=lambda item: item.day,
    ))
    if len(points) < 3:
        return result

    point_map = {_key(point): point for point in result.markers}
    marker_only_keys = {
        _key(point)
        for point in result.marker_only_points
    }
    sideways_keys = {
        _key(point)
        for segment in result.segments
        if segment.kind == "sideways"
        for point in (segment.start, segment.end)
    }
    spike_keys = {
        _key(point)
        for segment in result.segments
        if segment.kind == "spike"
        for point in (segment.start, segment.end)
    }

    rapid_move_keys = {
        _key(point)
        for point in result.protected_points
    }
    isolated_gap_keys = {
        _key(point)
        for index, point in enumerate(points[1:-1], start=1)
        if screen_x_span_share(points[index - 1], point, geometry)
        >= PIVOT_X_GAP_PROTECTION_SHARE
        and screen_x_span_share(point, points[index + 1], geometry)
        >= PIVOT_X_GAP_PROTECTION_SHARE
    }
    protected_keys = (
        marker_only_keys
        | sideways_keys
        | spike_keys
        | rapid_move_keys
        | isolated_gap_keys
    )

    # Protection constrains OUTPUT, not JUDGMENT.  Run one chronological state
    # machine across the complete Stage-4 timeline so later points may still
    # decide earlier provisional structure.  Protected points are inserted back
    # as mandatory split points when collapsed segments are rebuilt.
    candidates = list(
        _process_window(
            points,
            geometry,
            angle_threshold_deg,
            reset_keys=rapid_move_keys,
        )
    )

    if not candidates:
        return result

    # Prefer the earliest valid anchor. If two intervals share an anchor, keep
    # the farther endpoint. Later overlapping candidates are subordinate to the
    # earlier run and are ignored.
    candidates.sort(
        key=lambda item: (
            item[0].day,
            -item[1].day.toordinal(),
        )
    )
    filtered: list[tuple[LinePoint, LinePoint]] = []
    for start, end in candidates:
        if not filtered:
            filtered.append((start, end))
            continue

        prev_start, prev_end = filtered[-1]
        if start.day == prev_start.day:
            if end.day > prev_end.day:
                filtered[-1] = (start, end)
            continue

        if start.day < prev_end.day:
            continue

        filtered.append((start, end))

    exact_kind = {
        (_key(segment.start), _key(segment.end)): segment.kind
        for segment in result.segments
    }

    # Rebuild ONE chronological line instead of appending collapsed segments to
    # partially-overlapping old segments.  A point strictly inside a collapse
    # interval disappears unless it is protected.  Final rapid endpoints are
    # connected mandatory vertices; true marker-only points stay standalone.
    connected_map: dict[tuple[date, float], LinePoint] = {}
    for segment in result.segments:
        connected_map[_key(segment.start)] = segment.start
        connected_map[_key(segment.end)] = segment.end
    for start, end in filtered:
        for point_key, point in list(connected_map.items()):
            if (
                start.day < point.day < end.day
                and point_key not in protected_keys
            ):
                connected_map.pop(point_key, None)
        connected_map[_key(start)] = start
        connected_map[_key(end)] = end

    connected_points = sorted(
        connected_map.values(),
        key=lambda item: item.day,
    )
    kept_segments: list[SimplifiedLineSegment] = []
    for left, right in zip(connected_points, connected_points[1:]):
        kind = exact_kind.get((_key(left), _key(right)), "trend")
        kept_segments.append(
            SimplifiedLineSegment(start=left, end=right, kind=kind)
        )

    marker_map: dict[tuple[date, float], LinePoint] = {
        _key(point): point for point in connected_points
    }
    for point in result.marker_only_points:
        marker_map[_key(point)] = point

    if not set(marker_map).issubset(point_map):
        raise RuntimeError("stage5 produced a point absent from stage4")

    return SimplifiedLineResult(
        markers=tuple(sorted(
            marker_map.values(),
            key=lambda item: item.day,
        )),
        segments=tuple(kept_segments),
        marker_only_points=result.marker_only_points,
        protected_points=result.protected_points,
    )
