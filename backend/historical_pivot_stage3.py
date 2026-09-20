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

    # Normal-wave merge happens BEFORE any cross-side connection is created.
    # When the upper and lower RDP lines move in the same direction over the
    # same time region, they describe one wave, not two turns to be connected.
    #
    # Up wave   : keep the wave's first LOW and last HIGH.
    # Down wave : keep the wave's first HIGH and last LOW.
    #
    # Every ordinary point that merely follows inside that same wave is removed
    # before the single-line builder sees it. Protected spike/sideways points
    # block this collapse.
    protected_wave_keys = {
        point_key(point)
        for sideways in (*augmented.high_sideways_segments, *augmented.low_sideways_segments)
        for point in sideways.pivot_points
    }
    for spike in augmented.spike_peaks:
        protected_wave_keys.add(point_key(spike.point))
        if spike.entry is not None:
            protected_wave_keys.add(point_key(spike.entry))

    def leg_direction(left: PivotPoint, right: PivotPoint) -> int:
        if right.value > left.value:
            return 1
        if right.value < left.value:
            return -1
        return 0

    high_legs = [
        (index, left, right, leg_direction(left, right))
        for index, (left, right) in enumerate(zip(line_highs, line_highs[1:]))
        if leg_direction(left, right) != 0
    ]
    low_legs = [
        (index, left, right, leg_direction(left, right))
        for index, (left, right) in enumerate(zip(line_lows, line_lows[1:]))
        if leg_direction(left, right) != 0
    ]

    # Match each upper leg to at most one lower leg (and vice versa), choosing
    # the strongest same-direction time overlap. This avoids joining two separate
    # waves merely because their leg indexes happen to be nearby.
    wave_candidates: list[tuple[int, int, int, int, date, date]] = []
    for high_index, high_left, high_right, high_dir in high_legs:
        for low_index, low_left, low_right, low_dir in low_legs:
            if high_dir != low_dir:
                continue
            overlap_start = max(high_left.day, low_left.day)
            overlap_end = min(high_right.day, low_right.day)
            if overlap_start > overlap_end:
                continue
            overlap_days = (overlap_end - overlap_start).days + 1
            wave_candidates.append(
                (
                    overlap_days,
                    high_index,
                    low_index,
                    high_dir,
                    overlap_start,
                    overlap_end,
                )
            )

    used_high_legs: set[int] = set()
    used_low_legs: set[int] = set()
    matched_nodes: list[tuple[int, int, int, date, date]] = []
    for (
        overlap_days,
        high_index,
        low_index,
        direction,
        overlap_start,
        overlap_end,
    ) in sorted(wave_candidates, reverse=True):
        if high_index in used_high_legs or low_index in used_low_legs:
            continue
        used_high_legs.add(high_index)
        used_low_legs.add(low_index)
        matched_nodes.append(
            (high_index, low_index, direction, overlap_start, overlap_end)
        )

    # Only strictly consecutive matched leg pairs can extend the SAME normal wave.
    # A missing/mismatched leg ends the wave instead of allowing a transitive jump.
    matched_nodes.sort(key=lambda item: (item[3], item[4], item[0], item[1]))
    components: list[list[tuple[int, int, int, date, date]]] = []
    for node in matched_nodes:
        if not components:
            components.append([node])
            continue
        prev = components[-1][-1]
        if (
            node[2] == prev[2]
            and node[0] == prev[0] + 1
            and node[1] == prev[1] + 1
        ):
            components[-1].append(node)
        else:
            components.append([node])

    normal_wave_removed: set[tuple[date, float, str]] = set()
    normal_wave_kept: set[tuple[date, float, str]] = set()
    normal_wave_segments: list[SimplifiedLineSegment] = []

    for component in components:
        direction = component[0][2]
        high_indexes = sorted({item[0] for item in component})
        low_indexes = sorted({item[1] for item in component})

        high_points = {
            point_key(point): point
            for index in high_indexes
            for point in (line_highs[index], line_highs[index + 1])
        }
        low_points = {
            point_key(point): point
            for index in low_indexes
            for point in (line_lows[index], line_lows[index + 1])
        }

        if direction > 0:
            start = min(low_points.values(), key=lambda item: item.day)
            end = max(high_points.values(), key=lambda item: item.day)
        else:
            start = min(high_points.values(), key=lambda item: item.day)
            end = max(low_points.values(), key=lambda item: item.day)

        if start.day >= end.day:
            continue

        keep_keys = {point_key(start), point_key(end)}
        component_points = {
            **high_points,
            **low_points,
        }

        # Delete only points that actually belong to the merged wave interval.
        # A same-side endpoint that occurs AFTER the merged wave endpoint belongs
        # to the next wave and must remain available there.
        delete_keys = {
            point_key(point)
            for point in component_points.values()
            if start.day <= point.day <= end.day
            and point_key(point) not in keep_keys
        }

        # The redundant leading boundary point on the opposite side is also part
        # of the same normal wave even when it occurs just before the retained
        # start point (e.g. H1 before L1 in an upward wave).
        if direction > 0:
            leading = min(high_points.values(), key=lambda item: item.day)
        else:
            leading = min(low_points.values(), key=lambda item: item.day)
        if leading.day < start.day:
            delete_keys.add(point_key(leading))

        # A protected point is never silently swallowed inside a normal wave.
        if delete_keys & protected_wave_keys:
            continue

        normal_wave_kept.update(keep_keys)
        normal_wave_removed.update(delete_keys)
        normal_wave_segments.append(
            SimplifiedLineSegment(start=start, end=end, kind="trend")
        )

    # Keep wins when adjacent waves share a true turning endpoint.
    normal_wave_removed.difference_update(normal_wave_kept)

    if normal_wave_removed:
        line_highs = tuple(
            item for item in line_highs
            if point_key(item) not in normal_wave_removed
        )
        line_lows = tuple(
            item for item in line_lows
            if point_key(item) not in normal_wave_removed
        )

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
                # No later high remains, but a later low can still be the final
                # confirmed endpoint of the trailing down wave.
                final_low = next_valid_opposite(lows, anchor.day, "down")
                if final_low is not None:
                    add_segment(anchor, final_low, "trend")
                    anchor = final_low
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
            # Mirror case: no later low remains, but a later high can still be
            # the final endpoint of the trailing up wave.
            final_high = next_valid_opposite(highs, anchor.day, "up")
            if final_high is not None:
                add_segment(anchor, final_high, "trend")
                anchor = final_high
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

    if normal_wave_segments:
        # Stage 3 normal-wave decisions are authoritative. The legacy connector
        # may fill gaps outside them, but it may not cross or replace a confirmed
        # normal wave or skip a turning endpoint shared by two waves.
        wave_segments = sorted(
            normal_wave_segments,
            key=lambda item: (item.start.day, item.end.day),
        )
        wave_endpoint_days = {
            point.day
            for segment in wave_segments
            for point in (segment.start, segment.end)
        }

        def crosses_wave_structure(segment: SimplifiedLineSegment) -> bool:
            if any(
                wave.start.day <= segment.start.day
                and segment.end.day <= wave.end.day
                for wave in wave_segments
            ):
                return True
            return any(
                segment.start.day < endpoint_day < segment.end.day
                for endpoint_day in wave_endpoint_days
            )

        segments = [
            segment
            for segment in segments
            if not crosses_wave_structure(segment)
        ]
        for wave in wave_segments:
            add_segment(wave.start, wave.end, "trend")

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

    # Hard Stage 3 boundary: this stage may only delete from Stage 2's sealed set.
    stage1_keys = {
        (item.day, item.value, item.pivot_type)
        for item in augmented.display_markers
    }
    stage2_keys = {
        (item.day, item.value, item.pivot_type)
        for item in simplified.markers
    }
    if not stage2_keys.issubset(stage1_keys):
        raise RuntimeError("stage3 merge created a point absent from stage2")

    return simplified


