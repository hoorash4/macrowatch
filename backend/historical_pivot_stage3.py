"""Stage 3: merge Stage 2's upper/lower RDP points into one wave line.

This file intentionally contains one Stage-3 algorithm only.

Inputs:
- Stage 2 final high/low points
- Stage 2 protection metadata (sideways/spike)

Output:
- one chronological wave line
- protected standalone marker-only spikes

Stage 3 never reads Stage 1 candidates, envelope data, deleted points, or any
earlier-stage result.

Wave rule:
- upper and lower RDP boundaries moving together upward = one rising wave
- upper and lower RDP boundaries moving together downward = one falling wave
- rising wave keeps only start LOW -> final HIGH
- falling wave keeps only start HIGH -> final LOW
- a failed provisional reversal removes only its internal opposite-side point
- the prior same-direction extreme remains for Stage 4's 10-degree comparison
- 10-degree cleanup itself belongs only to Stage 4
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from historical_pivot_shared import (
    ChartGeometry,
    PivotPoint,
    SidewaysSegment,
    SimplifiedLineResult,
    SimplifiedLineSegment,
)
from historical_pivot_stage2 import Stage2Result


def _key(point: PivotPoint) -> tuple[date, float, str]:
    return point.day, point.value, point.pivot_type


def _unique(points: Sequence[PivotPoint]) -> list[PivotPoint]:
    by_key = {_key(point): point for point in points}
    return sorted(by_key.values(), key=lambda item: (item.day, item.pivot_type))


@dataclass(frozen=True)
class _Leg:
    start: PivotPoint
    end: PivotPoint
    direction: int  # +1 up, -1 down


@dataclass
class _CandidateRun:
    direction: int
    intervals: list[tuple[date, date, _Leg, _Leg]]

    @property
    def natural_start(self) -> PivotPoint:
        if self.direction > 0:
            return min(
                (low_leg.start for _, _, _, low_leg in self.intervals),
                key=lambda item: item.day,
            )
        return min(
            (high_leg.start for _, _, high_leg, _ in self.intervals),
            key=lambda item: item.day,
        )


@dataclass(frozen=True)
class _Wave:
    direction: int
    start: PivotPoint
    end: PivotPoint


@dataclass(frozen=True)
class _HardSegment:
    start: PivotPoint
    end: PivotPoint
    kind: str
    sideways: SidewaysSegment | None = None

    @property
    def restart_direction(self) -> int:
        return -1 if self.end.pivot_type == "high" else 1


def _legs(points: Sequence[PivotPoint], pivot_type: str) -> list[_Leg]:
    same_side = sorted(
        (point for point in points if point.pivot_type == pivot_type),
        key=lambda item: item.day,
    )
    result: list[_Leg] = []
    for left, right in zip(same_side, same_side[1:]):
        if right.value == left.value:
            continue
        result.append(
            _Leg(
                start=left,
                end=right,
                direction=1 if right.value > left.value else -1,
            )
        )
    return result


def _consensus_runs(points: Sequence[PivotPoint]) -> list[_CandidateRun]:
    """Find periods where upper and lower boundaries agree on direction."""
    highs = _legs(points, "high")
    lows = _legs(points, "low")
    intervals: list[tuple[date, date, int, _Leg, _Leg]] = []

    for high_leg in highs:
        for low_leg in lows:
            if high_leg.direction != low_leg.direction:
                continue
            start = max(high_leg.start.day, low_leg.start.day)
            end = min(high_leg.end.day, low_leg.end.day)
            if start > end:
                continue
            intervals.append(
                (start, end, high_leg.direction, high_leg, low_leg)
            )

    intervals.sort(key=lambda item: (item[0], item[1]))

    # Only a consensus change can start a new wave.  Gaps/disagreement between
    # the two boundaries do not themselves create a turn.
    runs: list[_CandidateRun] = []
    for start, end, direction, high_leg, low_leg in intervals:
        if runs and runs[-1].direction == direction:
            runs[-1].intervals.append((start, end, high_leg, low_leg))
            continue
        runs.append(
            _CandidateRun(
                direction=direction,
                intervals=[(start, end, high_leg, low_leg)],
            )
        )
    return runs


def _extreme_after(
    points: Sequence[PivotPoint],
    start: PivotPoint,
    direction: int,
) -> PivotPoint | None:
    wanted = "high" if direction > 0 else "low"
    candidates = [
        point
        for point in points
        if point.day > start.day and point.pivot_type == wanted
    ]
    if not candidates:
        return None
    if direction > 0:
        return max(candidates, key=lambda item: (item.value, item.day))
    return min(candidates, key=lambda item: (item.value, item.day))


def _build_waves(
    points: Sequence[PivotPoint],
    runs: Sequence[_CandidateRun],
    *,
    forced_anchor: PivotPoint | None,
    forced_direction: int | None,
) -> list[_Wave]:
    """Build continuous candidate waves from consensus direction runs."""
    if not points:
        return []

    active_runs = list(runs)

    # A protected prior endpoint fixes the first wave direction.  Ignore earlier
    # consensus noise until that direction appears.
    if forced_direction is not None:
        first_index = next(
            (
                index
                for index, run in enumerate(active_runs)
                if run.direction == forced_direction
            ),
            None,
        )
        if first_index is not None:
            active_runs = active_runs[first_index:]

    if not active_runs:
        if forced_anchor is None or forced_direction is None:
            return []
        end = _extreme_after(points, forced_anchor, forced_direction)
        if end is None:
            return []
        return [_Wave(forced_direction, forced_anchor, end)]

    waves: list[_Wave] = []
    for index, run in enumerate(active_runs):
        if index == 0:
            start = forced_anchor or run.natural_start
        else:
            start = waves[-1].end

        if index + 1 < len(active_runs):
            # The next opposite consensus run starts at the current wave's turn.
            end = active_runs[index + 1].natural_start
        else:
            end = _extreme_after(points, start, run.direction)

        if end is None or start.day >= end.day:
            continue
        wanted_end = "high" if run.direction > 0 else "low"
        if end.pivot_type != wanted_end:
            end = _extreme_after(points, start, run.direction)
            if end is None or start.day >= end.day:
                continue

        waves.append(_Wave(run.direction, start, end))

    return waves


def _later_extreme(
    points: Sequence[PivotPoint],
    *,
    after: date,
    pivot_type: str,
) -> PivotPoint | None:
    candidates = [
        point
        for point in points
        if point.day > after and point.pivot_type == pivot_type
    ]
    if not candidates:
        return None
    return (
        min(candidates, key=lambda item: (item.value, item.day))
        if pivot_type == "low"
        else max(candidates, key=lambda item: (item.value, item.day))
    )


def _cancel_provisional_waves(
    points: Sequence[PivotPoint],
    waves: Sequence[_Wave],
) -> list[_Wave]:
    """Cancel only the INTERNAL point of a failed reversal.

    Uptrend:
        ... -> H1 -> L1 -> H2, with H2 > H1
        the down reversal failed. Keep H1 and H2; delete L1.

    Downtrend:
        ... -> L1 -> H1 -> L2, with L2 < L1
        the up reversal failed. Keep L1 and L2; delete H1.

    The preserved H1/L1 is a same-direction extreme needed by Stage 4's
    10-degree comparison. This function never merges it away.
    """
    current = list(waves)

    changed = True
    while changed:
        changed = False
        rebuilt: list[_Wave] = []
        index = 0

        while index < len(current):
            if index + 2 < len(current):
                first = current[index]
                middle = current[index + 1]
                last = current[index + 2]

                if (
                    first.direction == last.direction
                    and middle.direction == -first.direction
                    and first.end == middle.start
                    and middle.end == last.start
                ):
                    turn = middle.start

                    if (
                        first.direction > 0
                        and last.end.pivot_type == "high"
                        and last.end.value > turn.value
                    ):
                        # Failed down reversal: keep prior high turn, remove low.
                        rebuilt.append(first)
                        rebuilt.append(
                            _Wave(
                                direction=1,
                                start=turn,
                                end=last.end,
                            )
                        )
                        index += 3
                        changed = True
                        continue

                    if (
                        first.direction < 0
                        and last.end.pivot_type == "low"
                        and last.end.value < turn.value
                    ):
                        # Failed up reversal: keep prior low turn, remove high.
                        rebuilt.append(first)
                        rebuilt.append(
                            _Wave(
                                direction=-1,
                                start=turn,
                                end=last.end,
                            )
                        )
                        index += 3
                        changed = True
                        continue

            rebuilt.append(current[index])
            index += 1

        current = rebuilt

    # End-of-series mirror. A final provisional reversal may have no next
    # consensus run because one RDP boundary has no later point. Use the actual
    # surviving Stage-2 same-side extreme to decide whether the old trend resumed.
    if len(current) >= 2:
        previous = current[-2]
        final = current[-1]
        if previous.direction == -final.direction and previous.end == final.start:
            turn = final.start

            if previous.direction < 0 and final.direction > 0:
                later_low = _later_extreme(
                    points,
                    after=final.end.day,
                    pivot_type="low",
                )
                if later_low is not None and later_low.value < turn.value:
                    current[-1] = _Wave(
                        direction=-1,
                        start=turn,
                        end=later_low,
                    )

            elif previous.direction > 0 and final.direction < 0:
                later_high = _later_extreme(
                    points,
                    after=final.end.day,
                    pivot_type="high",
                )
                if later_high is not None and later_high.value > turn.value:
                    current[-1] = _Wave(
                        direction=1,
                        start=turn,
                        end=later_high,
                    )

    return current


def _merge_window(
    points: Sequence[PivotPoint],
    *,
    forced_anchor: PivotPoint | None = None,
    forced_direction: int | None = None,
) -> list[PivotPoint]:
    ordered = _unique(points)
    if forced_anchor is not None and _key(forced_anchor) not in {_key(p) for p in ordered}:
        ordered.append(forced_anchor)
        ordered.sort(key=lambda item: (item.day, item.pivot_type))
    if not ordered:
        return []

    runs = _consensus_runs(ordered)

    # With no consensus yet, a forced protected endpoint can still own the
    # unfinished final wave.
    if not runs:
        if forced_anchor is not None and forced_direction is not None:
            end = _extreme_after(ordered, forced_anchor, forced_direction)
            return [forced_anchor, end] if end is not None else [forced_anchor]

        # Initial fallback only when one boundary is too short to form a leg.
        first = min(ordered, key=lambda item: (item.day, item.pivot_type))
        direction = -1 if first.pivot_type == "high" else 1
        end = _extreme_after(ordered, first, direction)
        return [first, end] if end is not None else [first]

    waves = _build_waves(
        ordered,
        runs,
        forced_anchor=forced_anchor,
        forced_direction=forced_direction,
    )
    waves = _cancel_provisional_waves(
        ordered,
        waves,
    )

    if not waves:
        return [forced_anchor] if forced_anchor is not None else []

    vertices = [waves[0].start]
    for wave in waves:
        if vertices[-1] != wave.start:
            vertices.append(wave.start)
        if vertices[-1] != wave.end:
            vertices.append(wave.end)
    return vertices


def _hard_segments(stage2: Stage2Result) -> list[_HardSegment]:
    segments: list[_HardSegment] = []

    for spike in stage2.spike_peaks:
        if spike.marker_only or spike.entry is None:
            continue
        if spike.entry.day < spike.point.day:
            segments.append(
                _HardSegment(spike.entry, spike.point, "spike")
            )

    for sideways in (
        *stage2.high_sideways_segments,
        *stage2.low_sideways_segments,
    ):
        if sideways.start.day < sideways.end.day:
            segments.append(
                _HardSegment(
                    sideways.start,
                    sideways.end,
                    "sideways",
                    sideways,
                )
            )

    segments.sort(
        key=lambda item: (
            item.start.day,
            0 if item.kind == "spike" else 1,
            item.end.day,
        )
    )

    # Protection boundaries may share an endpoint, but an overlapping interior
    # span cannot independently own the same part of the line.
    accepted: list[_HardSegment] = []
    occupied_until: date | None = None
    for segment in segments:
        if occupied_until is not None and segment.start.day < occupied_until:
            continue
        accepted.append(segment)
        occupied_until = segment.end.day
    return accepted


def simplify_pivot_lines(
    augmented: Stage2Result,
    geometry: ChartGeometry,
) -> SimplifiedLineResult:
    """Return one Stage-3 wave line from Stage-2 points only."""
    del geometry

    marker_only_spikes = tuple(
        spike for spike in augmented.spike_peaks if spike.marker_only
    )
    marker_only_keys = {_key(spike.point) for spike in marker_only_spikes}

    points = _unique(
        tuple(
            point
            for point in (*augmented.high_pivots, *augmented.low_pivots)
            if _key(point) not in marker_only_keys
        )
    )

    hard = _hard_segments(augmented)
    segments: list[SimplifiedLineSegment] = []
    sideways_out: list[SidewaysSegment] = []
    markers: dict[tuple[date, float, str], PivotPoint] = {}

    def remember(point: PivotPoint) -> None:
        markers[_key(point)] = point

    def add_segment(start: PivotPoint, end: PivotPoint, kind: str) -> None:
        if start.day >= end.day:
            return
        remember(start)
        remember(end)
        segment = SimplifiedLineSegment(start, end, kind)
        if segment not in segments:
            segments.append(segment)

    def add_vertices(vertices: Sequence[PivotPoint]) -> None:
        if len(vertices) == 1:
            remember(vertices[0])
        for left, right in zip(vertices, vertices[1:]):
            add_segment(left, right, "trend")

    cursor_day: date | None = None
    forced_anchor: PivotPoint | None = None
    forced_direction: int | None = None

    for protected in hard:
        window = [
            point
            for point in points
            if (cursor_day is None or point.day >= cursor_day)
            and point.day <= protected.start.day
        ]
        if forced_anchor is not None:
            window.append(forced_anchor)

        vertices = _merge_window(
            window,
            forced_anchor=forced_anchor,
            forced_direction=forced_direction,
        )
        add_vertices(vertices)

        if vertices and vertices[-1] != protected.start:
            add_segment(vertices[-1], protected.start, "trend")
        elif not vertices:
            remember(protected.start)

        add_segment(protected.start, protected.end, protected.kind)
        if protected.sideways is not None:
            sideways_out.append(protected.sideways)

        forced_anchor = protected.end
        forced_direction = protected.restart_direction
        cursor_day = protected.end.day

    tail = [
        point
        for point in points
        if cursor_day is None or point.day >= cursor_day
    ]
    if forced_anchor is not None:
        tail.append(forced_anchor)

    add_vertices(
        _merge_window(
            tail,
            forced_anchor=forced_anchor,
            forced_direction=forced_direction,
        )
    )

    for spike in marker_only_spikes:
        remember(spike.point)

    stage2_keys = {_key(point) for point in augmented.display_markers}
    if not set(markers).issubset(stage2_keys):
        raise RuntimeError("stage3 produced a point absent from stage2")

    segments.sort(key=lambda item: (item.start.day, item.end.day, item.kind))
    sideways_out.sort(key=lambda item: (item.start.day, item.end.day))

    return SimplifiedLineResult(
        markers=tuple(
            sorted(
                markers.values(),
                key=lambda item: (item.day, item.pivot_type),
            )
        ),
        segments=tuple(segments),
        sideways_segments=tuple(sideways_out),
    )
