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
    """Return direct-collapse intervals from one chronological Stage-3 pass.

    A Stage-3 point is always judged before it can be removed.

    For the active direction, anchor -> extreme is the current structural leg.
    After an opposite pullback appears, the very next same-side Stage-3 point
    decides the previous extreme:

    - if it exceeds the previous extreme, the old extreme was not a confirmed
      turn; the trend continues and the intermediate pullback can be collapsed.
    - if it does not exceed the previous extreme, the old extreme is confirmed
      as a turn. It immediately becomes the new anchor, the pullback becomes
      the first extreme of the opposite run, and judgment continues from there.

    This is the 100% retracement rule in point form: a candidate turn is
    cancelled only when the following same-side point fully retraces past it.
    No later/final point may retroactively re-judge an already confirmed turn.

    The first same-direction extension from a newly confirmed anchor is always
    collapsible. Second and later same-side candidates still consume the
    existing 10-degree ordinal; the angle only decides whether an improving
    extension may be collapsed.
    """
    ordered = list(points)
    initial = _first_nonflat_direction(ordered)
    if initial is None:
        return []

    start_index, direction = initial
    state = _RunState(
        anchor=ordered[start_index],
        direction=direction,
        extreme=ordered[start_index + 1],
    )
    intervals: list[tuple[PivotPoint, PivotPoint]] = []
    index = start_index + 2

    while index < len(ordered):
        point = ordered[index]

        # Wait for one opposite-side pullback after the active extreme.
        if state.opposite_extreme is None:
            if point.pivot_type != state.extreme.pivot_type:
                state.opposite_extreme = point
                index += 1
                continue

            # Consecutive same-side Stage-3 points are still judged in order.
            state.angle_ordinal += 1
            improves = _can_extend(state, point)
            angle = screen_origin_angle_degrees(
                state.anchor,
                state.extreme,
                point,
                geometry,
            )
            if (
                improves
                and state.angle_ordinal >= 2
                and angle > threshold
            ):
                _record_collapse(intervals, state)
                state = _RunState(
                    anchor=state.extreme,
                    direction=_sign(point.value - state.extreme.value),
                    extreme=point,
                )
                index += 1
                continue

            if improves:
                state.extreme = point
                state.collapse_end = point
            index += 1
            continue

        # If Stage 3 happens to provide another opposite-side point before a
        # same-side decision point, keep only the farther pullback for the
        # pending judgment. Nothing is deleted yet.
        if point.pivot_type != state.extreme.pivot_type:
            if (
                (state.direction > 0 and point.value < state.opposite_extreme.value)
                or (state.direction < 0 and point.value > state.opposite_extreme.value)
            ):
                state.opposite_extreme = point
            index += 1
            continue

        # This is the next same-side point. It MUST decide the candidate now.
        # It also consumes an angle ordinal even when it does not improve the
        # current extreme.
        state.angle_ordinal += 1
        improves = _can_extend(state, point)
        angle = screen_origin_angle_degrees(
            state.anchor,
            state.extreme,
            point,
            geometry,
        )

        if improves:
            # Candidate turn cancelled: the following same-side point retraced
            # more than 100% past the previous extreme, so the old trend lives.
            if state.angle_ordinal >= 2 and angle > threshold:
                # The direction is known, but this extension is too sharp to
                # collapse into the old anchor. Preserve existing structure and
                # start a fresh run from the pending pullback.
                _record_collapse(intervals, state)
                pending = state.opposite_extreme
                state = _RunState(
                    anchor=pending,
                    direction=_sign(point.value - pending.value),
                    extreme=point,
                )
                index += 1
                continue

            state.extreme = point
            state.collapse_end = point
            state.opposite_extreme = None
            index += 1
            continue

        # Candidate turn confirmed: the next same-side point failed to retrace
        # 100% back through the previous extreme. Freeze that extreme as the
        # new anchor NOW; later points cannot erase it retroactively.
        _record_collapse(intervals, state)
        turn = state.extreme
        first_extreme = state.opposite_extreme
        new_direction = _sign(first_extreme.value - turn.value)
        if new_direction == 0:
            index += 1
            continue

        state = _RunState(
            anchor=turn,
            direction=new_direction,
            extreme=first_extreme,
        )
        # The deciding point is already the first pullback of the new run.
        state.opposite_extreme = point
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
    rapid_move_keys = {
        _key(point)
        for point in result.protected_points
    }
    protected_keys = standalone_keys | sideways_keys | spike_keys | rapid_move_keys

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

        # Exactly one chronological state machine per unprotected window.
        # Restarting from every later turn creates overlapping collapse
        # candidates and lets a later/final point erase a turn that was already
        # decided earlier in the sequence.
        intervals.extend(
            _process_window(
                window,
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
        protected_points=result.protected_points,
    )
