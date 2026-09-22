"""Stage 2: classify special structures on the finished Stage-1 RDP sets.

Stage 2 owns three classifications:
- rapid rise/fall candidates (provisional protection only)
- spike candidates (provisional protection only)
- sideways ranges

Stage 2 never changes the Stage-1 RDP set. Rapid-move candidates carry their
own entry/end points and are handed to Stage 3 as candidate metadata only; no
duplicated provisional-point list is emitted. Consolidation/finalization belongs
only to Stage 4.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from historical_pivot_shared import (
    ChartGeometry,
    PivotPoint,
    RapidMoveCandidate,
    SidewaysSegment,
    SpikePeak,
    classify_sideways_reference_line,
    screen_segment_angle_degrees,
)
from historical_pivot_stage1 import BasePivotResult


RAPID_MOVE_MIN_VISUAL_Y_SHARE = 0.20
RAPID_MOVE_MAX_VERTICAL_ANGLE_DEG = 45.0
RAPID_MOVE_MIN_VERTICAL_ANGLE_DEG = 20.0
RAPID_MOVE_FULL_ANGLE_SHARE = 0.80


def _key(point: PivotPoint) -> tuple:
    return point.day, point.value, point.pivot_type


@dataclass(frozen=True)
class Stage2Result:
    high_pivots: tuple[PivotPoint, ...]
    low_pivots: tuple[PivotPoint, ...]
    spike_peaks: tuple[SpikePeak, ...] = ()
    high_sideways_segments: tuple[SidewaysSegment, ...] = ()
    low_sideways_segments: tuple[SidewaysSegment, ...] = ()
    rapid_move_candidates: tuple[RapidMoveCandidate, ...] = ()

    @property
    def display_markers(self) -> tuple[PivotPoint, ...]:
        return tuple(sorted(
            (*self.high_pivots, *self.low_pivots),
            key=lambda item: (item.day, item.pivot_type),
        ))


def _visual_y_share(
    start: PivotPoint,
    end: PivotPoint,
    geometry: ChartGeometry,
) -> float:
    return abs(float(end.value) - float(start.value)) / (
        float(geometry.y_max) - float(geometry.y_min)
    )


def _entry_before_peak(
    *,
    a_point: PivotPoint,
    peak: PivotPoint,
    opposite_rdp: Sequence[PivotPoint],
    opposite_candidates: Sequence[PivotPoint],
    direction: str,
) -> PivotPoint:
    """Return the effective A-B entry point.

    The entry is NOT limited to already-selected RDP pivots.

    Up move:
      only a plateau LOW below A can replace A; choose the lowest one.
    Down move:
      only a plateau HIGH above A can replace A; choose the highest one.

    If no plateau point improves on A in the move's opposite direction, A
    itself remains the entry. If the selected plateau point already exists in
    the selected opposite-side RDP set, reuse that exact point. Otherwise
    return the plateau candidate so Stage 2 can add it to the RDP set.

    This entry rule is shared by rapid-move and spike classification.
    """
    eligible = [
        point
        for point in opposite_candidates
        if (
            a_point.day < point.day < peak.day
            and (
                (direction == "up" and point.value < a_point.value)
                or (direction == "down" and point.value > a_point.value)
            )
        )
    ]
    if not eligible:
        return a_point

    selected = (
        min(eligible, key=lambda item: (item.value, item.day))
        if direction == "up"
        else max(eligible, key=lambda item: (item.value, item.day))
    )

    selected_key = _key(selected)
    for point in opposite_rdp:
        if _key(point) == selected_key:
            return point
    return selected


def _classify_spike_candidates(
    stage1: BasePivotResult,
    rapid_candidates: Sequence[RapidMoveCandidate],
) -> tuple[SpikePeak, ...]:
    """Mark rapid moves with no follow-in opposite RDP as spike candidates.

    Stage 2 does not measure spike angles or finalize spike structure.
    It only uses the still-separated upper/lower RDP boundaries to answer the
    one question that cannot be answered after Stage 3 merge:

      Did the opposite-side RDP boundary follow the move inside A-C?

    Up move:
      find the HIGH RDP A immediately before the rapid peak B and the HIGH RDP
      C immediately after B. If any LOW RDP between A and C rises above
      max(A, C), the opposite boundary followed the move, so it is not a spike
      candidate.

    Down move is the exact mirror.

    When no such follow-in point exists, keep the rapid entry/peak provisionally
    protected through the existing rapid-candidate protection and tag the peak
    as a spike candidate. No angle/retracement/follow-up judgment is done here.
    """
    high_rdp = tuple(sorted(stage1.high_pivots, key=lambda item: item.day))
    low_rdp = tuple(sorted(stage1.low_pivots, key=lambda item: item.day))
    found: list[SpikePeak] = []

    for rapid in rapid_candidates:
        if rapid.direction > 0:
            peak_index = next(
                (
                    idx for idx, point in enumerate(high_rdp)
                    if _key(point) == _key(rapid.end)
                ),
                None,
            )
            if peak_index is None or peak_index == 0 or peak_index + 1 >= len(high_rdp):
                continue
            a_point = high_rdp[peak_index - 1]
            peak = high_rdp[peak_index]
            c_point = high_rdp[peak_index + 1]
            followed = any(
                item.value > max(a_point.value, c_point.value)
                for item in low_rdp
                if a_point.day <= item.day <= c_point.day
            )
            if followed:
                continue
            found.append(
                SpikePeak(
                    point=peak,
                    direction="up",
                    angle_deg=0.0,
                    entry=rapid.start,
                    marker_only=False,
                )
            )
            continue

        peak_index = next(
            (
                idx for idx, point in enumerate(low_rdp)
                if _key(point) == _key(rapid.end)
            ),
            None,
        )
        if peak_index is None or peak_index == 0 or peak_index + 1 >= len(low_rdp):
            continue
        a_point = low_rdp[peak_index - 1]
        peak = low_rdp[peak_index]
        c_point = low_rdp[peak_index + 1]
        followed = any(
            item.value < min(a_point.value, c_point.value)
            for item in high_rdp
            if a_point.day <= item.day <= c_point.day
        )
        if followed:
            continue
        found.append(
            SpikePeak(
                point=peak,
                direction="down",
                angle_deg=0.0,
                entry=rapid.start,
                marker_only=False,
            )
        )

    return tuple(sorted(found, key=lambda item: item.point.day))


def _rapid_move_allowed_vertical_angle(share: float) -> float:
    """Scale the rapid-move vertical-axis angle limit by visual height.

    20% visual height -> 20 degrees.
    80% visual height -> 45 degrees.
    Above 80% -> fixed at 45 degrees.
    """
    if share <= RAPID_MOVE_MIN_VISUAL_Y_SHARE:
        return RAPID_MOVE_MIN_VERTICAL_ANGLE_DEG
    if share >= RAPID_MOVE_FULL_ANGLE_SHARE:
        return RAPID_MOVE_MAX_VERTICAL_ANGLE_DEG

    ratio = (
        share - RAPID_MOVE_MIN_VISUAL_Y_SHARE
    ) / (
        RAPID_MOVE_FULL_ANGLE_SHARE
        - RAPID_MOVE_MIN_VISUAL_Y_SHARE
    )
    return (
        RAPID_MOVE_MIN_VERTICAL_ANGLE_DEG
        + ratio
        * (
            RAPID_MOVE_MAX_VERTICAL_ANGLE_DEG
            - RAPID_MOVE_MIN_VERTICAL_ANGLE_DEG
        )
    )


def _rapid_move_passes(
    *,
    entry: PivotPoint,
    peak: PivotPoint,
    same_side_rdp: Sequence[PivotPoint],
    direction: int,
    geometry: ChartGeometry,
    min_visual_y_share: float,
) -> tuple[bool, float]:
    """Apply the single variable-angle rapid-move gate.

    visual height must be at least 20%.
    Allowed vertical-axis deviation scales continuously:
      20% -> 20 degrees
      80% -> 45 degrees
      above 80% -> 45 degrees
    """
    share = _visual_y_share(entry, peak, geometry)
    vertical_angle = 90.0 - abs(
        screen_segment_angle_degrees(entry, peak, geometry)
    )

    if share < min_visual_y_share:
        return False, share

    allowed_vertical_angle = _rapid_move_allowed_vertical_angle(share)
    return vertical_angle <= allowed_vertical_angle, share


def _classify_rapid_moves(
    stage1: BasePivotResult,
    geometry: ChartGeometry,
    *,
    min_visual_y_share: float,
) -> tuple[RapidMoveCandidate, ...]:
    """Create provisional rapid-move candidates from the separate RDP sides."""
    high_rdp = tuple(sorted(stage1.high_pivots, key=lambda item: item.day))
    low_rdp = tuple(sorted(stage1.low_pivots, key=lambda item: item.day))
    found: dict[tuple, RapidMoveCandidate] = {}

    for a_point, peak in zip(high_rdp, high_rdp[1:]):
        if peak.value <= a_point.value:
            continue
        entry = _entry_before_peak(
            a_point=a_point,
            peak=peak,
            opposite_rdp=low_rdp,
            opposite_candidates=stage1.low_candidates,
            direction="up",
        )
        if entry is None:
            continue
        passes, share = _rapid_move_passes(
            entry=entry,
            peak=peak,
            same_side_rdp=high_rdp,
            direction=1,
            geometry=geometry,
            min_visual_y_share=min_visual_y_share,
        )
        if not passes:
            continue
        candidate = RapidMoveCandidate(
            start=entry,
            end=peak,
            direction=1,
            visual_y_share=share,
        )
        found[(_key(entry), _key(peak), 1)] = candidate

    for a_point, peak in zip(low_rdp, low_rdp[1:]):
        if peak.value >= a_point.value:
            continue
        if _key(peak) in spike_peak_keys:
            continue
        entry = _entry_before_peak(
            a_point=a_point,
            peak=peak,
            opposite_rdp=high_rdp,
            opposite_candidates=stage1.high_candidates,
            direction="down",
        )
        if entry is None:
            continue
        passes, share = _rapid_move_passes(
            entry=entry,
            peak=peak,
            same_side_rdp=low_rdp,
            direction=-1,
            geometry=geometry,
            min_visual_y_share=min_visual_y_share,
        )
        if not passes:
            continue
        candidate = RapidMoveCandidate(
            start=entry,
            end=peak,
            direction=-1,
            visual_y_share=share,
        )
        found[(_key(entry), _key(peak), -1)] = candidate

    return tuple(sorted(
        found.values(),
        key=lambda item: (item.start.day, item.end.day, item.direction),
    ))


def _sideways_pairs(
    points: Sequence[PivotPoint],
    reference_side: str,
    geometry: ChartGeometry,
) -> tuple[SidewaysSegment, ...]:
    """Classify/merge sideways runs using only the approved 5-degree rule.

    The 5-degree angle test itself is direction-agnostic, but the reference
    side must match the trend entering the run:
    - after an uptrend, only a HIGH-side flat run can be a sideways range;
    - after a downtrend, only a LOW-side flat run can be a sideways range.
    When there is no previous same-side point at the graph edge, the incoming
    trend is unknown and the run may still be classified.

    Protection is decided only after the run is known:
    - if the same-side trend before and after the sideways run differs, protect
      both boundaries;
    - if the before/after trend is the same, classify it but do not protect it;
    - if either side is unavailable because the sideways run touches the graph
      start or graph end, protect both boundaries.
    """
    ordered = tuple(sorted(points, key=lambda item: item.day))
    if len(ordered) < 2:
        return ()

    raw_runs: list[tuple[int, int, float]] = []
    run_start: int | None = None
    run_end: int | None = None
    run_angle = 0.0

    def flush() -> None:
        nonlocal run_start, run_end, run_angle
        if run_start is not None and run_end is not None and run_end > run_start:
            raw_runs.append((run_start, run_end, run_angle))
        run_start = None
        run_end = None
        run_angle = 0.0

    for idx in range(len(ordered) - 1):
        left = ordered[idx]
        right = ordered[idx + 1]
        pair_angle = screen_segment_angle_degrees(left, right, geometry)

        if abs(pair_angle) > 5.0:
            flush()
            continue

        if run_start is None:
            run_start = idx
            run_end = idx + 1
            run_angle = pair_angle
            continue

        if run_end == idx:
            merged_angle = screen_segment_angle_degrees(
                ordered[run_start],
                right,
                geometry,
            )
            if abs(merged_angle) <= 5.0:
                run_end = idx + 1
                run_angle = merged_angle
                continue

        flush()
        run_start = idx
        run_end = idx + 1
        run_angle = pair_angle

    flush()

    found: list[SidewaysSegment] = []
    for start_idx, end_idx, angle in raw_runs:
        start_point = ordered[start_idx]
        end_point = ordered[end_idx]

        previous = ordered[start_idx - 1] if start_idx > 0 else None
        following = ordered[end_idx + 1] if end_idx + 1 < len(ordered) else None

        incoming = 0
        outgoing = 0
        if previous is not None:
            incoming = 1 if start_point.value > previous.value else -1 if start_point.value < previous.value else 0
        if following is not None:
            outgoing = 1 if following.value > end_point.value else -1 if following.value < end_point.value else 0

        # A sideways range belongs to the boundary that the incoming trend
        # is actually riding.  A falling trend rides LOW-side flats; a rising
        # trend rides HIGH-side flats.  Do not create an opposite-side
        # "sideways" merely because that same-side RDP pair happens to be flat.
        if previous is not None:
            if reference_side == "high" and incoming <= 0:
                continue
            if reference_side == "low" and incoming >= 0:
                continue

        edge_unknown = previous is None or following is None
        protected = edge_unknown or incoming == 0 or outgoing == 0 or incoming != outgoing
        prior_trend = "up" if incoming > 0 else "down" if incoming < 0 else "unknown"

        found.append(
            SidewaysSegment(
                start=start_point,
                end=end_point,
                prior_trend=prior_trend,
                reference_side=reference_side,
                angle_deg=angle,
                protected=protected,
            )
        )

    return tuple(found)

def classify_special_structures(
    stage1: BasePivotResult,
    geometry: ChartGeometry,
    *,
    rapid_min_visual_y_share: float = RAPID_MOVE_MIN_VISUAL_Y_SHARE,
) -> Stage2Result:
    """Classify all Stage-2 special structures without changing Stage-1 RDP."""
    if not 0 <= rapid_min_visual_y_share <= 1:
        raise ValueError("rapid_min_visual_y_share must be between 0 and 1")

    rapid = _classify_rapid_moves(
        stage1,
        geometry,
        min_visual_y_share=rapid_min_visual_y_share,
    )
    spikes = _classify_spike_candidates(stage1, rapid)

    # Rapid entry points may come from the full plateau-extrema candidate
    # set rather than the fixed-count RDP subset. Stage 2 adds those selected
    # entries back into the corresponding RDP side.
    added_entries = [
        candidate.start
        for candidate in rapid
    ]
    high_map = {_key(point): point for point in stage1.high_pivots}
    low_map = {_key(point): point for point in stage1.low_pivots}
    for point in added_entries:
        if point.pivot_type == "high":
            high_map[_key(point)] = point
        elif point.pivot_type == "low":
            low_map[_key(point)] = point

    augmented_highs = tuple(sorted(high_map.values(), key=lambda item: item.day))
    augmented_lows = tuple(sorted(low_map.values(), key=lambda item: item.day))

    marker_only_keys = {
        _key(spike.point)
        for spike in spikes
        if spike.marker_only
    }
    line_highs = tuple(
        point for point in augmented_highs
        if _key(point) not in marker_only_keys
    )
    line_lows = tuple(
        point for point in augmented_lows
        if _key(point) not in marker_only_keys
    )

    return Stage2Result(
        high_pivots=augmented_highs,
        low_pivots=augmented_lows,
        spike_peaks=spikes,
        high_sideways_segments=_sideways_pairs(line_highs, "high", geometry),
        low_sideways_segments=_sideways_pairs(line_lows, "low", geometry),
        rapid_move_candidates=rapid,
    )


# Temporary compatibility name while callers migrate to the Stage-2 role.
finalize_sideways_protection = classify_special_structures
