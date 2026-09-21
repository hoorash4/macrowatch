"""Stage 2: classify special structures on the finished Stage-1 RDP sets.

Stage 2 owns three classifications:
- rapid rise/fall candidates (provisional protection only)
- spikes (final protection)
- sideways ranges (final protection)

Stage 2 never changes the Stage-1 RDP set.  Rapid-move candidates are handed to
Stage 3 as provisional points; their consolidation/finalization belongs only to
Stage 4.
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
    screen_angle_degrees,
    screen_segment_angle_degrees,
)
from historical_pivot_stage1 import BasePivotResult


SPIKE_ANGLE_THRESHOLD_DEG = 25.0
SPIKE_MIN_VISUAL_Y_SHARE = 0.20
SPIKE_MIN_RETRACEMENT_RATIO = 0.70
SPIKE_MAX_BC_TO_AB_Y_RATIO = 1.50
SPIKE_FOLLOWUP_POINTS = 2

RAPID_MOVE_MIN_VISUAL_Y_SHARE = 0.30


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
    provisional_protected_points: tuple[PivotPoint, ...] = ()

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
    opposite: Sequence[PivotPoint],
    direction: str,
) -> PivotPoint | None:
    """Return the first qualifying opposite point when walking backward from B.

    Up move:   nearest low before B whose value is below A.
    Down move: nearest high before B whose value is above A.

    This entry rule is shared by rapid-move and spike classification.
    """
    eligible = [
        point
        for point in opposite
        if point.day < peak.day
        and (
            point.value < a_point.value
            if direction == "up"
            else point.value > a_point.value
        )
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda item: item.day)


def _followup_rebreaks_peak(
    same_side: Sequence[PivotPoint],
    *,
    peak: PivotPoint,
    c_point: PivotPoint,
    direction: str,
    followup_points: int,
) -> bool:
    after_c = [
        point
        for point in same_side
        if point.day > c_point.day
    ][:followup_points]
    if direction == "up":
        return any(point.value > peak.value for point in after_c)
    return any(point.value < peak.value for point in after_c)


def _spike_shape_passes(
    *,
    a_point: PivotPoint,
    peak: PivotPoint,
    c_point: PivotPoint,
    same_side: Sequence[PivotPoint],
    geometry: ChartGeometry,
    direction: str,
    angle_threshold_deg: float,
    min_visual_y_share: float,
    min_retracement_ratio: float,
    max_bc_to_ab_y_ratio: float,
    followup_points: int,
) -> tuple[bool, float]:
    if not (a_point.day < peak.day < c_point.day):
        return False, 0.0

    if direction == "up":
        ab = float(peak.value) - float(a_point.value)
        bc = float(peak.value) - float(c_point.value)
    else:
        ab = float(a_point.value) - float(peak.value)
        bc = float(c_point.value) - float(peak.value)

    if ab <= 0 or bc <= 0:
        return False, 0.0

    ratio = bc / ab
    if ratio < min_retracement_ratio or ratio > max_bc_to_ab_y_ratio:
        return False, 0.0
    if max(ab, bc) / (geometry.y_max - geometry.y_min) < min_visual_y_share:
        return False, 0.0

    angle = screen_angle_degrees(a_point, peak, c_point, geometry)
    if angle > angle_threshold_deg:
        return False, angle
    if _followup_rebreaks_peak(
        same_side,
        peak=peak,
        c_point=c_point,
        direction=direction,
        followup_points=followup_points,
    ):
        return False, angle
    return True, angle


def _classify_spikes(
    stage1: BasePivotResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float,
    min_visual_y_share: float,
    min_retracement_ratio: float,
    max_bc_to_ab_y_ratio: float,
    followup_points: int,
) -> tuple[SpikePeak, ...]:
    high_rdp = tuple(sorted(stage1.high_pivots, key=lambda item: item.day))
    low_rdp = tuple(sorted(stage1.low_pivots, key=lambda item: item.day))
    spikes: list[SpikePeak] = []

    def opposite_sideways_contains_spike(
        peak: PivotPoint,
        direction: str,
    ) -> bool:
        opposite = low_rdp if direction == "up" else high_rdp
        prior_trend = "down" if direction == "up" else "up"
        for left, right in zip(opposite, opposite[1:]):
            sideways = classify_sideways_reference_line(
                left,
                right,
                prior_trend,
                geometry,
            )
            if sideways is not None and left.day < peak.day < right.day:
                return True
        return False

    for index in range(1, len(high_rdp) - 1):
        a_point, peak, c_point = (
            high_rdp[index - 1],
            high_rdp[index],
            high_rdp[index + 1],
        )
        if not (geometry.display_start <= peak.day <= geometry.display_end):
            continue
        if not (peak.value > a_point.value and peak.value > c_point.value):
            continue

        opposite_inside = [
            item for item in low_rdp
            if a_point.day <= item.day <= c_point.day
        ]
        # A real spike cannot have the opposite boundary following the peak
        # above both adjacent same-side points.
        if any(
            item.value > max(a_point.value, c_point.value)
            for item in opposite_inside
        ):
            continue

        passes, angle = _spike_shape_passes(
            a_point=a_point,
            peak=peak,
            c_point=c_point,
            same_side=high_rdp,
            geometry=geometry,
            direction="up",
            angle_threshold_deg=angle_threshold_deg,
            min_visual_y_share=min_visual_y_share,
            min_retracement_ratio=min_retracement_ratio,
            max_bc_to_ab_y_ratio=max_bc_to_ab_y_ratio,
            followup_points=followup_points,
        )
        if not passes:
            continue

        marker_only = opposite_sideways_contains_spike(peak, "up")
        entry = None if marker_only else _entry_before_peak(
            a_point=a_point,
            peak=peak,
            opposite=low_rdp,
            direction="up",
        )
        if not marker_only and entry is None:
            continue

        spikes.append(
            SpikePeak(
                point=peak,
                direction="up",
                angle_deg=angle,
                entry=entry,
                marker_only=marker_only,
            )
        )

    for index in range(1, len(low_rdp) - 1):
        a_point, peak, c_point = (
            low_rdp[index - 1],
            low_rdp[index],
            low_rdp[index + 1],
        )
        if not (geometry.display_start <= peak.day <= geometry.display_end):
            continue
        if not (peak.value < a_point.value and peak.value < c_point.value):
            continue

        opposite_inside = [
            item for item in high_rdp
            if a_point.day <= item.day <= c_point.day
        ]
        if any(
            item.value < min(a_point.value, c_point.value)
            for item in opposite_inside
        ):
            continue

        passes, angle = _spike_shape_passes(
            a_point=a_point,
            peak=peak,
            c_point=c_point,
            same_side=low_rdp,
            geometry=geometry,
            direction="down",
            angle_threshold_deg=angle_threshold_deg,
            min_visual_y_share=min_visual_y_share,
            min_retracement_ratio=min_retracement_ratio,
            max_bc_to_ab_y_ratio=max_bc_to_ab_y_ratio,
            followup_points=followup_points,
        )
        if not passes:
            continue

        marker_only = opposite_sideways_contains_spike(peak, "down")
        entry = None if marker_only else _entry_before_peak(
            a_point=a_point,
            peak=peak,
            opposite=high_rdp,
            direction="down",
        )
        if not marker_only and entry is None:
            continue

        spikes.append(
            SpikePeak(
                point=peak,
                direction="down",
                angle_deg=angle,
                entry=entry,
                marker_only=marker_only,
            )
        )

    return tuple(sorted(spikes, key=lambda item: item.point.day))


def _classify_rapid_moves(
    stage1: BasePivotResult,
    geometry: ChartGeometry,
    *,
    min_visual_y_share: float,
    spike_peaks: Sequence[SpikePeak],
) -> tuple[RapidMoveCandidate, ...]:
    """Create provisional rapid-move candidates from the separate RDP sides."""
    high_rdp = tuple(sorted(stage1.high_pivots, key=lambda item: item.day))
    low_rdp = tuple(sorted(stage1.low_pivots, key=lambda item: item.day))
    spike_peak_keys = {_key(spike.point) for spike in spike_peaks}
    found: dict[tuple, RapidMoveCandidate] = {}

    for a_point, peak in zip(high_rdp, high_rdp[1:]):
        if peak.value <= a_point.value:
            continue
        if _key(peak) in spike_peak_keys:
            continue
        entry = _entry_before_peak(
            a_point=a_point,
            peak=peak,
            opposite=low_rdp,
            direction="up",
        )
        if entry is None:
            continue
        share = _visual_y_share(entry, peak, geometry)
        if share < min_visual_y_share:
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
            opposite=high_rdp,
            direction="down",
        )
        if entry is None:
            continue
        share = _visual_y_share(entry, peak, geometry)
        if share < min_visual_y_share:
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
    """Classify/merge sideways runs using only the approved 6-degree rule.

    Sideways classification itself is direction-agnostic.  A contiguous run
    remains one sideways segment while the direct line from the run start to the
    new endpoint stays within 6 degrees.

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

        if abs(pair_angle) > 6.0:
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
            if abs(merged_angle) <= 6.0:
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
    spike_angle_threshold_deg: float = SPIKE_ANGLE_THRESHOLD_DEG,
    spike_min_visual_y_share: float = SPIKE_MIN_VISUAL_Y_SHARE,
    spike_min_retracement_ratio: float = SPIKE_MIN_RETRACEMENT_RATIO,
    spike_max_bc_to_ab_y_ratio: float = SPIKE_MAX_BC_TO_AB_Y_RATIO,
    spike_followup_points: int = SPIKE_FOLLOWUP_POINTS,
    rapid_min_visual_y_share: float = RAPID_MOVE_MIN_VISUAL_Y_SHARE,
) -> Stage2Result:
    """Classify all Stage-2 special structures without changing Stage-1 RDP."""
    if spike_angle_threshold_deg <= 0 or spike_angle_threshold_deg >= 180:
        raise ValueError("spike_angle_threshold_deg must be between 0 and 180")
    if not 0 <= spike_min_visual_y_share <= 1:
        raise ValueError("spike_min_visual_y_share must be between 0 and 1")
    if spike_min_retracement_ratio < 0:
        raise ValueError("spike_min_retracement_ratio must be non-negative")
    if spike_max_bc_to_ab_y_ratio < spike_min_retracement_ratio:
        raise ValueError(
            "spike_max_bc_to_ab_y_ratio must be >= spike_min_retracement_ratio"
        )
    if spike_followup_points < 0:
        raise ValueError("spike_followup_points must be non-negative")
    if not 0 <= rapid_min_visual_y_share <= 1:
        raise ValueError("rapid_min_visual_y_share must be between 0 and 1")

    spikes = _classify_spikes(
        stage1,
        geometry,
        angle_threshold_deg=spike_angle_threshold_deg,
        min_visual_y_share=spike_min_visual_y_share,
        min_retracement_ratio=spike_min_retracement_ratio,
        max_bc_to_ab_y_ratio=spike_max_bc_to_ab_y_ratio,
        followup_points=spike_followup_points,
    )
    marker_only_keys = {
        _key(spike.point)
        for spike in spikes
        if spike.marker_only
    }
    line_highs = tuple(
        point for point in stage1.high_pivots
        if _key(point) not in marker_only_keys
    )
    line_lows = tuple(
        point for point in stage1.low_pivots
        if _key(point) not in marker_only_keys
    )

    rapid = _classify_rapid_moves(
        stage1,
        geometry,
        min_visual_y_share=rapid_min_visual_y_share,
        spike_peaks=spikes,
    )
    provisional_map = {
        _key(point): point
        for candidate in rapid
        for point in candidate.protected_points
    }

    return Stage2Result(
        high_pivots=tuple(stage1.high_pivots),
        low_pivots=tuple(stage1.low_pivots),
        spike_peaks=spikes,
        high_sideways_segments=_sideways_pairs(line_highs, "high", geometry),
        low_sideways_segments=_sideways_pairs(line_lows, "low", geometry),
        rapid_move_candidates=rapid,
        provisional_protected_points=tuple(sorted(
            provisional_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
    )


# Temporary compatibility name while callers migrate to the Stage-2 role.
finalize_sideways_protection = classify_special_structures
