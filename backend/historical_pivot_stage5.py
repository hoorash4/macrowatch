"""Stage 5: final consecutive-point cleanup over Stage 4 output only."""
from __future__ import annotations

from datetime import date

from historical_pivot_shared import (
    PivotPoint,
    SimplifiedLineResult,
    SimplifiedLineSegment,
)

def prune_unconfirmed_retracements(
    result: SimplifiedLineResult,
) -> SimplifiedLineResult:
    """Stage 5: collapse consecutive points moving in the same direction.

    Input is only Stage 4's single-line result.

    "Consecutive" means consecutive vertices on that line, regardless of whether
    a vertex was originally high or low. At least THREE connected vertices are
    required. If their segment directions remain monotonically up or monotonically
    down, preserve the run's first and last vertex and remove only ordinary
    interior vertices.

    Sideways boundaries, spike endpoints, and standalone no-line markers are
    protected and split the run.
    """
    def key(point: PivotPoint) -> tuple[date, float, str]:
        return point.day, point.value, point.pivot_type

    input_map = {key(point): point for point in result.markers}
    if len(result.markers) < 3:
        return result

    standalone_keys = {
        key(marker)
        for marker in result.markers
        if not any(
            key(marker) in {key(segment.start), key(segment.end)}
            for segment in result.segments
        )
    }
    sideways_keys = {
        key(point)
        for sideways in result.sideways_segments
        for point in sideways.pivot_points
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
    protected_keys = standalone_keys | sideways_keys | spike_keys | rapid_move_keys

    # Use only the actual connected line order. Standalone marker-only points
    # never participate in a directional run.
    line_points: list[PivotPoint] = []
    if result.segments:
        ordered_segments = sorted(
            result.segments,
            key=lambda item: (item.start.day, item.end.day),
        )
        line_points.append(ordered_segments[0].start)
        for segment in ordered_segments:
            if not line_points or line_points[-1] != segment.start:
                line_points.append(segment.start)
            if line_points[-1] != segment.end:
                line_points.append(segment.end)

    if len(line_points) < 3:
        return result

    delete_keys: set[tuple[date, float, str]] = set()
    run_start = 0

    def direction(left: PivotPoint, right: PivotPoint) -> int:
        if right.value > left.value:
            return 1
        if right.value < left.value:
            return -1
        return 0

    while run_start < len(line_points) - 2:
        first_direction = direction(
            line_points[run_start],
            line_points[run_start + 1],
        )
        if first_direction == 0:
            run_start += 1
            continue

        run_end = run_start + 1
        while run_end + 1 < len(line_points):
            next_direction = direction(
                line_points[run_end],
                line_points[run_end + 1],
            )
            if next_direction != first_direction:
                break
            run_end += 1

        if run_end - run_start + 1 >= 3:
            interior = line_points[run_start + 1:run_end]
            # Protected structure breaks the cleanup rather than being crossed.
            if not any(key(point) in protected_keys for point in interior):
                delete_keys.update(key(point) for point in interior)

        run_start = run_end

    if not delete_keys:
        return result

    surviving_markers = [
        point for point in result.markers
        if key(point) not in delete_keys
    ]
    surviving_keys = {key(point) for point in surviving_markers}
    if not surviving_keys.issubset(input_map):
        raise RuntimeError("stage5 cleanup created a point absent from stage4")

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
    surviving_sideways = tuple(
        segment
        for segment in result.sideways_segments
        if key(segment.start) in marker_map
        and key(segment.end) in marker_map
    )

    return SimplifiedLineResult(
        markers=tuple(sorted(
            marker_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(rebuilt_segments),
        sideways_segments=surviving_sideways,
        protected_points=result.protected_points,
    )

