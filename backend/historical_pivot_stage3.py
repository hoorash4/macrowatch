"""Stage 3: merge Stage 2 upper/lower RDP lines into one wave line.

Stage 3 does only the merge. It does not perform the Stage-4/5 cleanup.

Merge rule:
- uptrend: low -> high, then compare HIGHs; a higher HIGH extends high -> high.
- if the next HIGH is lower, connect the current HIGH to the next LOW and
  switch the active reference to LOW.
- downtrend is the exact mirror: high -> low, then compare LOWs; a lower LOW
  extends low -> low.
- if the next LOW is higher, connect the current LOW to the next HIGH and
  switch the active reference to HIGH.
- after a cross-side connection, the new endpoint becomes the only active
  reference. Stage 3 does not look ahead to confirm or cancel that turn.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

from historical_pivot_shared import (
    ChartGeometry,
    LinePoint,
    LineRapidMoveCandidate,
    PivotPoint,
    SimplifiedLineSegment,
    Stage3LineResult,
)
from historical_pivot_stage2 import Stage2Result


def _key(point: PivotPoint) -> tuple[date, float, str]:
    return point.day, point.value, point.pivot_type


def _line_key(point: PivotPoint | LinePoint) -> tuple[date, float]:
    return point.day, point.value


def _next_after(points: Sequence[PivotPoint], after: date) -> PivotPoint | None:
    return next((item for item in points if item.day > after), None)


def _merge_only(stage2: Stage2Result) -> list[tuple[PivotPoint, PivotPoint]]:
    """Merge upper/lower RDP boundaries using only the active-side rule.

    Stage-2 rapid entry/end points are protected merge vertices. They participate
    in the merge from the start and are never removed and reinserted later.
    """
    highs = list(sorted(stage2.high_pivots, key=lambda item: item.day))
    lows = list(sorted(stage2.low_pivots, key=lambda item: item.day))
    protected = {
        _key(point): point
        for candidate in stage2.rapid_move_candidates
        for point in (candidate.start, candidate.end)
    }
    protected_ordered = sorted(
        protected.values(),
        key=lambda item: (item.day, item.pivot_type),
    )
    if not highs or not lows:
        return []

    first_high = highs[0]
    first_low = lows[0]
    if first_low.day < first_high.day:
        direction = "up"
        anchor = first_low
        current = _next_after(highs, anchor.day)
    else:
        direction = "down"
        anchor = first_high
        current = _next_after(lows, anchor.day)

    if current is None:
        return []

    segments: list[tuple[PivotPoint, PivotPoint]] = [(anchor, current)]
    anchor = current

    def protected_before(
        proposed: PivotPoint,
    ) -> PivotPoint | None:
        return next(
            (
                point
                for point in protected_ordered
                if anchor.day < point.day < proposed.day
            ),
            None,
        )

    def connect(proposed: PivotPoint) -> bool:
        nonlocal anchor, direction
        protected_point = protected_before(proposed)
        if protected_point is not None:
            segments.append((anchor, protected_point))
            anchor = protected_point
            direction = "up" if anchor.pivot_type == "high" else "down"
            return False

        segments.append((anchor, proposed))
        anchor = proposed
        return True

    while True:
        if direction == "up":
            if anchor.pivot_type == "low":
                next_high = _next_after(highs, anchor.day)
                if next_high is None:
                    break
                if not connect(next_high):
                    continue
                continue

            next_high = _next_after(highs, anchor.day)
            if next_high is None:
                break

            if next_high.value > anchor.value:
                if not connect(next_high):
                    continue
                continue

            next_low = _next_after(lows, anchor.day)
            if next_low is None:
                break
            if not connect(next_low):
                continue
            direction = "down"
            continue

        if anchor.pivot_type == "high":
            next_low = _next_after(lows, anchor.day)
            if next_low is None:
                break
            if not connect(next_low):
                continue
            continue

        next_low = _next_after(lows, anchor.day)
        if next_low is None:
            break

        if next_low.value < anchor.value:
            if not connect(next_low):
                continue
            continue

        next_high = _next_after(highs, anchor.day)
        if next_high is None:
            break
        if not connect(next_high):
            continue
        direction = "up"

    return segments

def simplify_pivot_lines(
    augmented: Stage2Result,
    geometry: ChartGeometry,
) -> Stage3LineResult:
    """Merge Stage-2 upper/lower RDP lines and do nothing else."""
    del geometry

    raw_segments = _merge_only(augmented)

    point_map: dict[tuple[date, float], LinePoint] = {}
    line_segments: list[SimplifiedLineSegment] = []
    for left, right in raw_segments:
        left_line = point_map.setdefault(
            _line_key(left),
            LinePoint(left.day, left.value),
        )
        right_line = point_map.setdefault(
            _line_key(right),
            LinePoint(right.day, right.value),
        )
        line_segments.append(
            SimplifiedLineSegment(
                start=left_line,
                end=right_line,
                kind="trend",
            )
        )

    markers = tuple(sorted(point_map.values(), key=lambda item: item.day))
    marker_keys = {(point.day, point.value) for point in markers}

    rapid = tuple(
        LineRapidMoveCandidate(
            start=LinePoint(candidate.start.day, candidate.start.value),
            end=LinePoint(candidate.end.day, candidate.end.value),
            direction=candidate.direction,
            visual_y_share=candidate.visual_y_share,
        )
        for candidate in augmented.rapid_move_candidates
        if _line_key(candidate.start) in marker_keys
        and _line_key(candidate.end) in marker_keys
    )

    return Stage3LineResult(
        markers=markers,
        segments=tuple(line_segments),
        marker_only_points=(),
        rapid_move_candidates=rapid,
    )
