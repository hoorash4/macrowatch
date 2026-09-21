"""Stage 6: final consecutive-point cleanup over Stage 5 output only."""
from __future__ import annotations

from datetime import date

from historical_pivot_shared import (
    SAME_TREND_ANGLE_THRESHOLD_DEG,
    ChartGeometry,
    PivotPoint,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    screen_segment_angle_degrees,
)

def prune_unconfirmed_retracements(
    result: SimplifiedLineResult,
    geometry: ChartGeometry | None = None,
    *,
    angle_threshold_deg: float = SAME_TREND_ANGLE_THRESHOLD_DEG,
) -> SimplifiedLineResult:
    """Stage 6: final same-direction cleanup with an optional 10-degree guard.

    Consecutive vertices moving in the same value direction are candidates for
    collapse. The first interior angle of each same-direction run is ignored:
    the first three points are merged automatically. From the SECOND interior
    angle onward, compare the LAST accepted trend line with the new forward
    segment in screen coordinates. If their absolute direction difference is
    <= 10 degrees, keep collapsing. If it exceeds 10 degrees, preserve the
    bend point and start a new run there.

    Protected sideways/spike/rapid endpoints and standalone markers remain
    mandatory output vertices, but they do not stop chronological judgment.

    geometry=None preserves the previous unconditional monotonic-collapse
    behavior for compatibility callers. The production pipeline supplies
    geometry and therefore uses the angle guard.
    """
    def key(point: PivotPoint) -> tuple[date, float, str]:
        return point.day, point.value, point.pivot_type

    input_map = {key(point): point for point in result.markers}
    if len(result.markers) < 3:
        return result

    marker_only_keys = {
        key(point)
        for point in result.marker_only_points
    }
    sideways_keys = {
        key(point)
        for segment in result.segments
        if segment.kind == "sideways"
        for point in (segment.start, segment.end)
    }
    spike_keys = {
        key(point)
        for segment in result.segments
        if segment.kind == "spike"
        for point in (segment.start, segment.end)
    }
    rapid_move_keys = {
        key(point)
        for point in result.protected_points
    }
    protected_keys = marker_only_keys | sideways_keys | spike_keys | rapid_move_keys

    # Use the unique chronological vertices of the connected line.  This keeps
    # Stage 6 stable even if an upstream caller supplies overlapping segments.
    line_map: dict[tuple[date, float, str], PivotPoint] = {}
    for segment in result.segments:
        line_map[key(segment.start)] = segment.start
        line_map[key(segment.end)] = segment.end
    line_points = sorted(
        line_map.values(),
        key=lambda item: (item.day, item.pivot_type),
    )

    if len(line_points) < 3:
        return result

    delete_keys: set[tuple[date, float, str]] = set()

    def direction(left: PivotPoint, right: PivotPoint) -> int:
        if right.value > left.value:
            return 1
        if right.value < left.value:
            return -1
        return 0

    # Protection constrains OUTPUT, not JUDGMENT.  Judge one continuous line;
    # protected points remain mandatory vertices when the line is rebuilt.
    windows: list[list[PivotPoint]] = [line_points]

    def angle_difference(
        line_start: PivotPoint,
        line_end: PivotPoint,
        next_end: PivotPoint,
    ) -> float:
        if geometry is None:
            return 0.0
        previous_angle = screen_segment_angle_degrees(
            line_start,
            line_end,
            geometry,
        )
        next_angle = screen_segment_angle_degrees(
            line_end,
            next_end,
            geometry,
        )
        return abs(next_angle - previous_angle)

    for window in windows:
        run_start = 0
        while run_start < len(window) - 2:
            first_direction = direction(
                window[run_start],
                window[run_start + 1],
            )
            if first_direction == 0:
                run_start += 1
                continue

            # Find the full monotonic run first.
            run_end = run_start + 1
            while run_end + 1 < len(window):
                next_direction = direction(
                    window[run_end],
                    window[run_end + 1],
                )
                if next_direction != first_direction:
                    break
                run_end += 1

            run = window[run_start:run_end + 1]
            if len(run) < 3:
                run_start = run_end
                continue

            if geometry is None:
                delete_keys.update(key(point) for point in run[1:-1])
                run_start = run_end
                continue

            # First interior angle is deliberately ignored: p0->p1->p2 is
            # collapsed to p0->p2 without an angle test.
            accepted_start = run[0]
            accepted_end = run[2]
            delete_keys.add(key(run[1]))

            index = 3
            while index < len(run):
                candidate = run[index]
                bend_angle = angle_difference(
                    accepted_start,
                    accepted_end,
                    candidate,
                )

                if bend_angle <= angle_threshold_deg:
                    # Continue the same consolidated line. The previous end is
                    # now an interior point and can be removed.
                    delete_keys.add(key(accepted_end))
                    accepted_end = candidate
                    index += 1
                    continue

                # Preserve the bend point. Start a new same-direction run from
                # it; the first interior angle of that new run is again ignored.
                new_start_index = index - 1
                if new_start_index + 2 >= len(run):
                    break

                accepted_start = run[new_start_index]
                accepted_end = run[new_start_index + 2]
                delete_keys.add(key(run[new_start_index + 1]))
                index = new_start_index + 3

            run_start = run_end

    # A protected point may participate in the calculation but may never be
    # deleted from the output.
    delete_keys.difference_update(protected_keys)

    if not delete_keys:
        return result

    surviving_markers = [
        point for point in result.markers
        if key(point) not in delete_keys
    ]
    surviving_keys = {key(point) for point in surviving_markers}
    if not surviving_keys.issubset(input_map):
        raise RuntimeError("stage6 cleanup created a point absent from stage5")

    connected_survivors = [
        point for point in line_points
        if key(point) in surviving_keys
    ]

    exact_kind = {
        (key(segment.start), key(segment.end)): segment.kind
        for segment in result.segments
    }
    rebuilt_segments: list[SimplifiedLineSegment] = []
    for start_point, end_point in zip(
        connected_survivors,
        connected_survivors[1:],
    ):
        kind = exact_kind.get(
            (key(start_point), key(end_point)),
            "trend",
        )
        rebuilt_segments.append(
            SimplifiedLineSegment(
                start=start_point,
                end=end_point,
                kind=kind,
            )
        )

    marker_map = {
        key(point): point
        for point in surviving_markers
    }
    return SimplifiedLineResult(
        markers=tuple(sorted(
            marker_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(rebuilt_segments),
        marker_only_points=result.marker_only_points,
        protected_points=result.protected_points,
    )

