"""Stage 4: 10-degree simplification over Stage 3's single line.

This file intentionally contains one Stage-4 algorithm only.

Input:
- Stage 3 surviving line vertices / segments
- protected sideways / spike structure already present in Stage 3

Rule:
- work continuously from the current confirmed anchor even before the next
  reversal is fully confirmed
- keep tracking the current run's same-direction record extreme
- a provisional opposite turn is cancelled if the old trend makes a new extreme
- when a reversal is fully confirmed, that turn becomes the new anchor
- from each anchor, the first angle is ignored
- from the second angle onward, <= 10 degrees may collapse through to the newer
  same-direction extreme; > 10 degrees stops before that extreme
- deletion only: Stage 4 can never create a point absent from Stage 3
"""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class _Run:
    anchor: PivotPoint
    direction: str  # up | down
    first_extreme: PivotPoint | None = None
    current_extreme: PivotPoint | None = None
    angle_ordinal: int = 0
    collapse_end: PivotPoint | None = None


def prune_same_trend_extremes(
    result: SimplifiedLineResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SAME_TREND_ANGLE_THRESHOLD_DEG,
) -> SimplifiedLineResult:
    """Apply the approved 10-degree rule to Stage 3's single line."""
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")

    points = tuple(sorted(
        result.markers,
        key=lambda item: (item.day, item.pivot_type),
    ))
    if len(points) < 2:
        return result

    point_map = {_key(point): point for point in points}
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
    spike_segment_keys = {
        _key(point)
        for segment in result.segments
        if segment.kind == "spike"
        for point in (segment.start, segment.end)
    }
    protected_keys = standalone_keys | sideways_keys | spike_segment_keys

    # Hard structure boundaries split Stage 4 processing.  Sideways END belongs
    # to the just-finished run; a spike starts a hard interruption at its entry.
    hard_boundary_days = set()
    for segment in result.segments:
        if segment.kind == "sideways":
            hard_boundary_days.add(segment.end.day)
        elif segment.kind == "spike":
            hard_boundary_days.add(segment.start.day)

    def initial_direction(start_index: int) -> str | None:
        first = points[start_index]
        for item in points[start_index + 1:]:
            if item.day in hard_boundary_days and item.day != first.day:
                break
            if item.value > first.value:
                return "up"
            if item.value < first.value:
                return "down"
        return None

    replacement_intervals: list[tuple[PivotPoint, PivotPoint]] = []

    index = 0
    while index < len(points) - 1:
        direction = initial_direction(index)
        if direction is None:
            break

        run = _Run(anchor=points[index], direction=direction)

        # Provisional reversal state.
        pullback_low: PivotPoint | None = None
        lower_high_seen = False
        rebound_high: PivotPoint | None = None
        higher_low_seen = False

        cursor = index + 1
        next_anchor: PivotPoint | None = None
        next_direction: str | None = None

        while cursor < len(points):
            item = points[cursor]

            if item.day in hard_boundary_days and item.day > run.anchor.day:
                break

            if run.direction == "up":
                if item.pivot_type == "high":
                    if (
                        run.current_extreme is None
                        or item.value > run.current_extreme.value
                    ):
                        if run.current_extreme is None:
                            run.first_extreme = item
                            run.current_extreme = item
                        else:
                            run.angle_ordinal += 1
                            angle = screen_origin_angle_degrees(
                                run.anchor,
                                run.current_extreme,
                                item,
                                geometry,
                            )

                            # First angle is always ignored.
                            if (
                                run.angle_ordinal >= 2
                                and angle > angle_threshold_deg
                            ):
                                break

                            run.current_extreme = item
                            run.collapse_end = item

                        pullback_low = None
                        lower_high_seen = False
                    elif pullback_low is not None:
                        lower_high_seen = True

                    cursor += 1
                    continue

                # low during up-run = provisional reversal candidate
                if run.current_extreme is None:
                    cursor += 1
                    continue

                if pullback_low is None:
                    pullback_low = item
                    cursor += 1
                    continue

                if lower_high_seen and item.value < pullback_low.value:
                    # Full up -> down reversal confirmed.
                    next_anchor = run.current_extreme
                    next_direction = "down"
                    break

                if item.value < pullback_low.value:
                    pullback_low = item

                cursor += 1
                continue

            # run.direction == "down"
            if item.pivot_type == "low":
                if (
                    run.current_extreme is None
                    or item.value < run.current_extreme.value
                ):
                    if run.current_extreme is None:
                        run.first_extreme = item
                        run.current_extreme = item
                    else:
                        run.angle_ordinal += 1
                        angle = screen_origin_angle_degrees(
                            run.anchor,
                            run.current_extreme,
                            item,
                            geometry,
                        )

                        # First angle is always ignored.
                        if (
                            run.angle_ordinal >= 2
                            and angle > angle_threshold_deg
                        ):
                            break

                        run.current_extreme = item
                        run.collapse_end = item

                    rebound_high = None
                    higher_low_seen = False
                elif rebound_high is not None:
                    higher_low_seen = True

                cursor += 1
                continue

            # high during down-run = provisional reversal candidate
            if run.current_extreme is None:
                cursor += 1
                continue

            if rebound_high is None:
                rebound_high = item
                cursor += 1
                continue

            if higher_low_seen and item.value > rebound_high.value:
                # Full down -> up reversal confirmed.
                next_anchor = run.current_extreme
                next_direction = "up"
                break

            if item.value > rebound_high.value:
                rebound_high = item

            cursor += 1

        if (
            run.collapse_end is not None
            and run.anchor.day < run.collapse_end.day
        ):
            interior = [
                point
                for point in points
                if run.anchor.day < point.day < run.collapse_end.day
            ]
            if (
                interior
                and not any(_key(point) in protected_keys for point in interior)
                and _key(run.first_extreme) not in protected_keys
            ):
                replacement_intervals.append(
                    (run.anchor, run.collapse_end)
                )

        if next_anchor is not None and next_direction is not None:
            try:
                index = points.index(next_anchor)
            except ValueError:
                break
            continue

        # If no confirmed reversal occurred, continue scanning after the last
        # processed point only when there is still unprocessed structure.
        if cursor <= index:
            break
        index = max(cursor, index + 1)

    if not replacement_intervals:
        return result

    # Remove overlaps, keeping the earliest run replacement.
    replacement_intervals.sort(
        key=lambda item: (item[0].day, item[1].day)
    )
    non_overlapping: list[tuple[PivotPoint, PivotPoint]] = []
    for start, end in replacement_intervals:
        if non_overlapping and start.day < non_overlapping[-1][1].day:
            continue
        non_overlapping.append((start, end))
    replacement_intervals = non_overlapping

    def inside_replacement(segment: SimplifiedLineSegment) -> bool:
        return any(
            start.day <= segment.start.day
            and segment.end.day <= end.day
            for start, end in replacement_intervals
        )

    kept_segments = [
        segment
        for segment in result.segments
        if not inside_replacement(segment)
    ]
    for start, end in replacement_intervals:
        kept_segments.append(
            SimplifiedLineSegment(
                start=start,
                end=end,
                kind="trend",
            )
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
