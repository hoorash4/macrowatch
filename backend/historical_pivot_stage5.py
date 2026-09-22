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
    line_role_map,
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

    # provisional opposite excursion
    opposite_extreme: LinePoint | None = None


def _discover_initial_state(
    points: Sequence[LinePoint],
) -> tuple[_RunState, int] | None:
    """Start from the first actual segment of the merged Stage-3+ line."""
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
) -> list[tuple[LinePoint, LinePoint]]:
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
    roles = line_role_map(ordered)
    decision_points = [
        point for point in ordered
        if roles.get(_key(point)) is not None
    ]
    discovered = _discover_initial_state(decision_points)
    if discovered is None:
        return []

    state, index = discovered
    intervals: list[tuple[LinePoint, LinePoint]] = []

    while index < len(decision_points):
        point = decision_points[index]

        wanted_role = "high" if state.direction > 0 else "low"
        point_role = roles.get(_key(point))

        # Wait for one opposite-side pullback after the active extreme.
        if state.opposite_extreme is None:
            if point_role != wanted_role:
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
        if point_role != wanted_role:
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

        # The next same-side point failed to recover/extend the old extreme.
        # The old extreme is therefore confirmed as the turn immediately.
        # Freeze it now; later points may not retroactively erase that turn.
        opposite = state.opposite_extreme
        _record_collapse(intervals, state)
        turn = state.extreme
        state = _RunState(
            anchor=turn,
            direction=_sign(opposite.value - turn.value),
            extreme=opposite,
            opposite_extreme=point,
        )
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
