"""Stage 4: finalize rapid-move candidates from Stage 2/3.

Stage 4 applies the SAME chronological wave/retracement judgment used by the
general Stage-5 cleanup, but only rapid-move ENTRY points may start an anchor.

For each provisional rapid entry:
- FIRST walk backward through the completed Stage-3 line and test whether an
  earlier opposite-side point can be the same rapid-wave entry;
- whenever backward integration succeeds, replace the entry anchor immediately
  and test again from that NEW anchor;
- only after the earliest valid entry is fixed, walk forward from that new
  anchor through the completed Stage-3 line;
- an opposite wave is provisional, not an automatic rapid-move terminator;
- the very next same-side extreme decides it:
  * if it exceeds the previous extreme, the opposite wave was a retracement;
    keep the rapid trend alive and apply the anchor-based 10-degree test;
  * if it does not exceed the previous extreme, the prior extreme is the end
    of this rapid move; later points may not retroactively re-join it;
- hard spike/sideways structure ends the active rapid scan;
- only a Stage-2 rapid entry can become the next rapid anchor;
- after consolidation, the surviving rapid entry and final rapid peak become
  final protected points and all other rapid protection is released.

Stage 4 never performs the general Stage-5 cleanup.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

from historical_pivot_shared import (
    SAME_TREND_ANGLE_THRESHOLD_DEG,
    ChartGeometry,
    PivotPoint,
    RapidMoveCandidate,
    SimplifiedLineResult,
    screen_origin_angle_degrees,
)


def _key(point: PivotPoint) -> tuple[date, float, str]:
    return point.day, point.value, point.pivot_type


def _segment_direction(start: PivotPoint, end: PivotPoint) -> int:
    if end.value > start.value:
        return 1
    if end.value < start.value:
        return -1
    return 0


def _is_same_side(point: PivotPoint, direction: int) -> bool:
    return point.pivot_type == ("high" if direction > 0 else "low")


def _improves(point: PivotPoint, extreme: PivotPoint, direction: int) -> bool:
    return (
        point.value > extreme.value
        if direction > 0
        else point.value < extreme.value
    )


def _farther_opposite(
    point: PivotPoint,
    current: PivotPoint,
    direction: int,
) -> bool:
    return (
        point.value < current.value
        if direction > 0
        else point.value > current.value
    )


def _crosses_hard_structure(
    result: SimplifiedLineResult,
    *,
    start: date,
    end: date,
) -> bool:
    for segment in result.segments:
        if segment.kind not in {"spike", "sideways"}:
            continue
        if start < segment.start.day < end or start < segment.end.day < end:
            return True
    return False


def _timeline_points(result: SimplifiedLineResult) -> tuple[PivotPoint, ...]:
    """Return Stage-3 chronological points, including provisional rapid markers."""
    by_key = {_key(point): point for point in result.markers}
    for segment in result.segments:
        by_key[_key(segment.start)] = segment.start
        by_key[_key(segment.end)] = segment.end
    return tuple(sorted(
        by_key.values(),
        key=lambda item: (item.day, item.pivot_type),
    ))


def _scan_from_entry(
    result: SimplifiedLineResult,
    *,
    anchor: PivotPoint,
    direction: int,
    through: date,
    geometry: ChartGeometry,
    angle_threshold_deg: float,
) -> PivotPoint | None:
    """Run Stage-5-style wave judgment from one rapid ENTRY anchor only."""
    points = [
        point
        for point in _timeline_points(result)
        if anchor.day < point.day <= through
    ]
    if not points:
        return None

    extreme: PivotPoint | None = None
    opposite: PivotPoint | None = None

    for point in points:
        if _crosses_hard_structure(
            result,
            start=anchor.day,
            end=point.day,
        ):
            break

        if extreme is None:
            if _is_same_side(point, direction):
                extreme = point
            continue

        if not _is_same_side(point, direction):
            if opposite is None or _farther_opposite(point, opposite, direction):
                opposite = point
            continue

        # Same-side point after either no pullback or a provisional pullback.
        # This is the Stage-5 rule: it decides the prior extreme immediately.
        if not _improves(point, extreme, direction):
            # Failed to recover/extend the prior extreme. The prior extreme is
            # final for this rapid run; later points cannot re-join it.
            break

        angle = screen_origin_angle_degrees(
            anchor,
            extreme,
            point,
            geometry,
        )
        if angle > angle_threshold_deg:
            break

        # The old extreme was exceeded. Any opposite move since then was only
        # a retracement, so the rapid trend continues.
        extreme = point
        opposite = None

    return extreme


def _expand_entry_backward(
    result: SimplifiedLineResult,
    *,
    initial_entry: PivotPoint,
    seed_peak: PivotPoint,
    direction: int,
    geometry: ChartGeometry,
    angle_threshold_deg: float,
) -> PivotPoint:
    """Move a provisional rapid entry backward before any forward extension.

    Candidate anchors are earlier opposite-side Stage-3 points, tested nearest
    first.  A candidate is accepted only when the SAME Stage-5-style
    wave/retracement + 10-degree logic, re-run from that candidate anchor,
    still reaches the seed rapid peak.  After acceptance the anchor is replaced
    immediately and the search continues farther backward from the new anchor.
    """
    timeline = _timeline_points(result)
    anchor = initial_entry
    wanted_entry_type = "low" if direction > 0 else "high"

    while True:
        earlier = [
            point
            for point in timeline
            if point.day < anchor.day
            and point.pivot_type == wanted_entry_type
        ]
        if not earlier:
            return anchor

        proposal = max(earlier, key=lambda item: (item.day, item.value))
        if _crosses_hard_structure(
            result,
            start=proposal.day,
            end=seed_peak.day,
        ):
            return anchor

        reached = _scan_from_entry(
            result,
            anchor=proposal,
            direction=direction,
            through=seed_peak.day,
            geometry=geometry,
            angle_threshold_deg=angle_threshold_deg,
        )
        if reached is None or _key(reached) != _key(seed_peak):
            return anchor

        anchor = proposal


def finalize_rapid_moves(
    result: SimplifiedLineResult,
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
        if not result.provisional_protected_points:
            return result
        return SimplifiedLineResult(
            markers=result.markers,
            segments=result.segments,
            sideways_segments=result.sideways_segments,
            protected_points=result.protected_points,
            provisional_protected_points=(),
            rapid_move_candidates=(),
        )

    y_span = float(geometry.y_max - geometry.y_min)
    if y_span <= 0:
        raise ValueError("geometry y-axis span must be positive")

    finalized: list[RapidMoveCandidate] = []
    consumed: set[int] = set()

    for index, candidate in enumerate(candidates):
        if index in consumed or candidate.start.day >= candidate.end.day:
            continue

        direction = candidate.direction

        # Backward extension MUST happen before any forward judgment.  The
        # Stage-2 entry is provisional; if an earlier Stage-3 point belongs to
        # the same rapid wave, make it the new entry anchor first.  All forward
        # 10-degree judgments are then re-run from that new anchor.
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
        group: list[RapidMoveCandidate] = []
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
            if _crosses_hard_structure(
                result,
                start=anchor.day,
                end=other.end.day,
            ):
                break
            group_indices.append(next_index)
            group.append(other)

        if not group:
            continue

        candidate_end_keys = {_key(item.end) for item in group}
        latest_end = max(item.end.day for item in group)
        final_peak = _scan_from_entry(
            result,
            anchor=anchor,
            direction=direction,
            through=latest_end,
            geometry=geometry,
            angle_threshold_deg=angle_threshold_deg,
        )

        # Ordinary Stage-3 points may judge the wave, but Stage 4 may finalize
        # only a peak that Stage 2 actually identified as a rapid endpoint.
        if final_peak is not None and _key(final_peak) not in candidate_end_keys:
            final_peak = None

        if final_peak is None:
            # This entry did not survive the wave/retracement judgment. Release
            # only this candidate; later rapid entries remain eligible anchors.
            consumed.add(index)
            continue

        finalized.append(
            RapidMoveCandidate(
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

    final_protected_map = {
        _key(point): point
        for point in result.protected_points
    }
    for candidate in finalized:
        final_protected_map[_key(candidate.start)] = candidate.start
        final_protected_map[_key(candidate.end)] = candidate.end

    connected_keys = {
        _key(point)
        for segment in result.segments
        for point in (segment.start, segment.end)
    }
    hard_keys = {
        _key(point)
        for segment in result.segments
        if segment.kind in {"spike", "sideways"}
        for point in (segment.start, segment.end)
    }
    finalized_keys = set(final_protected_map)
    provisional_keys = {
        _key(point) for point in result.provisional_protected_points
    }

    # Stage 4 alone releases rapid provisional protection. Standalone
    # provisional markers that did not survive are removed; real Stage-3 line
    # vertices remain as ordinary unprotected points for Stage 5 to judge.
    markers = tuple(
        point
        for point in result.markers
        if (
            _key(point) not in provisional_keys
            or _key(point) in finalized_keys
            or _key(point) in connected_keys
            or _key(point) in hard_keys
        )
    )

    marker_keys = {_key(point) for point in markers}
    protected = tuple(sorted(
        (
            point
            for key, point in final_protected_map.items()
            if key in marker_keys
        ),
        key=lambda item: (item.day, item.pivot_type),
    ))

    return SimplifiedLineResult(
        markers=tuple(sorted(
            markers,
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=result.segments,
        sideways_segments=result.sideways_segments,
        protected_points=protected,
        provisional_protected_points=(),
        rapid_move_candidates=tuple(finalized),
    )
