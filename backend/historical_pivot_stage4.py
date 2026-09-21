"""Stage 4: finalize and consolidate Stage-2 rapid-move candidates.

Stage 3 has already built the single wave line and carried every provisional
rapid-move endpoint through without deleting it.  Stage 4 is the ONLY stage
allowed to release or consolidate those provisional points.

Rules:
- rapid candidates of the same direction may extend one another only while
  there is no opposite Stage-3 wave between them;
- extension is measured from the original rapid-move entry anchor;
- the angle between anchor->previous peak and anchor->new peak must be <= the
  approved 10-degree same-trend threshold;
- a hard spike/sideways boundary ends the extension;
- after this stage, surviving rapid-move endpoints become final protected
  points and all provisional protection is cleared.

Stage 4 does not perform the general trend/reversal cleanup.  That is Stage 5.
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


def _has_opposite_wave(
    result: SimplifiedLineResult,
    *,
    after: date,
    through: date,
    direction: int,
) -> bool:
    """Return True only for an actual connected Stage-3 opposite trend wave."""
    for segment in result.segments:
        if segment.kind != "trend":
            continue
        if segment.end.day <= after or segment.start.day >= through:
            continue
        seg_direction = _segment_direction(segment.start, segment.end)
        if seg_direction != 0 and seg_direction != direction:
            return True
    return False


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


def finalize_rapid_moves(
    result: SimplifiedLineResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SAME_TREND_ANGLE_THRESHOLD_DEG,
) -> SimplifiedLineResult:
    """Finalize Stage-2 rapid candidates against the completed Stage-3 line."""
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
    active: RapidMoveCandidate | None = None

    def flush() -> None:
        nonlocal active
        if active is not None:
            finalized.append(active)
            active = None

    for candidate in candidates:
        if candidate.start.day >= candidate.end.day:
            continue

        if active is None:
            active = candidate
            continue

        # Nested/duplicate candidate with no later extreme adds nothing.
        if (
            candidate.direction == active.direction
            and candidate.end.day <= active.end.day
        ):
            continue

        if candidate.direction != active.direction:
            flush()
            active = candidate
            continue

        if _crosses_hard_structure(
            result,
            start=active.start.day,
            end=candidate.end.day,
        ):
            flush()
            active = candidate
            continue

        # Any real opposite Stage-3 wave ends the rapid move at the prior peak.
        if _has_opposite_wave(
            result,
            after=active.end.day,
            through=candidate.end.day,
            direction=active.direction,
        ):
            flush()
            active = candidate
            continue

        angle = screen_origin_angle_degrees(
            active.start,
            active.end,
            candidate.end,
            geometry,
        )
        if angle > angle_threshold_deg:
            flush()
            active = candidate
            continue

        active = RapidMoveCandidate(
            start=active.start,
            end=candidate.end,
            direction=active.direction,
            visual_y_share=abs(
                float(candidate.end.value) - float(active.start.value)
            ) / y_span,
        )

    flush()

    final_protected_map = {
        _key(point): point
        for point in result.protected_points
    }
    for candidate in finalized:
        for point in candidate.protected_points:
            final_protected_map[_key(point)] = point

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

    # Only Stage 4 may release provisional rapid points.  If a released point
    # is merely a standalone provisional marker, remove it now.  If it is also
    # a real Stage-3 line vertex or hard-protected point, keep the marker but
    # remove its rapid protection so Stage 5 can judge it normally.
    markers = tuple(
        point
        for point in result.markers
        if (
            _key(point) not in {
                _key(p) for p in result.provisional_protected_points
            }
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
