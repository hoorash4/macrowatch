"""Stage 4: 10-degree simplification over Stage 3's single-line points only."""
from __future__ import annotations

from datetime import date
from typing import Sequence

from historical_pivot_shared import (
    ChartGeometry,
    PivotPoint,
    SidewaysSegment,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    SAME_TREND_ANGLE_THRESHOLD_DEG,
    screen_origin_angle_degrees,
)

def prune_same_trend_extremes(
    result: SimplifiedLineResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SAME_TREND_ANGLE_THRESHOLD_DEG,
) -> SimplifiedLineResult:
    """Additional deletion-only pass over the already simplified path.

    The existing simplification is left intact first. This pass only collapses a
    confirmed directional run between an existing transition extreme and a later
    same-trend extreme.

    Uptrend:
      - anchor = an existing low that is lower than the adjacent surviving lows
      - inspect later highs only
      - lower/equal highs are ignored
      - only strict new highs participate
      - compare consecutive record highs from the fixed low anchor
      - every comparison must be <= 10 degrees
      - at least TWO successful record-high updates are required before collapsing
        anything (H1->H2 and H2->H3)
      - when a new record high exceeds 10 degrees, stop before that high

    Downtrend is the exact mirror using a local high anchor and strict new lows.

    Sideways and spike segments are hard trend boundaries. The scan stops before
    them. Once an extreme is confirmed, every ordinary segment/marker strictly
    between anchor and extreme is removed and replaced by one direct trend segment.
    Spike peaks and sideways structure are never deleted.
    """
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")

    def key(point: PivotPoint) -> tuple[date, float, str]:
        return point.day, point.value, point.pivot_type

    # This stage may only inspect points that survived the immediately previous
    # stage. Never re-inject points from augmented/base/raw candidate sets.
    point_map = {key(marker): marker for marker in result.markers}
    points = tuple(sorted(
        point_map.values(),
        key=lambda item: (item.day, item.pivot_type),
    ))

    segment_endpoint_keys = {
        key(point)
        for segment in result.segments
        for point in (segment.start, segment.end)
    }
    # Standalone markers are points intentionally emitted by the previous stage
    # without a connecting segment (for example marker-only sideways spikes).
    # They remain protected, but protection never prevents them from acting as
    # an anchor if they are otherwise a valid surviving point.
    standalone_marker_keys = {
        key(marker)
        for marker in result.markers
        if key(marker) not in segment_endpoint_keys
    }
    highs = tuple(item for item in points if item.pivot_type == "high")
    lows = tuple(item for item in points if item.pivot_type == "low")

    # A spike still stops the active trend at its start. A sideways segment is
    # different: both boundary pivots remain part of the just-finished trend, so
    # the 10-degree scan may inspect through the sideways END but never beyond it.
    hard_boundaries = tuple(sorted(
        (
            segment.start.day
            if segment.kind == "spike"
            else segment.end.day
        )
        for segment in result.segments
        if segment.kind in {"sideways", "spike"}
    ))

    def first_boundary_after(day: date) -> date | None:
        return next((item for item in hard_boundaries if item > day), None)

    # Only a fully confirmed trend reversal may create a NEW anchor.
    #
    # Stage 3 already supplies one merged chronological line.  Stage 4 therefore
    # does NOT carry one global trend state from the chart start.  Doing so makes
    # a missed early reversal hide every later valid anchor.
    #
    # Instead, each Stage-3 turning point is independently confirmed:
    #
    # low -> up anchor:
    #   rebound high -> higher low -> later higher high
    #   any lower low before confirmation cancels the candidate
    #
    # high -> down anchor:
    #   pullback low -> lower high -> later lower low
    #   any higher high before confirmation cancels the candidate
    #
    # This is the exact provisional-reversal rule and its mirror.
    ordered_points = list(points)
    sideways_segments_sorted = tuple(sorted(
        result.sideways_segments,
        key=lambda item: (item.start.day, item.end.day),
    ))

    def sideways_end_between(start_day: date, confirm_day: date) -> PivotPoint | None:
        matches = [
            item.end
            for item in sideways_segments_sorted
            if start_day <= item.start.day
            and item.end.day <= confirm_day
        ]
        return matches[-1] if matches else None

    def initial_direction() -> str | None:
        if len(ordered_points) < 2:
            return None
        first = ordered_points[0]
        for item in ordered_points[1:]:
            if item.value > first.value:
                return "up"
            if item.value < first.value:
                return "down"
        return None

    def confirm_low_anchor(index: int) -> tuple[PivotPoint, str] | None:
        candidate = ordered_points[index]
        if candidate.pivot_type != "low":
            return None

        rebound_high: PivotPoint | None = None
        higher_low_seen = False

        for item in ordered_points[index + 1:]:
            if item.pivot_type == "low":
                # Old downtrend made a new low: candidate reversal is cancelled.
                if item.value < candidate.value:
                    return None
                if rebound_high is not None and item.value > candidate.value:
                    higher_low_seen = True
                continue

            if rebound_high is None:
                rebound_high = item
                continue

            if higher_low_seen and item.value > rebound_high.value:
                anchor = sideways_end_between(candidate.day, item.day) or candidate
                return anchor, "up"

            if not higher_low_seen and item.value > rebound_high.value:
                rebound_high = item

        return None

    def confirm_high_anchor(index: int) -> tuple[PivotPoint, str] | None:
        candidate = ordered_points[index]
        if candidate.pivot_type != "high":
            return None

        pullback_low: PivotPoint | None = None
        lower_high_seen = False

        for item in ordered_points[index + 1:]:
            if item.pivot_type == "high":
                # Old uptrend made a new high: candidate reversal is cancelled.
                if item.value > candidate.value:
                    return None
                if pullback_low is not None and item.value < candidate.value:
                    lower_high_seen = True
                continue

            if pullback_low is None:
                pullback_low = item
                continue

            if lower_high_seen and item.value < pullback_low.value:
                anchor = sideways_end_between(candidate.day, item.day) or candidate
                return anchor, "down"

            if not lower_high_seen and item.value < pullback_low.value:
                pullback_low = item

        return None

    run_starts: list[tuple[PivotPoint, str]] = []

    first_direction = initial_direction()
    if ordered_points and first_direction is not None:
        run_starts.append((ordered_points[0], first_direction))

    for index in range(len(ordered_points)):
        confirmed = (
            confirm_low_anchor(index)
            if ordered_points[index].pivot_type == "low"
            else confirm_high_anchor(index)
        )
        if confirmed is not None:
            run_starts.append(confirmed)

    # A point may be encountered again when a sideways END or an already
    # simplified line turn becomes the confirmed reversal anchor. Keep only its
    # latest confirmed direction.
    run_start_map = {
        key(anchor): (anchor, run_direction)
        for anchor, run_direction in run_starts
    }
    run_starts = sorted(
        run_start_map.values(),
        key=lambda item: item[0].day,
    )

    replacement_intervals: list[tuple[PivotPoint, PivotPoint]] = []

    def already_inside(day: date) -> bool:
        return any(start.day < day < end.day for start, end in replacement_intervals)

    for anchor, direction in run_starts:
        if already_inside(anchor.day):
            continue

        structural_boundary = first_boundary_after(anchor.day)
        next_reversal_day = next(
            (
                later_anchor.day
                for later_anchor, _ in run_starts
                if later_anchor.day > anchor.day
            ),
            None,
        )
        boundary_candidates = [
            item
            for item in (structural_boundary, next_reversal_day)
            if item is not None
        ]
        boundary = min(boundary_candidates) if boundary_candidates else None

        same_side = highs if direction == "up" else lows
        candidates = [
            item for item in same_side
            if item.day > anchor.day
            and (boundary is None or item.day <= boundary)
        ]
        if not candidates:
            continue

        # Connection candidates and angle ordinals are intentionally separate.
        #
        # A lower high during an up-run (or a higher low during a down-run) is
        # NOT a new connection extreme, but it still consumes an angle ordinal.
        # Therefore the first visible angle is ignored regardless of size, while
        # the second and later visible angles are subject to the 10-degree rule.
        #
        # Example (up-run): H1 -> lower H2 -> higher H3
        #   - H2 is skipped as a connection extreme
        #   - angle H1-anchor-H2 is still angle #1 and is ignored
        #   - angle H1-anchor-H3 is angle #2 and must pass the threshold
        #
        # Existing simplify_pivot_lines() already handles provisional 100%+
        # retracement/reversal structure by following the later same-side extreme;
        # do not duplicate that state machine here.
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

            # Angle #1 never stops the run. Angle #2 onward does.
            if angle_ordinal >= 2 and angle > angle_threshold_deg:
                break

            improves = (
                candidate.value > extreme.value
                if direction == "up"
                else candidate.value < extreme.value
            )
            if improves:
                extreme = candidate

        # No later same-side candidate improved on the first extreme, so there is
        # nothing to collapse to. The first angle never blocks simplification:
        # when the next candidate improves the extreme, collapse through it
        # immediately. The 10-degree rule starts with angle #2.
        if extreme == candidates[0]:
            continue

        sideways_keys = {
            key(point)
            for sideways in result.sideways_segments
            for point in sideways.pivot_points
        }
        structure_protected_keys = standalone_marker_keys | sideways_keys

        # The first connection extreme disappears when we collapse to the final
        # extreme. Preserve it when it is a protected spike/sideways boundary.
        # The final extreme itself may be protected because it remains the endpoint.
        if key(candidates[0]) in structure_protected_keys:
            continue

        if anchor.day >= extreme.day:
            continue
        if boundary is not None and extreme.day > boundary:
            continue

        # Spike peaks and both sideways boundary pivots are always preserved.
        # A collapse may not cross any of those protected structure points.
        protected_structure_points = [
            item for item in points
            if key(item) in structure_protected_keys
        ]
        if any(
            anchor.day < marker.day < extreme.day
            for marker in protected_structure_points
        ):
            continue

        interior_points = [
            item for item in points
            if anchor.day < item.day < extreme.day
            and key(item) not in structure_protected_keys
        ]
        if not interior_points:
            continue

        replacement_intervals.append((anchor, extreme))

    if not replacement_intervals:
        return result

    replacement_intervals.sort(key=lambda item: (item[0].day, item[1].day))
    non_overlapping: list[tuple[PivotPoint, PivotPoint]] = []
    for start, end in replacement_intervals:
        if non_overlapping and start.day < non_overlapping[-1][1].day:
            continue
        non_overlapping.append((start, end))
    replacement_intervals = non_overlapping

    def inside_interval(segment: SimplifiedLineSegment) -> bool:
        return any(
            start.day <= segment.start.day
            and segment.end.day <= end.day
            for start, end in replacement_intervals
        )

    kept_segments = [
        segment for segment in result.segments
        if not inside_interval(segment)
    ]
    for start, end in replacement_intervals:
        kept_segments.append(
            SimplifiedLineSegment(start=start, end=end, kind="trend")
        )
    kept_segments.sort(key=lambda item: (item.start.day, item.end.day, item.kind))

    marker_map: dict[tuple[date, float, str], PivotPoint] = {}
    for segment in kept_segments:
        marker_map[key(segment.start)] = segment.start
        marker_map[key(segment.end)] = segment.end
    for marker in result.markers:
        if key(marker) in standalone_marker_keys:
            marker_map[key(marker)] = marker

    # Deletion-only invariant: this stage may never resurrect a point that the
    # previous stage did not return.
    if not set(marker_map).issubset(point_map):
        raise RuntimeError("prune_same_trend_extremes resurrected a prior-stage point")

    return SimplifiedLineResult(
        markers=tuple(sorted(
            marker_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(kept_segments),
        sideways_segments=result.sideways_segments,
    )




