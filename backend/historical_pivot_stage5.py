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
    """Final deletion-only cleanup over the completed 10-degree result.

    "Consecutive" means consecutive vertices in the CURRENT surviving line.
    Highs and lows are never collected into separate timelines.

    Cleanup is allowed only for a run of at least THREE adjacent vertices that:
      - all have the same pivot_type, and
      - move monotonically in one value direction.

    For each such run, preserve the first and last vertex and delete only the
    interior vertices. Two adjacent same-side vertices are never enough.
    Alternating structures such as low->high->low or high->low->high are never
    treated as consecutive same-side runs.

    Sideways/spike/standalone structure is protected. This stage is deletion-only
    and can never restore a point absent from its input.
    """
    def key(point: PivotPoint) -> tuple[date, float, str]:
        return point.day, point.value, point.pivot_type

    input_map = {key(point): point for point in result.markers}
    original = list(sorted(
        result.markers,
        key=lambda item: (item.day, item.pivot_type),
    ))
    if len(original) < 3:
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
    protected_keys = standalone_keys | sideways_keys | spike_keys

    delete_keys: set[tuple[date, float, str]] = set()
    index = 0

    while index < len(original):
        run_end = index + 1
        while (
            run_end < len(original)
            and original[run_end].pivot_type == original[index].pivot_type
        ):
            run_end += 1

        same_type_run = original[index:run_end]
        if len(same_type_run) >= 3:
            sub_start = 0
            while sub_start < len(same_type_run) - 2:
                first_delta = (
                    same_type_run[sub_start + 1].value
                    - same_type_run[sub_start].value
                )
                if first_delta == 0:
                    sub_start += 1
                    continue

                direction = 1 if first_delta > 0 else -1
                sub_end = sub_start + 1
                while sub_end + 1 < len(same_type_run):
                    delta = (
                        same_type_run[sub_end + 1].value
                        - same_type_run[sub_end].value
                    )
                    if delta == 0 or (1 if delta > 0 else -1) != direction:
                        break
                    sub_end += 1

                monotonic_run = same_type_run[sub_start:sub_end + 1]
                if len(monotonic_run) >= 3:
                    interior = monotonic_run[1:-1]
                    if not any(key(point) in protected_keys for point in interior):
                        delete_keys.update(key(point) for point in interior)

                sub_start = sub_end

        index = run_end

    if not delete_keys:
        return result

    ordered = [
        point for point in original
        if key(point) not in delete_keys
    ]

    surviving_keys = {key(point) for point in ordered}
    if not surviving_keys.issubset(input_map):
        raise RuntimeError("final cleanup resurrected a prior-stage point")

    exact_kind = {
        (key(segment.start), key(segment.end)): segment.kind
        for segment in result.segments
    }
    line_points = [point for point in ordered if key(point) not in standalone_keys]
    rebuilt_segments: list[SimplifiedLineSegment] = []
    for start_point, end_point in zip(line_points, line_points[1:]):
        kind = exact_kind.get((key(start_point), key(end_point)), "trend")
        rebuilt_segments.append(
            SimplifiedLineSegment(
                start=start_point,
                end=end_point,
                kind=kind,
            )
        )

    marker_map = {key(point): point for point in ordered}
    surviving_sideways = tuple(
        segment
        for segment in result.sideways_segments
        if key(segment.start) in marker_map and key(segment.end) in marker_map
    )

    return SimplifiedLineResult(
        markers=tuple(sorted(
            marker_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(rebuilt_segments),
        sideways_segments=surviving_sideways,
    )


