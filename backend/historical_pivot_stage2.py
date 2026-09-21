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
    prior_trend: str,
    geometry: ChartGeometry,
) -> tuple[SidewaysSegment, ...]:
    """Classify and merge same-side sideways runs at the approved 6 degrees.

    A run may start only after the matching same-side trend:
    - uptrend   -> rising highs, then a flat high/high pair
    - downtrend -> falling lows, then a flat low/low pair

    Once established, adjacent flat pairs belong to the same sideways structure
    while the direct line from the run start to the new end also remains within
    6 degrees.  If that direct line leaves the sideways band, the old run is
    sealed and the current flat pair starts a new run.
    """
    found: list[SidewaysSegment] = []
    ordered = tuple(sorted(points, key=lambda item: item.day))
    active: SidewaysSegment | None = None

    for index, (left, right) in enumerate(zip(ordered, ordered[1:])):
        pair = classify_sideways_reference_line(
            left,
            right,
            prior_trend,
            geometry,
        )
        if pair is None:
            if active is not None:
                found.append(active)
                active = None
            continue

        if active is not None and active.end == left:
            merged = classify_sideways_reference_line(
                active.start,
                right,
                prior_trend,
                geometry,
            )
            if merged is not None:
                active = merged
            else:
                found.append(active)
                active = pair
            continue

        if active is not None:
            found.append(active)
            active = None

        if index == 0:
            continue

        previous = ordered[index - 1]
        arrived_from_trend = (
            left.value > previous.value
            if prior_trend == "up"
            else left.value < previous.value
        )
        if arrived_from_trend:
            active = pair

    if active is not None:
        found.append(active)

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
        high_sideways_segments=_sideways_pairs(line_highs, "up", geometry),
        low_sideways_segments=_sideways_pairs(line_lows, "down", geometry),
        rapid_move_candidates=rapid,
        provisional_protected_points=tuple(sorted(
            provisional_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
    )


# Temporary compatibility name while callers migrate to the Stage-2 role.
finalize_sideways_protection = classify_special_structures
