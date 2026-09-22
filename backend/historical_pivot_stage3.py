"""Stage 3: merge Stage 2's upper/lower RDP points into one wave line.

Inputs:
- Stage 2 final high/low RDP points
- Stage 2 spike/sideways classification metadata
- Stage 2 rapid-move candidates

Output:
- one chronological wave line
- explicit marker-only spike points, if any
- all Stage-2 rapid candidates carried provisionally into Stage 4

Spike/sideways line structure is encoded directly in segment kinds; Stage 3 does
not pass a second copy of sideways metadata downstream.

Stage 3 does not reclassify or clean up the merged wave. Rapid candidate
entry/end points and hard spike/sideways endpoints are kept on the connected
line so Stage 4 receives the full merged chronology. Only marker-only spikes
may remain as detached standalone markers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from historical_pivot_shared import (
    ChartGeometry,
    LinePoint,
    LineRapidMoveCandidate,
    PivotPoint,
    SidewaysSegment,
    Stage3LineResult,
    SimplifiedLineSegment,
    screen_angle_degrees,
)
from historical_pivot_stage2 import Stage2Result


TRANSIENT_EXCURSION_MAX_ANGLE_DEG = 30.0


def _key(point: PivotPoint) -> tuple[date, float, str]:
    return point.day, point.value, point.pivot_type



def _transient_excursion_keys(
    stage2: Stage2Result,
    geometry: ChartGeometry,
) -> set[tuple[date, float, str]]:
    """Return ordinary same-side RDP peaks/troughs that immediately mean-revert.

    For three consecutive same-side RDP points A-B-C:
    - HIGH side: B > A and C < A
    - LOW side:  B < A and C > A
    - the screen-space interior angle at B is <= 30 degrees

    Such B is a merge target, not a protected structure. Any spike, protected
    sideways boundary, or rapid-move provisional endpoint outranks this rule
    and is therefore never returned here.
    """
    protected: set[tuple[date, float, str]] = set()

    for spike in stage2.spike_peaks:
        protected.add(_key(spike.point))
        if spike.entry is not None:
            protected.add(_key(spike.entry))

    for sideways in (
        *stage2.high_sideways_segments,
        *stage2.low_sideways_segments,
    ):
        if getattr(sideways, "protected", True):
            protected.add(_key(sideways.start))
            protected.add(_key(sideways.end))

    for rapid in stage2.rapid_move_candidates:
        protected.add(_key(rapid.start))
        protected.add(_key(rapid.end))

    merge_targets: set[tuple[date, float, str]] = set()
    for pivot_type, points in (
        ("high", stage2.high_pivots),
        ("low", stage2.low_pivots),
    ):
        ordered = sorted(points, key=lambda item: item.day)
        for a_point, b_point, c_point in zip(
            ordered,
            ordered[1:],
            ordered[2:],
        ):
            b_key = _key(b_point)
            if b_key in protected:
                continue

            if pivot_type == "high":
                reverted = (
                    b_point.value > a_point.value
                    and c_point.value < a_point.value
                )
            else:
                reverted = (
                    b_point.value < a_point.value
                    and c_point.value > a_point.value
                )
            if not reverted:
                continue

            angle = screen_angle_degrees(
                a_point,
                b_point,
                c_point,
                geometry,
            )
            if angle <= TRANSIENT_EXCURSION_MAX_ANGLE_DEG:
                merge_targets.add(b_key)

    return merge_targets


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
) -> list[_Wave]:
    """Build continuous candidate waves from consensus direction runs."""
    if not points:
        return []

    active_runs = list(runs)

    if not active_runs:
        return []

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


def _merge_window(
    points: Sequence[PivotPoint],
    *,
    forced_anchor: PivotPoint | None = None,
) -> list[PivotPoint]:
    ordered = _unique(points)
    if forced_anchor is not None and _key(forced_anchor) not in {_key(p) for p in ordered}:
        ordered.append(forced_anchor)
        ordered.sort(key=lambda item: (item.day, item.pivot_type))
    if not ordered:
        return []

    runs = _consensus_runs(ordered)

    if not runs:
        # Not enough two-boundary information to define a normal wave.
        # Keep only a protected incoming anchor if one exists.
        return [forced_anchor] if forced_anchor is not None else []

    waves = _build_waves(
        ordered,
        runs,
        forced_anchor=forced_anchor,
    )

    if not waves:
        return [forced_anchor] if forced_anchor is not None else []

    # One boundary can end before the other, so a final opposite-side point
    # may not form another consensus run.
    #
    # Preserve it ONLY when it fully exceeds the just-finished wave's start
    # extreme, proving that the apparent final wave was actually reversed:
    #
    #   up wave   LOW(start) -> HIGH(end) -> lower LOW(< start)  => keep LOW
    #   down wave HIGH(start) -> LOW(end)  -> higher HIGH(> start) => keep HIGH
    #
    # A merely following higher-low / lower-high is still inside the same normal
    # wave and must disappear here.
    last_wave = waves[-1]
    last_end = last_wave.end
    opposite_type = "low" if last_end.pivot_type == "high" else "high"
    trailing = [
        point
        for point in ordered
        if point.day > last_end.day and point.pivot_type == opposite_type
    ]
    if trailing:
        trailing_end = (
            min(trailing, key=lambda item: (item.value, item.day))
            if opposite_type == "low"
            else max(trailing, key=lambda item: (item.value, item.day))
        )

        resumes_prior_trend = (
            opposite_type == "low"
            and trailing_end.value < last_wave.start.value
        ) or (
            opposite_type == "high"
            and trailing_end.value > last_wave.start.value
        )

        if resumes_prior_trend and last_end.day < trailing_end.day:
            waves.append(
                _Wave(
                    direction=-1 if opposite_type == "low" else 1,
                    start=last_end,
                    end=trailing_end,
                )
            )

    vertices = [waves[0].start]
    for wave in waves:
        if vertices[-1] != wave.start:
            vertices.append(wave.start)
        if vertices[-1] != wave.end:
            vertices.append(wave.end)
    return vertices


def _collapse_same_direction_interiors(
    points: Sequence[PivotPoint],
    mandatory_keys: set[tuple[date, float, str]],
) -> list[PivotPoint]:
    """Remove non-mandatory interior vertices that do not change direction.

    Once the upper/lower lines have been merged into one chronological line,
    a point between two points moving in the same direction is not a wave
    vertex. Mandatory protected/provisional structure endpoints remain so the
    stage that owns that structure can still decide it.
    """
    result = list(points)
    changed = True
    while changed and len(result) >= 3:
        changed = False
        collapsed = [result[0]]
        for index in range(1, len(result) - 1):
            previous = collapsed[-1]
            current = result[index]
            following = result[index + 1]

            if _key(current) in mandatory_keys:
                collapsed.append(current)
                continue

            first_delta = current.value - previous.value
            second_delta = following.value - current.value
            same_direction = (
                first_delta != 0
                and second_delta != 0
                and (first_delta > 0) == (second_delta > 0)
            )
            if same_direction:
                changed = True
                continue

            collapsed.append(current)

        collapsed.append(result[-1])
        result = collapsed

    return result


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
        if not getattr(sideways, "protected", True):
            continue
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
) -> Stage3LineResult:
    """Return one Stage-3 wave line from Stage-2 points only."""

    marker_only_spikes = tuple(
        spike for spike in augmented.spike_peaks if spike.marker_only
    )
    marker_only_keys = {_key(spike.point) for spike in marker_only_spikes}

    # Stage 3 owns only the upper/lower RDP-line merge.  Cleanup decisions
    # belong downstream, so no transient-excursion or post-merge point pruning
    # is applied here.
    points = _unique(
        tuple(
            point
            for point in (*augmented.high_pivots, *augmented.low_pivots)
            if _key(point) not in marker_only_keys
        )
    )

    hard = _hard_segments(augmented)
    segments: list[SimplifiedLineSegment] = []
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

    # Protection constrains OUTPUT, not JUDGMENT.  Build the normal wave over
    # the complete Stage-2 timeline first.  Then force protected structure back
    # into that line as mandatory vertices/segments without allowing a direct
    # connection to skip across it.
    base_vertices = _merge_window(points)

    # A normal-wave vertex inside a hard segment must not split that protected
    # structure.  The hard segment owns its whole interior span.
    filtered_base = [
        point
        for point in base_vertices
        if not any(
            protected.start.day < point.day < protected.end.day
            for protected in hard
        )
    ]

    structural: dict[tuple[date, float, str], PivotPoint] = {
        _key(point): point for point in filtered_base
    }

    # Rapid candidates are only provisional at Stage 2. Stage 3 may judge the
    # normal wave without them, but it must not delete their entry/end points:
    # Stage 4 owns the rapid-move confirmation/rejection decision. Keep both
    # endpoints as connected vertices (never as detached standalone markers).
    for candidate in augmented.rapid_move_candidates:
        structural[_key(candidate.start)] = candidate.start
        structural[_key(candidate.end)] = candidate.end

    hard_kind: dict[tuple[tuple[date, float, str], tuple[date, float, str]], str] = {}
    for protected in hard:
        structural[_key(protected.start)] = protected.start
        structural[_key(protected.end)] = protected.end
        hard_kind[(_key(protected.start), _key(protected.end))] = protected.kind
    structural_points = sorted(
        structural.values(),
        key=lambda item: (item.day, item.pivot_type),
    )

    mandatory_keys = {
        _key(candidate.start)
        for candidate in augmented.rapid_move_candidates
    } | {
        _key(candidate.end)
        for candidate in augmented.rapid_move_candidates
    } | {
        _key(protected.start)
        for protected in hard
    } | {
        _key(protected.end)
        for protected in hard
    }
    # Do not prune merged vertices in Stage 3.  The merged chronology is the
    # complete input handed to Stage 4.
    if len(structural_points) == 1:
        remember(structural_points[0])
    for left, right in zip(structural_points, structural_points[1:]):
        kind = hard_kind.get((_key(left), _key(right)), "trend")
        add_segment(left, right, kind)

    for spike in marker_only_spikes:
        remember(spike.point)

    # Only marker-only spikes may survive as true standalone markers.
    # Rapid endpoints are provisional connected vertices and must all reach
    # Stage 4, which owns their final confirmation/rejection.
    connected_keys = {
        _key(point)
        for segment in segments
        for point in (segment.start, segment.end)
    }
    surviving_rapid = tuple(augmented.rapid_move_candidates)
    if any(
        _key(candidate.start) not in connected_keys
        or _key(candidate.end) not in connected_keys
        for candidate in surviving_rapid
    ):
        raise RuntimeError("stage3 failed to connect a rapid candidate endpoint")
    stage2_keys = {_key(point) for point in augmented.display_markers}
    if not set(markers).issubset(stage2_keys):
        raise RuntimeError("stage3 produced a point absent from stage2")

    segments.sort(key=lambda item: (item.start.day, item.end.day, item.kind))

    line_by_pivot_key = {
        _key(point): LinePoint(point.day, point.value)
        for point in markers.values()
    }
    line_markers = tuple(sorted(
        line_by_pivot_key.values(),
        key=lambda item: item.day,
    ))
    line_segments = tuple(
        SimplifiedLineSegment(
            start=line_by_pivot_key[_key(segment.start)],
            end=line_by_pivot_key[_key(segment.end)],
            kind=segment.kind,
        )
        for segment in segments
    )
    line_marker_only_points = tuple(sorted(
        (
            line_by_pivot_key[_key(spike.point)]
            for spike in marker_only_spikes
        ),
        key=lambda item: item.day,
    ))
    line_rapid_candidates = tuple(
        LineRapidMoveCandidate(
            start=line_by_pivot_key[_key(candidate.start)],
            end=line_by_pivot_key[_key(candidate.end)],
            direction=candidate.direction,
            visual_y_share=candidate.visual_y_share,
        )
        for candidate in surviving_rapid
    )

    return Stage3LineResult(
        markers=line_markers,
        segments=line_segments,
        marker_only_points=line_marker_only_points,
        rapid_move_candidates=line_rapid_candidates,
    )
