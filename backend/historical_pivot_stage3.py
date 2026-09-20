"""Stage 3: merge Stage 2's sealed points into one line.

The merge logic is intentionally unchanged from the pre-refactor implementation.
Only the input boundary and the hand-off to Stage 4 were separated.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

from historical_pivot_shared import (
    ChartGeometry,
    PivotPoint,
    SidewaysSegment,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    SpikePeak,
    classify_sideways_reference_line,
)
from historical_pivot_stage2 import Stage2Result

def simplify_pivot_lines(
    augmented: Stage2Result,
    geometry: ChartGeometry,
) -> SimplifiedLineResult:
    """Simplify the line path in chronological order.

    Rules:
      - uptrend starts low -> high, then continues high -> high
      - when the next high is lower, the PREVIOUS high owns the down reversal
      - that previous high connects to the first later low that is NOT itself
        a low-side up->down reversal point
      - downtrend is the exact mirror
      - sideways keeps the same side as the prior trend
      - overlapping opposite-side sideways pivots are removed before later links
      - spike entry interrupts the current line, entry -> peak is drawn, then restart
    """
    original_highs = tuple(sorted(augmented.high_pivots, key=lambda item: item.day))
    original_lows = tuple(sorted(augmented.low_pivots, key=lambda item: item.day))
    if not original_highs or not original_lows:
        return SimplifiedLineResult(markers=(), segments=(), sideways_segments=())

    marker_only_spikes = tuple(
        item for item in augmented.spike_peaks if item.marker_only
    )
    marker_only_keys = {
        (item.point.day, item.point.value, item.point.pivot_type)
        for item in marker_only_spikes
    }
    line_highs = tuple(
        item for item in original_highs
        if (item.day, item.value, item.pivot_type) not in marker_only_keys
    )
    line_lows = tuple(
        item for item in original_lows
        if (item.day, item.value, item.pivot_type) not in marker_only_keys
    )

    spikes = tuple(sorted(
        (
            item for item in augmented.spike_peaks
            if not item.marker_only and item.entry is not None
        ),
        key=lambda item: item.entry.day,
    ))

    def point_key(point: PivotPoint) -> tuple[date, float, str]:
        return point.day, point.value, point.pivot_type

    def sideways_pairs(
        points: Sequence[PivotPoint],
        prior_trend: str,
    ) -> tuple[SidewaysSegment, ...]:
        found: list[SidewaysSegment] = []
        for left, right in zip(points, points[1:]):
            segment = classify_sideways_reference_line(
                left, right, prior_trend, geometry,
            )
            if segment is not None:
                found.append(segment)
        return tuple(found)

    high_sideways = sideways_pairs(line_highs, "up")
    low_sideways = sideways_pairs(line_lows, "down")
    removed_keys: set[tuple[date, float, str]] = set()

    def overlaps(left: SidewaysSegment, right: SidewaysSegment) -> bool:
        return left.start.day <= right.end.day and right.start.day <= left.end.day

    def remove_opposite_sideways(selected: SidewaysSegment) -> None:
        opposite = high_sideways if selected.reference_side == "low" else low_sideways
        for other in opposite:
            if overlaps(selected, other):
                removed_keys.add(point_key(other.start))
                removed_keys.add(point_key(other.end))

    highs = list(original_highs)
    lows = list(original_lows)
    consumed_spikes: set[tuple[date, float, str]] = set()
    segments: list[SimplifiedLineSegment] = []
    sideways_segments: list[SidewaysSegment] = []

    def refresh_points() -> None:
        nonlocal highs, lows
        highs = [item for item in line_highs if point_key(item) not in removed_keys]
        lows = [item for item in line_lows if point_key(item) not in removed_keys]

    def add_segment(start: PivotPoint, end: PivotPoint, kind: str) -> None:
        if start.day >= end.day:
            return
        key = (
            start.day, start.value, start.pivot_type,
            end.day, end.value, end.pivot_type, kind,
        )
        if any(
            (
                item.start.day, item.start.value, item.start.pivot_type,
                item.end.day, item.end.value, item.end.pivot_type, item.kind,
            ) == key
            for item in segments
        ):
            return
        segments.append(SimplifiedLineSegment(start=start, end=end, kind=kind))

    def next_after(points: Sequence[PivotPoint], after: date) -> PivotPoint | None:
        return next((item for item in points if item.day > after), None)

    def previous_before(points: Sequence[PivotPoint], before: date) -> PivotPoint | None:
        previous = [item for item in points if item.day < before]
        return previous[-1] if previous else None

    def is_same_direction_turn(
        candidate: PivotPoint,
        points: Sequence[PivotPoint],
        wanted_direction: str,
    ) -> bool:
        """Candidate is the actual same-side turning vertex into wanted_direction."""
        ordered = [item for item in points if item.day <= candidate.day]
        if len(ordered) < 3 or ordered[-1] != candidate:
            return False
        left, pivot, right = ordered[-3], ordered[-2], ordered[-1]

        # The TURN lives at the middle point. We are testing whether candidate
        # is that turning point, so candidate needs a point AFTER it.
        after = next_after(points, candidate.day)
        before = previous_before(points, candidate.day)
        if before is None or after is None:
            return False

        if candidate.pivot_type == "low":
            if wanted_direction == "down":
                return before.value < candidate.value and after.value < candidate.value
            return before.value > candidate.value and after.value > candidate.value

        if wanted_direction == "down":
            return before.value < candidate.value and after.value < candidate.value
        return before.value > candidate.value and after.value > candidate.value

    def next_valid_opposite(
        points: Sequence[PivotPoint],
        after: date,
        wanted_direction: str,
    ) -> PivotPoint | None:
        candidate = next_after(points, after)
        while candidate is not None and is_same_direction_turn(
            candidate,
            points,
            wanted_direction,
        ):
            candidate = next_after(points, candidate.day)
        return candidate

    def next_spike_before(after: date, before: date | None) -> SpikePeak | None:
        for spike in spikes:
            key = (spike.point.day, spike.point.value, spike.direction)
            if key in consumed_spikes or spike.entry is None:
                continue
            if spike.entry.day <= after:
                continue
            if before is not None and spike.entry.day > before:
                continue
            if point_key(spike.entry) in removed_keys or point_key(spike.point) in removed_keys:
                continue
            return spike
        return None

    refresh_points()
    first_high = highs[0]
    first_low = lows[0]

    if first_low.day < first_high.day:
        direction = "up"
        anchor = first_low
        current_same_side = next_after(highs, anchor.day)
    else:
        direction = "down"
        anchor = first_high
        current_same_side = next_after(lows, anchor.day)

    if current_same_side is None:
        return SimplifiedLineResult(markers=(), segments=(), sideways_segments=())

    # Initial cross-side leg.
    add_segment(anchor, current_same_side, "trend")
    anchor = current_same_side

    while True:
        refresh_points()

        if direction == "up":
            next_high = next_after(highs, anchor.day)
            if next_high is None:
                break

            # A spike/restart can leave the current anchor on the opposite side.
            # Complete only that first cross-side leg, then resume high -> high.
            if anchor.pivot_type == "low":
                add_segment(anchor, next_high, "trend")
                anchor = next_high
                continue

            spike = next_spike_before(anchor.day, next_high.day)
            if spike is not None and spike.entry is not None:
                add_segment(anchor, spike.entry, "trend")
                add_segment(spike.entry, spike.point, "spike")
                consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
                anchor = spike.point
                direction = "down" if spike.point.pivot_type == "high" else "up"
                continue

            sideways = classify_sideways_reference_line(anchor, next_high, "up", geometry)
            if sideways is not None:
                remove_opposite_sideways(sideways)
                refresh_points()
                add_segment(anchor, next_high, "sideways")
                sideways_segments.append(sideways)
                anchor = next_high
                continue

            if next_high.value > anchor.value:
                add_segment(anchor, next_high, "trend")
                anchor = next_high
                continue

            # next_high is lower: anchor is the actual high-side reversal owner.
            reversal_owner = anchor
            low_candidate = next_valid_opposite(lows, reversal_owner.day, "down")
            if low_candidate is None:
                break

            spike = next_spike_before(reversal_owner.day, low_candidate.day)
            if spike is not None and spike.entry is not None:
                add_segment(reversal_owner, spike.entry, "trend")
                add_segment(spike.entry, spike.point, "spike")
                consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
                anchor = spike.point
                direction = "down" if spike.point.pivot_type == "high" else "up"
                continue

            add_segment(reversal_owner, low_candidate, "trend")
            anchor = low_candidate
            direction = "down"
            continue

        next_low = next_after(lows, anchor.day)
        if next_low is None:
            break

        # A spike/restart can leave the current anchor on the opposite side.
        # Complete only that first cross-side leg, then resume low -> low.
        if anchor.pivot_type == "high":
            add_segment(anchor, next_low, "trend")
            anchor = next_low
            continue

        spike = next_spike_before(anchor.day, next_low.day)
        if spike is not None and spike.entry is not None:
            add_segment(anchor, spike.entry, "trend")
            add_segment(spike.entry, spike.point, "spike")
            consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
            anchor = spike.point
            direction = "down" if spike.point.pivot_type == "high" else "up"
            continue

        sideways = classify_sideways_reference_line(anchor, next_low, "down", geometry)
        if sideways is not None:
            remove_opposite_sideways(sideways)
            refresh_points()
            add_segment(anchor, next_low, "sideways")
            sideways_segments.append(sideways)
            anchor = next_low
            continue

        if next_low.value < anchor.value:
            add_segment(anchor, next_low, "trend")
            anchor = next_low
            continue

        # next_low is higher: anchor is the actual low-side reversal owner.
        reversal_owner = anchor
        high_candidate = next_valid_opposite(highs, reversal_owner.day, "up")
        if high_candidate is None:
            break

        spike = next_spike_before(reversal_owner.day, high_candidate.day)
        if spike is not None and spike.entry is not None:
            add_segment(reversal_owner, spike.entry, "trend")
            add_segment(spike.entry, spike.point, "spike")
            consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
            anchor = spike.point
            direction = "down" if spike.point.pivot_type == "high" else "up"
            continue

        add_segment(reversal_owner, high_candidate, "trend")
        anchor = high_candidate
        direction = "up"

    segments.sort(key=lambda item: (item.start.day, item.end.day, item.kind))
    sideways_segments.sort(key=lambda item: (item.start.day, item.end.day))

    used_markers: dict[tuple[date, float, str], PivotPoint] = {}
    for segment in segments:
        used_markers[point_key(segment.start)] = segment.start
        used_markers[point_key(segment.end)] = segment.end
    for spike in marker_only_spikes:
        used_markers[point_key(spike.point)] = spike.point

    simplified = SimplifiedLineResult(
        markers=tuple(sorted(
            used_markers.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(segments),
        sideways_segments=tuple(sideways_segments),
    )

    # Hard stage boundary: stage 2 may only delete from the sealed stage-1 set.
    # There is no legal path for a candidate/debug/deleted point to re-enter here.
    stage1_keys = {
        (item.day, item.value, item.pivot_type)
        for item in augmented.display_markers
    }
    stage2_keys = {
        (item.day, item.value, item.pivot_type)
        for item in simplified.markers
    }
    if not stage2_keys.issubset(stage1_keys):
        raise RuntimeError("line simplification resurrected a non-final stage-1 point")

    return simplified


