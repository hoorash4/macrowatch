"""Stage 4: confirmed-run 10-degree simplification.

Stage 4 receives only Stage 3's single merged line.

One algorithm:
1. Track the current trend and a provisional opposite reversal.
2. A provisional reversal is cancelled if the old trend makes a new extreme.
3. A reversal is confirmed only after:
   - opposite extreme,
   - retracement that does not break the turn,
   - another extreme in the new direction.
4. The confirmed turn becomes the next run anchor.
5. Within every active/confirmed run, same-direction record extremes are simplified:
   - first angle is always ignored,
   - second and later angles collapse only while <= 10 degrees,
   - > 10 degrees starts a new same-direction angle run at the previous extreme.

Stage 4 is deletion-only and never reads Stage 2/1/raw candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class _Pending:
    direction: str
    turn: PivotPoint
    first_extreme: PivotPoint
    current_extreme: PivotPoint
    retrace_seen: bool = False


@dataclass(frozen=True)
class _Anchor:
    point: PivotPoint
    direction: str


def _direction_from(points: Sequence[PivotPoint]) -> str | None:
    if len(points) < 2:
        return None
    # A valid run anchor owns the direction by its side:
    # low starts/continues an up-run, high starts/continues a down-run.
    # Never infer "up" from a HIGH merely because the next value is higher,
    # or "down" from a LOW merely because the next value is lower.
    return "up" if points[0].pivot_type == "low" else "down"


def _confirmed_anchors(points: Sequence[PivotPoint]) -> list[_Anchor]:
    """Find run anchors while continuously tracking provisional reversals."""
    ordered = list(points)
    direction = _direction_from(ordered)
    if not ordered or direction is None:
        return []

    anchors: list[_Anchor] = [_Anchor(ordered[0], direction)]
    active_extreme: PivotPoint | None = None
    pending: _Pending | None = None

    def improves_active(item: PivotPoint) -> bool:
        if active_extreme is None:
            return True
        return (
            item.value > active_extreme.value
            if direction == "up"
            else item.value < active_extreme.value
        )

    for item in ordered[1:]:
        wanted_type = "high" if direction == "up" else "low"

        if pending is None:
            if item.pivot_type == wanted_type:
                if improves_active(item):
                    active_extreme = item
                continue

            if active_extreme is None:
                continue

            pending = _Pending(
                direction="down" if direction == "up" else "up",
                turn=active_extreme,
                first_extreme=item,
                current_extreme=item,
            )
            continue

        # Old trend resumes: provisional reversal is cancelled.
        if direction == "up":
            if (
                item.pivot_type == "high"
                and item.value > pending.turn.value
            ):
                active_extreme = item
                pending = None
                continue

            if pending.direction == "down":
                if item.pivot_type == "high":
                    if item.value < pending.turn.value:
                        pending.retrace_seen = True
                    continue

                # low in provisional down-run
                if item.value < pending.current_extreme.value:
                    if pending.retrace_seen:
                        anchors.append(_Anchor(pending.turn, "down"))
                        direction = "down"
                        active_extreme = item
                        pending = None
                    else:
                        pending.current_extreme = item
                continue

        else:
            if (
                item.pivot_type == "low"
                and item.value < pending.turn.value
            ):
                active_extreme = item
                pending = None
                continue

            if pending.direction == "up":
                if item.pivot_type == "low":
                    if item.value > pending.turn.value:
                        pending.retrace_seen = True
                    continue

                # high in provisional up-run
                if item.value > pending.current_extreme.value:
                    if pending.retrace_seen:
                        anchors.append(_Anchor(pending.turn, "up"))
                        direction = "up"
                        active_extreme = item
                        pending = None
                    else:
                        pending.current_extreme = item
                continue

    # Deduplicate in chronological order; later confirmation for the same point wins.
    by_key = {
        _key(anchor.point): anchor
        for anchor in anchors
    }
    return sorted(
        by_key.values(),
        key=lambda anchor: anchor.point.day,
    )


def _angle_replacements(
    points: Sequence[PivotPoint],
    anchors: Sequence[_Anchor],
    geometry: ChartGeometry,
    angle_threshold_deg: float,
    protected_keys: set[tuple[date, float, str]],
) -> list[tuple[PivotPoint, PivotPoint]]:
    """Compute only the direct replacement intervals for confirmed/active runs."""
    replacements: list[tuple[PivotPoint, PivotPoint]] = []

    for index, anchor_info in enumerate(anchors):
        anchor = anchor_info.point
        direction = anchor_info.direction
        next_anchor_day = (
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
            and (next_anchor_day is None or point.day <= next_anchor_day)
        ]
        if len(candidates) < 2:
            continue

        run_anchor = anchor
        extreme = candidates[0]
        angle_ordinal = 0
        collapse_end: PivotPoint | None = None

        for candidate in candidates[1:]:
            improves = (
                candidate.value > extreme.value
                if direction == "up"
                else candidate.value < extreme.value
            )
            if not improves:
                continue

            angle_ordinal += 1
            angle = screen_origin_angle_degrees(
                run_anchor,
                extreme,
                candidate,
                geometry,
            )

            if angle_ordinal == 1 or angle <= angle_threshold_deg:
                extreme = candidate
                collapse_end = candidate
                continue

            # >= second angle and >10°: preserve the previous extreme and begin
            # a new same-direction angle run there.
            if collapse_end is not None:
                interior = [
                    point
                    for point in points
                    if run_anchor.day < point.day < collapse_end.day
                ]
                if (
                    interior
                    and not any(_key(point) in protected_keys for point in interior)
                    and _key(extreme) not in protected_keys
                ):
                    replacements.append((run_anchor, collapse_end))

            run_anchor = extreme
            extreme = candidate
            angle_ordinal = 0
            collapse_end = None

        if collapse_end is not None:
            interior = [
                point
                for point in points
                if run_anchor.day < point.day < collapse_end.day
            ]
            if (
                interior
                and not any(_key(point) in protected_keys for point in interior)
                and _key(candidates[0]) not in protected_keys
            ):
                replacements.append((run_anchor, collapse_end))

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

    # Split at protected structure boundaries. No 10-degree collapse may cross them.
    split_days = sorted({
        segment.end.day if segment.kind == "sideways" else segment.start.day
        for segment in result.segments
        if segment.kind in {"sideways", "spike"}
    })

    windows: list[list[PivotPoint]] = []
    start_index = 0
    for split_day in split_days:
        window = [
            point
            for point in points[start_index:]
            if point.day <= split_day
        ]
        if window:
            windows.append(window)
            last = window[-1]
            start_index = points.index(last)
    tail = list(points[start_index:])
    if tail:
        windows.append(tail)

    replacements: list[tuple[PivotPoint, PivotPoint]] = []
    for window in windows:
        anchors = _confirmed_anchors(window)
        replacements.extend(
            _angle_replacements(
                window,
                anchors,
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
