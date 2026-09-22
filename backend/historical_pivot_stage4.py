"""Stage 4: finalize rapid-move candidates from Stage 2/3.

Stage 4 applies the SAME chronological wave/retracement judgment used by the
general Stage-5 cleanup, but only rapid-move ENTRY points may start an anchor.

For each provisional rapid entry:
- FIRST walk backward through the completed Stage-3 line and test whether an
  earlier opposite-side point can be the same rapid-wave entry;
- whenever backward integration succeeds, replace the entry anchor immediately
  and test again from that NEW anchor;
- only after the earliest valid entry is fixed, walk forward from that new
  anchor through the ENTIRE completed Stage-3 chronology; Stage-2 candidate
  endpoints are never scan boundaries;
- an opposite wave is provisional, not an automatic rapid-move terminator;
- the very next same-side extreme decides it:
  * if it exceeds the previous extreme, the opposite wave was a retracement;
    keep the rapid trend alive and apply the anchor-based 10-degree test;
  * if it does not exceed the previous extreme, the prior extreme is the end
    of this rapid move; later points may not retroactively re-join it;
- hard spike/sideways structure remains protected in output but does not stop
  the rapid-wave judgment scan;
- only a Stage-2 rapid entry can become the next rapid anchor;
- after consolidation, the surviving rapid entry and final rapid peak carry
  structural protection metadata into later cleanup; that metadata is not a
  scan boundary and does not make either vertex unconditionally undeletable.

Stage 4 never performs the general Stage-5 cleanup. Its output contains only
the line, explicit marker-only spikes, and final rapid protected endpoints;
rapid candidates and provisional state end here.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

from historical_pivot_shared import (
    SAME_TREND_ANGLE_THRESHOLD_DEG,
    ChartGeometry,
    LinePoint,
    LineRapidMoveCandidate,
    SimplifiedLineResult,
    Stage3LineResult,
    line_role_map,
    screen_origin_angle_degrees,
    screen_segment_angle_degrees,
)


def _key(point: LinePoint) -> tuple[date, float]:
    return point.day, point.value


def _segment_direction(start: LinePoint, end: LinePoint) -> int:
    if end.value > start.value:
        return 1
    if end.value < start.value:
        return -1
    return 0


def _is_same_side(
    point: LinePoint,
    direction: int,
    roles: dict[tuple[date, float], str | None],
) -> bool:
    return roles.get(_key(point)) == ("high" if direction > 0 else "low")


def _improves(point: LinePoint, extreme: LinePoint, direction: int) -> bool:
    return (
        point.value > extreme.value
        if direction > 0
        else point.value < extreme.value
    )


def _farther_opposite(
    point: LinePoint,
    current: LinePoint,
    direction: int,
) -> bool:
    return (
        point.value < current.value
        if direction > 0
        else point.value > current.value
    )


def _timeline_points(result: Stage3LineResult) -> tuple[LinePoint, ...]:
    """Return only connected Stage-3 line vertices in chronological order."""
    by_key: dict[tuple[date, float], LinePoint] = {}
    for segment in result.segments:
        by_key[_key(segment.start)] = segment.start
        by_key[_key(segment.end)] = segment.end
    return tuple(sorted(
        by_key.values(),
        key=lambda item: item.day,
    ))


def _scan_from_entry(
    result: Stage3LineResult,
    *,
    anchor: LinePoint,
    direction: int,
    through: date | None,
    geometry: ChartGeometry,
    angle_threshold_deg: float,
) -> LinePoint | None:
    """Inspect the rapid wave from its entry using only the merged line.

    The rapid entry is the fixed anchor. A pullback does not end the rapid wave
    merely because direction turned. The rapid wave ends only if the opposite
    move actually crosses the entry anchor. While the anchor holds, a later new
    extreme continues the same rapid wave and is still subject to the existing
    anchor-based 10-degree check.
    """
    points = [
        point
        for point in _timeline_points(result)
        if anchor.day < point.day
        and (through is None or point.day <= through)
    ]
    if not points:
        return None

    extreme: LinePoint | None = None

    for point in points:
        if extreme is None:
            if (
                (direction > 0 and point.value > anchor.value)
                or (direction < 0 and point.value < anchor.value)
            ):
                extreme = point
            continue

        # Crossing the rapid entry anchor is a real trend reversal. Merely
        # turning back inside the anchor/extreme range is only a retracement.
        crossed_anchor = (
            point.value <= anchor.value
            if direction > 0
            else point.value >= anchor.value
        )
        if crossed_anchor:
            # A move that fully crosses its entry is no longer the same rapid
            # structure.  Reject the provisional rapid candidate instead of
            # freezing the prior peak as a protected endpoint.
            return None

        if not _improves(point, extreme, direction):
            continue

        angle = screen_origin_angle_degrees(
            anchor,
            extreme,
            point,
            geometry,
        )
        if angle > angle_threshold_deg:
            break

        extreme = point

    return extreme


def _expand_entry_backward(
    result: Stage3LineResult,
    *,
    initial_entry: LinePoint,
    seed_peak: LinePoint,
    direction: int,
    geometry: ChartGeometry,
    angle_threshold_deg: float,
) -> LinePoint:
    """Move a provisional rapid entry backward before any forward extension.

    The CURRENT entry->seed-peak trend direction is the fixed 10-degree
    reference. An earlier same-side proposal may replace the entry only when
    proposal->the SAME seed peak stays within that existing trend direction by
    the approved angle threshold. Only after that geometric gate passes do we
    run the existing wave/retracement scan from the proposal.

    This prevents a proposal from redefining the reference line first and then
    judging itself against its own newly-created trend direction.
    """
    timeline = _timeline_points(result)
    roles = line_role_map(timeline)
    anchor = initial_entry
    wanted_entry_role = "low" if direction > 0 else "high"

    # The backward 10-degree test must use the first real Stage-3 extreme
    # reached from the provisional entry, not a later Stage-2 seed endpoint
    # that may skip one or more intermediate waves.
    reference_peak = next(
        (
            point
            for point in timeline
            if initial_entry.day < point.day <= seed_peak.day
            and (
                (direction > 0 and point.value > initial_entry.value)
                or (direction < 0 and point.value < initial_entry.value)
            )
        ),
        seed_peak,
    )

    while True:
        earlier = [
            point
            for point in timeline
            if point.day < anchor.day
            and roles.get(_key(point)) == wanted_entry_role
        ]
        if not earlier:
            return anchor

        proposal = max(earlier, key=lambda item: (item.day, item.value))

        current_angle = screen_segment_angle_degrees(
            anchor,
            reference_peak,
            geometry,
        )
        proposal_angle = screen_segment_angle_degrees(
            proposal,
            reference_peak,
            geometry,
        )
        if abs(proposal_angle - current_angle) > angle_threshold_deg:
            return anchor

        reached = _scan_from_entry(
            result,
            anchor=proposal,
            direction=direction,
            through=reference_peak.day,
            geometry=geometry,
            angle_threshold_deg=angle_threshold_deg,
        )
        if reached is None or _key(reached) != _key(reference_peak):
            return anchor

        anchor = proposal


def finalize_rapid_moves(
    result: Stage3LineResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SAME_TREND_ANGLE_THRESHOLD_DEG,
) -> SimplifiedLineResult:
    """Finalize rapid candidates using entry-anchored Stage-5 wave logic."""
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")

    candidates = sorted(
        result.rapid_move_candidates,
        key=lambda item: (item.start.day, item.end.day, item.direction),
    )
    if not candidates:
        return SimplifiedLineResult(
            markers=result.markers,
            segments=result.segments,
            marker_only_points=result.marker_only_points,
            protected_points=(),
        )

    y_span = float(geometry.y_max - geometry.y_min)
    if y_span <= 0:
        raise ValueError("geometry y-axis span must be positive")

    finalized: list[LineRapidMoveCandidate] = []
    consumed: set[int] = set()

    for index, candidate in enumerate(candidates):
        if index in consumed or candidate.start.day >= candidate.end.day:
            continue

        direction = candidate.direction

        # Backward extension MUST happen before any forward judgment. The
        # current entry->seed-peak direction remains the 10-degree reference;
        # only a backward proposal aligned with that existing direction may
        # replace the entry. Wave/retracement judgment runs only after that.
        anchor = _expand_entry_backward(
            result,
            initial_entry=candidate.start,
            seed_peak=candidate.end,
            direction=direction,
            geometry=geometry,
            angle_threshold_deg=angle_threshold_deg,
        )

        # Only candidates that can plausibly belong to this FINAL entry-anchored
        # run are offered to the forward scan.
        group_indices: list[int] = []
        group: list[LineRapidMoveCandidate] = []
        for next_index in range(index, len(candidates)):
            other = candidates[next_index]
            if next_index in consumed:
                continue
            if other.start.day < anchor.day:
                continue
            if other.direction != direction:
                if other.start.day > candidate.end.day:
                    break
                continue
            group_indices.append(next_index)
            group.append(other)

        if not group:
            continue

        # Stage-2 endpoints are provisional survival metadata only. They must
        # never limit Stage-4 judgment. Scan the complete Stage-3 chronology
        # from the finalized entry until the structure itself terminates.
        final_peak = _scan_from_entry(
            result,
            anchor=anchor,
            direction=direction,
            through=None,
            geometry=geometry,
            angle_threshold_deg=angle_threshold_deg,
        )

        # The provisional seed must at least be reached by the finalized
        # structure. If chronological judgment terminates before that seed,
        # the Stage-2 candidate is rejected.
        seed_reached = (
            final_peak is not None
            and final_peak.day >= candidate.end.day
            and (
                (direction > 0 and final_peak.value >= candidate.end.value)
                or (direction < 0 and final_peak.value <= candidate.end.value)
            )
        )

        if not seed_reached:
            # This entry did not survive the wave/retracement judgment. Release
            # only this candidate; later rapid entries remain eligible anchors.
            consumed.add(index)
            continue

        finalized.append(
            LineRapidMoveCandidate(
                start=anchor,
                end=final_peak,
                direction=direction,
                visual_y_share=abs(
                    float(final_peak.value) - float(anchor.value)
                ) / y_span,
            )
        )

        # Any rapid candidates fully contained inside the finalized run have
        # been consolidated into this entry->final_peak structure.
        for group_index in group_indices:
            other = candidates[group_index]
            if other.start.day >= anchor.day and other.end.day <= final_peak.day:
                consumed.add(group_index)

    final_protected_map: dict[tuple[date, float], LinePoint] = {}
    for candidate in finalized:
        final_protected_map[_key(candidate.start)] = candidate.start
        final_protected_map[_key(candidate.end)] = candidate.end

    protected = tuple(sorted(
        final_protected_map.values(),
        key=lambda item: item.day,
    ))

    return SimplifiedLineResult(
        markers=result.markers,
        segments=result.segments,
        marker_only_points=result.marker_only_points,
        protected_points=protected,
    )
