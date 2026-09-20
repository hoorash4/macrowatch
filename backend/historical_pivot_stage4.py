"""Stage 4: confirmed-candidate 10-degree simplification.

Stage 4 receives only Stage 3's single merged line.

One algorithm:
- Every Stage-3 turn is a possible reversal anchor.
- A LOW candidate becomes an UP anchor only after:
    rebound HIGH -> higher LOW -> later higher HIGH.
  Any lower LOW before confirmation cancels it.
- A HIGH candidate becomes a DOWN anchor only after:
    pullback LOW -> lower HIGH -> later lower LOW.
  Any higher HIGH before confirmation cancels it.
- Candidate anchors are evaluated independently, so a failed outer reversal does
  not erase a valid inner candidate.
- The first Stage-3 point is the initial anchor; LOW starts up, HIGH starts down.
- From each confirmed/initial anchor, the first angle is ignored.
- Every later same-side point consumes an angle ordinal even when it does not
  improve the connection extreme.
- From angle #2 onward, >10 degrees stops the collapse before that point.
- Stage 4 only deletes; it never creates a point absent from Stage 3.
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


@dataclass(frozen=True)
class _Anchor:
    point: PivotPoint
    direction: str  # up | down


def _confirm_low_anchor(
    points: Sequence[PivotPoint],
    index: int,
) -> _Anchor | None:
    candidate = points[index]
    if candidate.pivot_type != "low":
        return None

    rebound_high: PivotPoint | None = None
    higher_low_seen = False

    for item in points[index + 1:]:
        if item.pivot_type == "low":
            if item.value < candidate.value:
                return None
            if rebound_high is not None and item.value > candidate.value:
                higher_low_seen = True
            continue

        # high
        if rebound_high is None:
            rebound_high = item
            continue

        if higher_low_seen and item.value > rebound_high.value:
            return _Anchor(candidate, "up")

        if not higher_low_seen and item.value > rebound_high.value:
            rebound_high = item

    return None


def _confirm_high_anchor(
    points: Sequence[PivotPoint],
    index: int,
) -> _Anchor | None:
    candidate = points[index]
    if candidate.pivot_type != "high":
        return None

    pullback_low: PivotPoint | None = None
    lower_high_seen = False

    for item in points[index + 1:]:
        if item.pivot_type == "high":
            if item.value > candidate.value:
                return None
            if pullback_low is not None and item.value < candidate.value:
                lower_high_seen = True
            continue

        # low
        if pullback_low is None:
            pullback_low = item
            continue

        if lower_high_seen and item.value < pullback_low.value:
            return _Anchor(candidate, "down")

        if not lower_high_seen and item.value < pullback_low.value:
            pullback_low = item

    return None


def _anchors(points: Sequence[PivotPoint]) -> list[_Anchor]:
    if not points:
        return []

    # Initial anchor direction is determined by its SIDE, never by the next value.
    result: list[_Anchor] = [
        _Anchor(
            points[0],
            "up" if points[0].pivot_type == "low" else "down",
        )
    ]

    for index in range(1, len(points)):
        confirmed = (
            _confirm_low_anchor(points, index)
            if points[index].pivot_type == "low"
            else _confirm_high_anchor(points, index)
        )
        if confirmed is not None:
            result.append(confirmed)

    by_key = {_key(anchor.point): anchor for anchor in result}
    return sorted(by_key.values(), key=lambda item: item.point.day)


def _angle_replacements(
    points: Sequence[PivotPoint],
    anchors: Sequence[_Anchor],
    geometry: ChartGeometry,
    threshold: float,
    protected_keys: set[tuple[date, float, str]],
) -> list[tuple[PivotPoint, PivotPoint]]:
    replacements: list[tuple[PivotPoint, PivotPoint]] = []

    for index, anchor_info in enumerate(anchors):
        anchor = anchor_info.point
        direction = anchor_info.direction
        boundary = (
            anchors[index + 1].point.day
            if index + 1 < len(anchors)
            else None
        )
        wanted_type = "high" if direction == "up" else "low"

        candidates = [
            point
            for point in points
            if point.day > anchor.day
            and point.pivot_type == wanted_type
            and (boundary is None or point.day <= boundary)
        ]
        if len(candidates) < 2:
            continue

        extreme = candidates[0]
        angle_ordinal = 0
        collapse_end: PivotPoint | None = None

        for candidate in candidates[1:]:
            # EVERY same-side candidate consumes an angle ordinal, including a
            # lower high / higher low that is not a connection extreme.
            angle_ordinal += 1
            angle = screen_origin_angle_degrees(
                anchor,
                extreme,
                candidate,
                geometry,
            )

            improves = (
                candidate.value > extreme.value
                if direction == "up"
                else candidate.value < extreme.value
            )

            # First angle is always ignored. From angle #2 onward, a wide angle
            # stops before this candidate.
            if angle_ordinal >= 2 and angle > threshold:
                break

            if improves:
                extreme = candidate
                collapse_end = candidate

        if collapse_end is None:
            continue

        interior = [
            point
            for point in points
            if anchor.day < point.day < collapse_end.day
        ]
        if not interior:
            continue
        if any(_key(point) in protected_keys for point in interior):
            continue
        if _key(candidates[0]) in protected_keys:
            continue

        replacements.append((anchor, collapse_end))

    return replacements


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

    # Hard structure divides Stage 4 into independent windows.
    split_days = sorted({
        segment.end.day if segment.kind == "sideways" else segment.start.day
        for segment in result.segments
        if segment.kind in {"sideways", "spike"}
    })

    windows: list[list[PivotPoint]] = []
    window_start = 0
    for split_day in split_days:
        current = [
            point
            for point in points[window_start:]
            if point.day <= split_day
        ]
        if current:
            windows.append(current)
            # Share the boundary endpoint with the next window.
            window_start = points.index(current[-1])
    tail = list(points[window_start:])
    if tail:
        windows.append(tail)

    replacements: list[tuple[PivotPoint, PivotPoint]] = []
    for window in windows:
        replacements.extend(
            _angle_replacements(
                window,
                _anchors(window),
                geometry,
                angle_threshold_deg,
                protected_keys,
            )
        )

    if not replacements:
        return result

    replacements.sort(key=lambda item: (item[0].day, item[1].day))
    non_overlapping: list[tuple[PivotPoint, PivotPoint]] = []
    for start, end in replacements:
        if non_overlapping and start.day < non_overlapping[-1][1].day:
            continue
        non_overlapping.append((start, end))
    replacements = non_overlapping

    def inside(segment: SimplifiedLineSegment) -> bool:
        return any(
            start.day <= segment.start.day
            and segment.end.day <= end.day
            for start, end in replacements
        )

    kept_segments = [
        segment
        for segment in result.segments
        if not inside(segment)
    ]
    for start, end in replacements:
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
