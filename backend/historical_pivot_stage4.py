"""Stage 4: confirmed-anchor 10-degree simplification.

Stage 4 receives only Stage 3's single surviving line.

It has two responsibilities:
1. classify every possible reversal anchor as confirmed, cancelled, or unresolved;
2. apply the approved 10-degree collapse inside each confirmed/provisional run
   without crossing a surviving reversal boundary.

No Stage-1/2 data, deleted point, envelope, or separate high/low RDP track is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from historical_pivot_shared import (
    ChartGeometry,
    PivotPoint,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    SAME_TREND_ANGLE_THRESHOLD_DEG,
    screen_origin_angle_degrees,
)


AnchorStatus = Literal["confirmed", "cancelled", "unresolved"]


@dataclass(frozen=True)
class _AnchorCheck:
    point: PivotPoint
    direction: str  # up | down
    status: AnchorStatus


def _key(point: PivotPoint) -> tuple[date, float, str]:
    return point.day, point.value, point.pivot_type


def _initial_direction(points: list[PivotPoint]) -> str | None:
    if len(points) < 2:
        return None
    first = points[0]
    for point in points[1:]:
        if point.value > first.value:
            return "up"
        if point.value < first.value:
            return "down"
    return None


def _check_low_anchor(
    points: list[PivotPoint],
    index: int,
) -> _AnchorCheck:
    """A low is an up-anchor candidate until confirmed or cancelled.

    confirmed:
      rebound high -> higher low -> later higher high

    cancelled:
      a lower low appears before confirmation
    """
    anchor = points[index]
    rebound_high: PivotPoint | None = None
    higher_low_seen = False

    for point in points[index + 1:]:
        if point.pivot_type == "low":
            if point.value < anchor.value:
                return _AnchorCheck(anchor, "up", "cancelled")
            if rebound_high is not None and point.value > anchor.value:
                higher_low_seen = True
            continue

        if rebound_high is None:
            rebound_high = point
            continue

        if higher_low_seen and point.value > rebound_high.value:
            return _AnchorCheck(anchor, "up", "confirmed")

        # Before the higher low exists, a stronger rebound just replaces H1.
        if not higher_low_seen and point.value > rebound_high.value:
            rebound_high = point

    return _AnchorCheck(anchor, "up", "unresolved")


def _check_high_anchor(
    points: list[PivotPoint],
    index: int,
) -> _AnchorCheck:
    """A high is a down-anchor candidate until confirmed or cancelled.

    confirmed:
      pullback low -> lower high -> later lower low

    cancelled:
      a higher high appears before confirmation
    """
    anchor = points[index]
    pullback_low: PivotPoint | None = None
    lower_high_seen = False

    for point in points[index + 1:]:
        if point.pivot_type == "high":
            if point.value > anchor.value:
                return _AnchorCheck(anchor, "down", "cancelled")
            if pullback_low is not None and point.value < anchor.value:
                lower_high_seen = True
            continue

        if pullback_low is None:
            pullback_low = point
            continue

        if lower_high_seen and point.value < pullback_low.value:
            return _AnchorCheck(anchor, "down", "confirmed")

        # Before the lower high exists, a deeper pullback just replaces L1.
        if not lower_high_seen and point.value < pullback_low.value:
            pullback_low = point

    return _AnchorCheck(anchor, "down", "unresolved")


def prune_same_trend_extremes(
    result: SimplifiedLineResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SAME_TREND_ANGLE_THRESHOLD_DEG,
) -> SimplifiedLineResult:
    """Collapse same-direction extremes using confirmed/provisional anchors."""
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")

    point_map = {_key(point): point for point in result.markers}
    points = list(sorted(
        point_map.values(),
        key=lambda item: (item.day, item.pivot_type),
    ))
    if len(points) < 3:
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

    # Hard structure is always a run boundary.
    hard_boundary_days = {
        point.day
        for point in points
        if _key(point) in protected_keys
    }

    first_direction = _initial_direction(points)
    if first_direction is None:
        return result

    checks: list[_AnchorCheck] = []
    for index, point in enumerate(points):
        checks.append(
            _check_low_anchor(points, index)
            if point.pivot_type == "low"
            else _check_high_anchor(points, index)
        )

    # Initial chart anchor is always a usable run start.
    run_starts: list[tuple[PivotPoint, str]] = [
        (points[0], first_direction)
    ]

    # A confirmed reversal becomes a new anchor.
    for check in checks[1:]:
        if check.status == "confirmed":
            run_starts.append((check.point, check.direction))

    # Unresolved candidates are NOT new anchors yet, but Stage 4 may not collapse
    # across them because their reversal outcome is still unknown.
    unresolved_days = {
        check.point.day
        for check in checks
        if check.status == "unresolved"
        and check.point != points[0]
    }

    # Deduplicate anchors by point; latest classification wins only if identical.
    run_map = {
        _key(point): (point, direction)
        for point, direction in run_starts
    }
    run_starts = sorted(run_map.values(), key=lambda item: item[0].day)

    highs = tuple(point for point in points if point.pivot_type == "high")
    lows = tuple(point for point in points if point.pivot_type == "low")

    replacements: list[tuple[PivotPoint, PivotPoint]] = []

    def inside_existing(day: date) -> bool:
        return any(start.day < day < end.day for start, end in replacements)

    for anchor, direction in run_starts:
        if inside_existing(anchor.day):
            continue

        next_confirmed = next(
            (
                later.day
                for later, _ in run_starts
                if later.day > anchor.day
            ),
            None,
        )
        next_unresolved = next(
            (
                day
                for day in sorted(unresolved_days)
                if day > anchor.day
            ),
            None,
        )
        next_hard = next(
            (
                day
                for day in sorted(hard_boundary_days)
                if day > anchor.day
            ),
            None,
        )

        boundaries = [
            day
            for day in (next_confirmed, next_unresolved, next_hard)
            if day is not None
        ]
        boundary = min(boundaries) if boundaries else None

        same_side = highs if direction == "up" else lows
        candidates = [
            point
            for point in same_side
            if point.day > anchor.day
            and (boundary is None or point.day <= boundary)
        ]
        if len(candidates) < 2:
            continue

        extreme = candidates[0]
        angle_ordinal = 0

        for candidate in candidates[1:]:
            angle_ordinal += 1
            angle = screen_origin_angle_degrees(
                anchor,
                extreme,
                candidate,
                geometry,
            )

            # First interior angle is always ignored.
            # 10-degree threshold begins at angle #2.
            if angle_ordinal >= 2 and angle > angle_threshold_deg:
                break

            improves = (
                candidate.value > extreme.value
                if direction == "up"
                else candidate.value < extreme.value
            )
            if improves:
                extreme = candidate

        if extreme == candidates[0]:
            continue
        if anchor.day >= extreme.day:
            continue
        if boundary is not None and extreme.day > boundary:
            continue

        # Never collapse through protected structure.
        if any(
            anchor.day < point.day < extreme.day
            and _key(point) in protected_keys
            for point in points
        ):
            continue

        replacements.append((anchor, extreme))

    if not replacements:
        return result

    replacements.sort(key=lambda item: (item[0].day, item[1].day))
    non_overlapping: list[tuple[PivotPoint, PivotPoint]] = []
    for start, end in replacements:
        if non_overlapping and start.day < non_overlapping[-1][1].day:
            continue
        non_overlapping.append((start, end))
    replacements = non_overlapping

    def segment_inside_replacement(segment: SimplifiedLineSegment) -> bool:
        return any(
            start.day <= segment.start.day
            and segment.end.day <= end.day
            for start, end in replacements
        )

    kept_segments = [
        segment
        for segment in result.segments
        if not segment_inside_replacement(segment)
    ]
    for start, end in replacements:
        kept_segments.append(
            SimplifiedLineSegment(
                start=start,
                end=end,
                kind="trend",
            )
        )
    kept_segments.sort(key=lambda item: (item.start.day, item.end.day, item.kind))

    marker_map: dict[tuple[date, float, str], PivotPoint] = {}
    for segment in kept_segments:
        marker_map[_key(segment.start)] = segment.start
        marker_map[_key(segment.end)] = segment.end
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
        segments=tuple(kept_segments),
        sideways_segments=surviving_sideways,
    )
