"""Stage 4: 10-degree cleanup on Stage 3's single line.

Stage 4 uses exactly one state machine and only Stage 3 output.

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
- Stage 4 only deletes; it never creates a point absent from Stage 3
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

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


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


@dataclass
class _RunState:
    anchor: PivotPoint
    direction: int  # +1 rising, -1 falling
    extreme: PivotPoint
    angle_ordinal: int = 0
    collapse_end: PivotPoint | None = None
    angle_blocked: bool = False

    # provisional opposite excursion
    opposite_extreme: PivotPoint | None = None
    rebound_extreme: PivotPoint | None = None


def _first_nonflat_direction(points: Sequence[PivotPoint]) -> tuple[int, int] | None:
    for index in range(len(points) - 1):
        direction = _sign(points[index + 1].value - points[index].value)
        if direction != 0:
            return index, direction
    return None


def _can_extend(state: _RunState, point: PivotPoint) -> bool:
    return (
        point.value > state.extreme.value
        if state.direction > 0
        else point.value < state.extreme.value
    )


def _record_collapse(
    intervals: list[tuple[PivotPoint, PivotPoint]],
    state: _RunState,
) -> None:
    if (
        state.collapse_end is not None
        and state.anchor.day < state.collapse_end.day
    ):
        intervals.append((state.anchor, state.collapse_end))


def _process_window(
    points: Sequence[PivotPoint],
    geometry: ChartGeometry,
    threshold: float,
) -> list[tuple[PivotPoint, PivotPoint]]:
    """Return direct-collapse intervals for one unprotected line window."""
    ordered = list(points)
    initial = _first_nonflat_direction(ordered)
    if initial is None:
        return []

    start_index, direction = initial
    anchor = ordered[start_index]
    extreme = ordered[start_index + 1]
    state = _RunState(
        anchor=anchor,
        direction=direction,
        extreme=extreme,
    )

    intervals: list[tuple[PivotPoint, PivotPoint]] = []
    index = start_index + 2

    while index < len(ordered):
        point = ordered[index]

        # Every SAME-SIDE candidate consumes an angle ordinal, even when it
        # does not improve the connection extreme. This is critical: skipping a
        # lower high / higher low must not make a later point become "angle #1".
        same_side = point.pivot_type == state.extreme.pivot_type
        improves = _can_extend(state, point)

        if same_side and not state.angle_blocked:
            state.angle_ordinal += 1
            angle = screen_origin_angle_degrees(
                state.anchor,
                state.extreme,
                point,
                geometry,
            )

            if state.angle_ordinal >= 2 and angle > threshold:
                # Stop before this candidate. Preserve everything from here until
                # a confirmed reversal creates a new anchor.
                _record_collapse(intervals, state)
                state.collapse_end = None
                state.angle_blocked = True

                if improves:
                    state.extreme = point
                    state.opposite_extreme = None
                    state.rebound_extreme = None

                index += 1
                continue

        # Old trend resumes / continues with a new extreme.
        if improves:
            state.extreme = point
            if not state.angle_blocked:
                state.collapse_end = point
            state.opposite_extreme = None
            state.rebound_extreme = None
            index += 1
            continue

        # Opposite excursion / failed same-direction move.
        if state.direction > 0:
            # Rising run: watch for high -> low -> lower high -> lower low.
            if state.opposite_extreme is None:
                state.opposite_extreme = point
                index += 1
                continue

            if state.rebound_extreme is None:
                if point.value > state.opposite_extreme.value:
                    # rebound high candidate
                    state.rebound_extreme = point
                elif point.value < state.opposite_extreme.value:
                    # deeper first pullback before rebound
                    state.opposite_extreme = point
                index += 1
                continue

            # Full down reversal confirmed by a lower low.
            if point.value < state.opposite_extreme.value:
                _record_collapse(intervals, state)

                turn = state.extreme
                state = _RunState(
                    anchor=turn,
                    direction=-1,
                    extreme=point,
                )
                index += 1
                continue

            # Still inside provisional reversal.
            if point.value > state.rebound_extreme.value:
                state.rebound_extreme = point
            index += 1
            continue

        # Falling run: watch for low -> high -> higher low -> higher high.
        if state.opposite_extreme is None:
            state.opposite_extreme = point
            index += 1
            continue

        if state.rebound_extreme is None:
            if point.value < state.opposite_extreme.value:
                # rebound low candidate
                state.rebound_extreme = point
            elif point.value > state.opposite_extreme.value:
                # higher first rebound before a pullback
                state.opposite_extreme = point
            index += 1
            continue

        # Full up reversal confirmed by a higher high.
        if point.value > state.opposite_extreme.value:
            _record_collapse(intervals, state)

            turn = state.extreme
            state = _RunState(
                anchor=turn,
                direction=1,
                extreme=point,
            )
            index += 1
            continue

        # Still inside provisional reversal.
        if point.value < state.rebound_extreme.value:
            state.rebound_extreme = point
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

    points = tuple(sorted(
        result.markers,
        key=lambda item: (item.day, item.pivot_type),
    ))
    if len(points) < 3:
        return result

    point_map = {_key(point): point for point in points}
    endpoint_keys = {
        _key(point)
        for segment in result.segments
        for point in (segment.start, segment.end)
    }
    standalone_keys = {
        _key(point)
        for point in result.markers
        if _key(point) not in endpoint_keys
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

    # Hard structure splits the line. The boundary point belongs to both sides.
    split_days = sorted({
        segment.end.day if segment.kind == "sideways" else segment.start.day
        for segment in result.segments
        if segment.kind in {"sideways", "spike"}
    })

    windows: list[list[PivotPoint]] = []
    start_index = 0
    for split_day in split_days:
        current = [
            point
            for point in points[start_index:]
            if point.day <= split_day
        ]
        if current:
            windows.append(current)
            start_index = points.index(current[-1])

    tail = list(points[start_index:])
    if tail:
        windows.append(tail)

    intervals: list[tuple[PivotPoint, PivotPoint]] = []
    for window in windows:
        if len(window) < 3:
            continue

        # Run the SAME state machine from every actual value-direction turn.
        # A failed/unfinished state before this turn must not poison later runs.
        start_indexes = [0]
        for idx in range(1, len(window) - 1):
            left = _sign(window[idx].value - window[idx - 1].value)
            right = _sign(window[idx + 1].value - window[idx].value)
            if left != 0 and right != 0 and left != right:
                start_indexes.append(idx)

        for start_idx in start_indexes:
            intervals.extend(
                _process_window(
                    window[start_idx:],
                    geometry,
                    angle_threshold_deg,
                )
            )

    # A collapse may never cross protected structure.
    candidates: list[tuple[PivotPoint, PivotPoint]] = []
    for start, end in intervals:
        if any(
            start.day < point.day < end.day
            and _key(point) in protected_keys
            for point in points
        ):
            continue
        candidates.append((start, end))

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
    filtered: list[tuple[PivotPoint, PivotPoint]] = []
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

    def inside(segment: SimplifiedLineSegment) -> bool:
        return any(
            start.day <= segment.start.day
            and segment.end.day <= end.day
            for start, end in filtered
        )

    kept_segments = [
        segment
        for segment in result.segments
        if not inside(segment)
    ]
    for start, end in filtered:
        kept_segments.append(
            SimplifiedLineSegment(start=start, end=end, kind="trend")
        )
    kept_segments.sort(
        key=lambda item: (item.start.day, item.end.day, item.kind)
    )

    marker_map: dict[tuple[date, float, str], PivotPoint] = {}
    for segment in kept_segments:
        marker_map[_key(segment.start)] = segment.start
        marker_map[_key(segment.end)] = segment.end
    for point in result.markers:
        if _key(point) in standalone_keys:
            marker_map[_key(point)] = point

    if not set(marker_map).issubset(point_map):
        raise RuntimeError("stage4 produced a point absent from stage3")

    return SimplifiedLineResult(
        markers=tuple(sorted(
            marker_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(kept_segments),
        sideways_segments=result.sideways_segments,
    )
