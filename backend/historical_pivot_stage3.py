"""Stage 3: merge Stage 2 points into one wave line.

Stage 3 has exactly one responsibility:
    Stage 2 sealed points -> one chronological wave line.

It never reads Stage 1 candidates, envelope data, deleted points, or any earlier
stage result.  Ordinary points are consumed once by a single state machine.
Protected sideways/spike structure from Stage 2 is treated as a hard boundary.

Normal wave rules:
- rising wave: keep start low -> final high
- falling wave: keep start high -> final low
- following/internal points do not become vertices
- an opposite-side bounce is provisional until the following same-side extreme
  is known
- if the old trend makes a new extreme, the provisional reversal is cancelled
- the first chart wave keeps its real starting anchor
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
    SpikePeak,
)
from historical_pivot_stage2 import Stage2Result


def _key(point: PivotPoint) -> tuple[date, float, str]:
    return point.day, point.value, point.pivot_type


def _sorted_unique(points: Sequence[PivotPoint]) -> list[PivotPoint]:
    by_key = {_key(point): point for point in points}
    return sorted(by_key.values(), key=lambda item: (item.day, item.pivot_type))


def _first_side_direction(points: Sequence[PivotPoint], pivot_type: str) -> int | None:
    same_side = sorted(
        (point for point in points if point.pivot_type == pivot_type),
        key=lambda item: item.day,
    )
    for left, right in zip(same_side, same_side[1:]):
        if right.value > left.value:
            return 1
        if right.value < left.value:
            return -1
    return None


def _initial_direction(points: Sequence[PivotPoint]) -> str:
    """Choose the first normal-wave direction from the two RDP boundaries."""
    high_direction = _first_side_direction(points, "high")
    low_direction = _first_side_direction(points, "low")

    if high_direction is not None and high_direction == low_direction:
        return "up" if high_direction > 0 else "down"
    if high_direction is not None and low_direction is None:
        return "up" if high_direction > 0 else "down"
    if low_direction is not None and high_direction is None:
        return "up" if low_direction > 0 else "down"

    first = min(points, key=lambda item: (item.day, item.pivot_type))
    return "down" if first.pivot_type == "high" else "up"


@dataclass
class _PendingReversal:
    old_direction: str
    old_anchor: PivotPoint
    turn: PivotPoint
    safe_retrace_seen: bool = False


def _merge_wave_window(
    points: Sequence[PivotPoint],
    *,
    forced_anchor: PivotPoint | None = None,
    forced_direction: str | None = None,
) -> list[PivotPoint]:
    """Reduce one ordinary window with one state machine.

    A reversal can be provisional.  If the old trend subsequently makes a new
    extreme, the provisional turn is removed and the old wave simply extends.
    """
    ordered = _sorted_unique(points)
    if forced_anchor is not None and _key(forced_anchor) not in {_key(p) for p in ordered}:
        ordered.append(forced_anchor)
        ordered.sort(key=lambda item: (item.day, item.pivot_type))
    if not ordered:
        return []

    direction = forced_direction or _initial_direction(ordered)
    anchor_type = "low" if direction == "up" else "high"

    if forced_anchor is not None:
        anchor = forced_anchor
    else:
        anchor = next(
            (point for point in ordered if point.pivot_type == anchor_type),
            ordered[0],
        )

    confirmed: list[PivotPoint] = [anchor]
    extreme: PivotPoint | None = None

    # Ordinary reversal-candidate state.
    pullback_low: PivotPoint | None = None
    lower_high_seen = False
    rebound_high: PivotPoint | None = None
    higher_low_seen = False

    # A newly detected reversal remains provisional until the new trend survives
    # one retracement and makes another same-direction extreme.
    pending: _PendingReversal | None = None

    def reset_candidates() -> None:
        nonlocal pullback_low, lower_high_seen, rebound_high, higher_low_seen
        pullback_low = None
        lower_high_seen = False
        rebound_high = None
        higher_low_seen = False

    def start_provisional(new_direction: str, trigger: PivotPoint) -> None:
        nonlocal direction, anchor, extreme, pending
        if extreme is None:
            return
        turn = extreme
        confirmed.append(turn)
        pending = _PendingReversal(
            old_direction=direction,
            old_anchor=anchor,
            turn=turn,
        )
        direction = new_direction
        anchor = turn
        extreme = trigger
        reset_candidates()

    def cancel_provisional(resuming_extreme: PivotPoint) -> None:
        nonlocal direction, anchor, extreme, pending
        assert pending is not None
        if confirmed and confirmed[-1] == pending.turn:
            confirmed.pop()
        direction = pending.old_direction
        anchor = pending.old_anchor
        extreme = resuming_extreme
        pending = None
        reset_candidates()

    for point in ordered:
        if point.day <= anchor.day:
            # Before the first opposite-side point exists, a more extreme same-side
            # point can still replace an unprotected starting anchor.
            if extreme is None and point.pivot_type == anchor.pivot_type:
                if (
                    direction == "up"
                    and point.value < anchor.value
                ) or (
                    direction == "down"
                    and point.value > anchor.value
                ):
                    anchor = point
                    confirmed[0] = point
            continue

        if extreme is None:
            wanted = "high" if direction == "up" else "low"
            if point.pivot_type == wanted:
                extreme = point
            elif point.pivot_type == anchor.pivot_type:
                if (
                    direction == "up"
                    and point.value < anchor.value
                ) or (
                    direction == "down"
                    and point.value > anchor.value
                ):
                    anchor = point
                    confirmed[-1] = point
            continue

        # A provisional reversal can still be cancelled by the old trend making
        # a new extreme beyond the provisional turning point.
        if pending is not None:
            if (
                direction == "up"
                and point.pivot_type == "low"
                and point.value < pending.turn.value
            ):
                cancel_provisional(point)
                continue
            if (
                direction == "down"
                and point.pivot_type == "high"
                and point.value > pending.turn.value
            ):
                cancel_provisional(point)
                continue

            if direction == "up":
                if point.pivot_type == "low":
                    if point.value > pending.turn.value:
                        pending.safe_retrace_seen = True
                        if pullback_low is None or point.value < pullback_low.value:
                            pullback_low = point
                    continue

                if point.value > extreme.value:
                    if pending.safe_retrace_seen:
                        pending = None
                    extreme = point
                    pullback_low = None
                    lower_high_seen = False
                continue

            # pending downtrend
            if point.pivot_type == "high":
                if point.value < pending.turn.value:
                    pending.safe_retrace_seen = True
                    if rebound_high is None or point.value > rebound_high.value:
                        rebound_high = point
                continue

            if point.value < extreme.value:
                if pending.safe_retrace_seen:
                    pending = None
                extreme = point
                rebound_high = None
                higher_low_seen = False
            continue

        if direction == "up":
            if point.pivot_type == "high":
                if point.value > extreme.value:
                    # Same rising wave: update only the endpoint.
                    extreme = point
                    pullback_low = None
                    lower_high_seen = False
                elif pullback_low is not None:
                    lower_high_seen = True
                continue

            # A low inside an uptrend is provisional.
            if pullback_low is None:
                pullback_low = point
                continue

            if lower_high_seen and point.value < pullback_low.value:
                start_provisional("down", point)
                continue

            if point.value < pullback_low.value:
                pullback_low = point
            continue

        # direction == "down"
        if point.pivot_type == "low":
            if point.value < extreme.value:
                # Same falling wave: update only the endpoint.
                extreme = point
                rebound_high = None
                higher_low_seen = False
            elif rebound_high is not None:
                higher_low_seen = True
            continue

        # A high inside a downtrend is provisional.
        if rebound_high is None:
            rebound_high = point
            continue

        if higher_low_seen and point.value > rebound_high.value:
            start_provisional("up", point)
            continue

        if point.value > rebound_high.value:
            rebound_high = point

    if extreme is not None and (not confirmed or confirmed[-1] != extreme):
        confirmed.append(extreme)

    # Consecutive duplicate vertices are impossible by construction, but enforce it
    # at the boundary so Stage 4 receives one clean ordered line.
    result: list[PivotPoint] = []
    for point in confirmed:
        if result and _key(result[-1]) == _key(point):
            continue
        result.append(point)
    return result


@dataclass(frozen=True)
class _HardSegment:
    start: PivotPoint
    end: PivotPoint
    kind: str
    sideways: SidewaysSegment | None = None

    @property
    def restart_direction(self) -> str:
        # A protected high endpoint begins a possible down wave; a protected low
        # endpoint begins a possible up wave.
        return "down" if self.end.pivot_type == "high" else "up"


def _hard_segments(stage2: Stage2Result) -> list[_HardSegment]:
    segments: list[_HardSegment] = []

    for spike in stage2.spike_peaks:
        if spike.marker_only or spike.entry is None:
            continue
        if spike.entry.day >= spike.point.day:
            continue
        segments.append(
            _HardSegment(
                start=spike.entry,
                end=spike.point,
                kind="spike",
            )
        )

    for sideways in (
        *stage2.high_sideways_segments,
        *stage2.low_sideways_segments,
    ):
        if sideways.start.day >= sideways.end.day:
            continue
        segments.append(
            _HardSegment(
                start=sideways.start,
                end=sideways.end,
                kind="sideways",
                sideways=sideways,
            )
        )

    # A normal spike takes precedence over any overlapping ordinary sideways span.
    segments.sort(
        key=lambda item: (
            item.start.day,
            0 if item.kind == "spike" else 1,
            item.end.day,
        )
    )
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
    """Return Stage 3's single merged wave line.

    geometry is intentionally unused here.  Angle/sideways classification was
    completed before this stage; Stage 3 receives only Stage 2's sealed points
    and protection metadata.
    """
    del geometry

    marker_only_spikes = tuple(
        spike for spike in augmented.spike_peaks if spike.marker_only
    )
    marker_only_keys = {_key(spike.point) for spike in marker_only_spikes}

    line_points = _sorted_unique(
        tuple(
            point
            for point in (*augmented.high_pivots, *augmented.low_pivots)
            if _key(point) not in marker_only_keys
        )
    )
    if not line_points:
        return SimplifiedLineResult(
            markers=tuple(
                sorted(
                    (spike.point for spike in marker_only_spikes),
                    key=lambda item: (item.day, item.pivot_type),
                )
            ),
            segments=(),
            sideways_segments=(),
        )

    hard = _hard_segments(augmented)
    segments: list[SimplifiedLineSegment] = []
    sideways_out: list[SidewaysSegment] = []
    used: dict[tuple[date, float, str], PivotPoint] = {}

    def remember(point: PivotPoint) -> None:
        used[_key(point)] = point

    def add_segment(start: PivotPoint, end: PivotPoint, kind: str) -> None:
        if start.day >= end.day:
            return
        remember(start)
        remember(end)
        candidate = SimplifiedLineSegment(start=start, end=end, kind=kind)
        if candidate not in segments:
            segments.append(candidate)

    def add_wave_vertices(vertices: Sequence[PivotPoint]) -> None:
        for left, right in zip(vertices, vertices[1:]):
            add_segment(left, right, "trend")
        if len(vertices) == 1:
            remember(vertices[0])

    cursor_day: date | None = None
    forced_anchor: PivotPoint | None = None
    forced_direction: str | None = None

    for protected in hard:
        window = [
            point
            for point in line_points
            if (cursor_day is None or point.day >= cursor_day)
            and point.day < protected.start.day
        ]
        if forced_anchor is not None:
            window.append(forced_anchor)

        vertices = _merge_wave_window(
            window,
            forced_anchor=forced_anchor,
            forced_direction=forced_direction,
        )
        add_wave_vertices(vertices)

        if vertices and vertices[-1] != protected.start:
            add_segment(vertices[-1], protected.start, "trend")
        elif not vertices and forced_anchor is not None and forced_anchor != protected.start:
            add_segment(forced_anchor, protected.start, "trend")
        else:
            remember(protected.start)

        add_segment(protected.start, protected.end, protected.kind)
        if protected.sideways is not None:
            sideways_out.append(protected.sideways)

        forced_anchor = protected.end
        forced_direction = protected.restart_direction
        cursor_day = protected.end.day

    tail = [
        point
        for point in line_points
        if cursor_day is None or point.day >= cursor_day
    ]
    if forced_anchor is not None:
        tail.append(forced_anchor)

    tail_vertices = _merge_wave_window(
        tail,
        forced_anchor=forced_anchor,
        forced_direction=forced_direction,
    )
    add_wave_vertices(tail_vertices)

    for spike in marker_only_spikes:
        remember(spike.point)

    # Stage 3 is deletion-only relative to Stage 2.  There is no source from
    # which an earlier candidate/deleted point could re-enter.
    stage2_keys = {_key(point) for point in augmented.display_markers}
    output_keys = set(used)
    if not output_keys.issubset(stage2_keys):
        raise RuntimeError("stage3 produced a point absent from stage2")

    segments.sort(key=lambda item: (item.start.day, item.end.day, item.kind))
    sideways_out.sort(key=lambda item: (item.start.day, item.end.day))

    return SimplifiedLineResult(
        markers=tuple(
            sorted(
                used.values(),
                key=lambda item: (item.day, item.pivot_type),
            )
        ),
        segments=tuple(segments),
        sideways_segments=tuple(sideways_out),
    )
