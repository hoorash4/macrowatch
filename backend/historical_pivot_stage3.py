"""Stage 3: merge Stage 2 upper/lower RDP lines into one wave line.

Stage 3 does only the merge. It does not perform the Stage-4/5 cleanup.

Merge rule:
- uptrend: low -> high, then compare HIGHs; a higher HIGH extends high -> high.
- if the next HIGH is lower, the previous HIGH owns the down reversal and
  connects to the first later LOW, unless the old uptrend immediately resumes
  with a new HIGH above the reversal owner.
- downtrend is the exact mirror: high -> low, then compare LOWs; a lower LOW
  extends low -> low.
- therefore, after high -> low, the LOW is the active reference. Intervening
  HIGHs do not become vertices merely because they occur earlier in time.
- Stage-2 spike/sideways structure is carried as segment kind metadata.
- Stage-2 rapid candidate endpoints must survive connected into Stage 4.
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
    """Merge the two RDP boundaries using the approved active-side rule."""
    marker_only_keys = {
        _key(spike.point)
        for spike in stage2.spike_peaks
        if spike.marker_only
    }
    highs = [
        point
        for point in sorted(stage2.high_pivots, key=lambda item: item.day)
        if _key(point) not in marker_only_keys
    ]
    lows = [
        point
        for point in sorted(stage2.low_pivots, key=lambda item: item.day)
        if _key(point) not in marker_only_keys
    ]
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

    def replace_last_endpoint(old_end: PivotPoint, new_end: PivotPoint) -> bool:
        for idx in range(len(segments) - 1, -1, -1):
            left, right = segments[idx]
            if _key(right) != _key(old_end):
                continue
            if left.day >= new_end.day:
                return False
            segments[idx] = (left, new_end)
            return True
        return False

    while True:
        if direction == "up":
            # After a cross-side restart, finish LOW -> HIGH once. From then on,
            # HIGH is the active side and only HIGHs decide continuation.
            if anchor.pivot_type == "low":
                next_high = _next_after(highs, anchor.day)
                if next_high is None:
                    break
                segments.append((anchor, next_high))
                anchor = next_high
                continue

            next_high = _next_after(highs, anchor.day)
            if next_high is None:
                break

            if next_high.value > anchor.value:
                # Same uptrend: HIGH -> HIGH.
                segments.append((anchor, next_high))
                anchor = next_high
                continue

            # Lower HIGH: previous HIGH owns a possible down reversal.
            reversal_owner = anchor
            low_candidate = _next_after(lows, reversal_owner.day)
            if low_candidate is None:
                break

            # If the old uptrend immediately makes a new HIGH above the owner,
            # the low was only a pullback; extend the old trend instead.
            following_high = _next_after(highs, low_candidate.day)
            if (
                following_high is not None
                and following_high.value > reversal_owner.value
            ):
                if not replace_last_endpoint(reversal_owner, following_high):
                    segments.append((reversal_owner, following_high))
                anchor = following_high
                continue

            segments.append((reversal_owner, low_candidate))
            anchor = low_candidate
            direction = "down"
            continue

        # direction == "down"
        # After HIGH -> LOW, LOW is the active side. Compare LOWs, not the
        # intervening HIGHs.
        if anchor.pivot_type == "high":
            next_low = _next_after(lows, anchor.day)
            if next_low is None:
                break
            segments.append((anchor, next_low))
            anchor = next_low
            continue

        next_low = _next_after(lows, anchor.day)
        if next_low is None:
            break

        if next_low.value < anchor.value:
            # Same downtrend: LOW -> LOW.
            segments.append((anchor, next_low))
            anchor = next_low
            continue

        # Higher LOW: previous LOW owns a possible up reversal.
        reversal_owner = anchor
        high_candidate = _next_after(highs, reversal_owner.day)
        if high_candidate is None:
            break

        following_low = _next_after(lows, high_candidate.day)
        if (
            following_low is not None
            and following_low.value < reversal_owner.value
        ):
            # Old downtrend resumed with a new LOW; the rebound HIGH was not a
            # turn. Extend LOW -> LOW.
            if not replace_last_endpoint(reversal_owner, following_low):
                segments.append((reversal_owner, following_low))
            anchor = following_low
            continue

        segments.append((reversal_owner, high_candidate))
        anchor = high_candidate
        direction = "up"

    return segments


def simplify_pivot_lines(
    augmented: Stage2Result,
    geometry: ChartGeometry,
) -> Stage3LineResult:
    """Merge Stage-2 RDP boundaries and hand the merged line to Stage 4."""
    del geometry  # Stage 3 merge itself has no angle/cleanup judgment.

    raw_segments = _merge_only(augmented)

    marker_only_spikes = tuple(
        spike for spike in augmented.spike_peaks if spike.marker_only
    )

    hard_kind: dict[tuple[tuple[date, float], tuple[date, float]], str] = {}
    for spike in augmented.spike_peaks:
        if spike.marker_only or spike.entry is None:
            continue
        hard_kind[(_line_key(spike.entry), _line_key(spike.point))] = "spike"
    for sideways in (
        *augmented.high_sideways_segments,
        *augmented.low_sideways_segments,
    ):
        if not getattr(sideways, "protected", True):
            continue
        hard_kind[(_line_key(sideways.start), _line_key(sideways.end))] = "sideways"

    # Stage-2 rapid candidates are provisional but must survive Stage 3 so that
    # Stage 4, not Stage 3, decides whether they remain. If a candidate endpoint
    # lies inside a merged segment, split that segment at the endpoint.
    mandatory: dict[tuple[date, float], PivotPoint] = {}
    for candidate in augmented.rapid_move_candidates:
        mandatory[_line_key(candidate.start)] = candidate.start
        mandatory[_line_key(candidate.end)] = candidate.end

    split_segments: list[tuple[PivotPoint, PivotPoint]] = []
    for left, right in raw_segments:
        interior = sorted(
            (
                point for point in mandatory.values()
                if left.day < point.day < right.day
            ),
            key=lambda item: item.day,
        )
        chain = [left, *interior, right]
        for a, b in zip(chain, chain[1:]):
            if a.day < b.day:
                split_segments.append((a, b))

    point_map: dict[tuple[date, float], LinePoint] = {}
    for left, right in split_segments:
        point_map[_line_key(left)] = LinePoint(left.day, left.value)
        point_map[_line_key(right)] = LinePoint(right.day, right.value)

    line_segments: list[SimplifiedLineSegment] = []
    for left, right in split_segments:
        kind = hard_kind.get((_line_key(left), _line_key(right)), "trend")
        line_segments.append(
            SimplifiedLineSegment(
                start=point_map[_line_key(left)],
                end=point_map[_line_key(right)],
                kind=kind,
            )
        )

    marker_only_points = tuple(sorted(
        (
            LinePoint(spike.point.day, spike.point.value)
            for spike in marker_only_spikes
        ),
        key=lambda item: item.day,
    ))
    for point in marker_only_points:
        point_map[(point.day, point.value)] = point

    markers = tuple(sorted(point_map.values(), key=lambda item: item.day))

    line_rapid_candidates = tuple(
        LineRapidMoveCandidate(
            start=LinePoint(candidate.start.day, candidate.start.value),
            end=LinePoint(candidate.end.day, candidate.end.value),
            direction=candidate.direction,
            visual_y_share=candidate.visual_y_share,
        )
        for candidate in augmented.rapid_move_candidates
    )

    return Stage3LineResult(
        markers=markers,
        segments=tuple(line_segments),
        marker_only_points=marker_only_points,
        rapid_move_candidates=line_rapid_candidates,
    )
