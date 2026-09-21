"""Stage 1: envelope/RDP selection and spike correction/protection.

Only the sealed SpikeAugmentedPivotResult leaves this module. Plateau candidates and
pre-spike RDP internals remain local to this stage.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from historical_pivot_shared import (
    ChartGeometry,
    PivotPoint,
    SeriesPoint,
    SpikePeak,
    classify_sideways_reference_line,
    normalize_rows,
    screen_angle_degrees,
)

SPIKE_ANGLE_THRESHOLD_DEG = 25.0
SPIKE_MIN_VISUAL_Y_SHARE = 0.20
SPIKE_MIN_RETRACEMENT_RATIO = 0.70
SPIKE_MAX_BC_TO_AB_Y_RATIO = 1.50
SPIKE_FOLLOWUP_POINTS = 2

@dataclass(frozen=True)
class PivotPolicy:
    envelope_points: int
    rdp_points: int
    envelope_calendar_radius_days: int | None = None

PIVOT_POLICIES = {
    "D": PivotPolicy(envelope_points=35, rdp_points=14, envelope_calendar_radius_days=17),
    "W": PivotPolicy(envelope_points=5, rdp_points=14),
    "M": PivotPolicy(envelope_points=3, rdp_points=10),
}

@dataclass(frozen=True)
class EnvelopePoint:
    day: date
    value: float
    upper: float
    lower: float

@dataclass(frozen=True)
class BasePivotResult:
    frequency: str
    policy: PivotPolicy
    high_candidates: tuple[PivotPoint, ...]
    low_candidates: tuple[PivotPoint, ...]
    high_pivots: tuple[PivotPoint, ...]
    low_pivots: tuple[PivotPoint, ...]

@dataclass(frozen=True)
class SpikeAugmentedPivotResult:
    """The only Stage 1 result allowed to leave this module."""
    high_pivots: tuple[PivotPoint, ...]
    low_pivots: tuple[PivotPoint, ...]
    spike_peaks: tuple[SpikePeak, ...] = ()

    def __post_init__(self) -> None:
        final_keys = {
            (item.day, item.value, item.pivot_type)
            for item in (*self.high_pivots, *self.low_pivots)
        }
        for spike in self.spike_peaks:
            if (spike.point.day, spike.point.value, spike.point.pivot_type) not in final_keys:
                raise ValueError("stage-1 spike metadata references a non-final peak")
            if spike.entry is not None and (
                spike.entry.day, spike.entry.value, spike.entry.pivot_type
            ) not in final_keys:
                raise ValueError("stage-1 spike metadata references a non-final entry")

    @property
    def display_markers(self) -> tuple[PivotPoint, ...]:
        return tuple(sorted(
            (*self.high_pivots, *self.low_pivots),
            key=lambda item: (item.day, item.pivot_type),
        ))

def build_envelope(points: Sequence[SeriesPoint], frequency: str) -> tuple[EnvelopePoint, ...]:
    policy = PIVOT_POLICIES.get(frequency)
    if policy is None:
        raise ValueError(f"unsupported pivot frequency: {frequency}")
    result: list[EnvelopePoint] = []

    if policy.envelope_calendar_radius_days is not None:
        radius_days = policy.envelope_calendar_radius_days
        for point in points:
            window = [
                item for item in points
                if abs((item.day - point.day).days) <= radius_days
            ]
            values = [item.value for item in window]
            result.append(EnvelopePoint(point.day, point.value, max(values), min(values)))
        return tuple(result)

    radius = policy.envelope_points // 2
    for index, point in enumerate(points):
        window = points[max(0, index - radius):min(len(points), index + radius + 1)]
        values = [item.value for item in window]
        result.append(EnvelopePoint(point.day, point.value, max(values), min(values)))
    return tuple(result)



def plateau_extrema(
    envelope: Sequence[EnvelopePoint],
    pivot_type: str,
) -> tuple[PivotPoint, ...]:
    if pivot_type not in {"high", "low"}:
        raise ValueError("pivot_type must be high or low")
    attribute = "upper" if pivot_type == "high" else "lower"
    candidates: list[PivotPoint] = []
    run_start = 0
    while run_start < len(envelope):
        level = getattr(envelope[run_start], attribute)
        run_end = run_start + 1
        while run_end < len(envelope) and getattr(envelope[run_end], attribute) == level:
            run_end += 1
        run = envelope[run_start:run_end]
        if len(run) >= 2:
            chosen = (
                max(run, key=lambda item: item.value)
                if pivot_type == "high"
                else min(run, key=lambda item: item.value)
            )
            candidates.append(PivotPoint(chosen.day, chosen.value, pivot_type))
        run_start = run_end
    unique = {(item.day, item.value): item for item in candidates}
    return tuple(sorted(unique.values(), key=lambda item: item.day))



def _perpendicular_distance(
    point: PivotPoint,
    left: PivotPoint,
    right: PivotPoint,
    origin: date,
) -> float:
    px = float((point.day - origin).days)
    py = point.value
    x1 = float((left.day - origin).days)
    y1 = left.value
    x2 = float((right.day - origin).days)
    y2 = right.value
    denominator = math.hypot(y2 - y1, x2 - x1)
    if denominator == 0:
        return math.hypot(px - x1, py - y1)
    return abs((y2 - y1) * px - (x2 - x1) * py + x2 * y1 - y2 * x1) / denominator



def fixed_count_rdp(
    points: Sequence[PivotPoint],
    target_count: int,
) -> tuple[PivotPoint, ...]:
    """Greedy fixed-count RDP-style simplification used in the approved experiments."""
    if target_count < 2:
        raise ValueError("target_count must be at least 2")
    ordered = tuple(sorted(points, key=lambda item: item.day))
    if len(ordered) <= target_count:
        return ordered
    origin = ordered[0].day
    selected = {0, len(ordered) - 1}
    while len(selected) < target_count:
        best_index: int | None = None
        best_distance = -1.0
        indices = sorted(selected)
        for left_index, right_index in zip(indices, indices[1:]):
            for index in range(left_index + 1, right_index):
                distance = _perpendicular_distance(
                    ordered[index], ordered[left_index], ordered[right_index], origin,
                )
                if distance > best_distance:
                    best_index = index
                    best_distance = distance
        if best_index is None:
            break
        selected.add(best_index)
    return tuple(ordered[index] for index in sorted(selected))



def calculate_base_pivots(
    rows: Iterable[dict[str, Any]],
    frequency: str,
) -> BasePivotResult:
    points = normalize_rows(rows)
    policy = PIVOT_POLICIES.get(frequency)
    if policy is None:
        raise ValueError(f"unsupported pivot frequency: {frequency}")
    envelope = build_envelope(points, frequency)
    high_candidates = plateau_extrema(envelope, "high")
    low_candidates = plateau_extrema(envelope, "low")
    return BasePivotResult(
        frequency=frequency,
        policy=policy,
        high_candidates=high_candidates,
        low_candidates=low_candidates,
        high_pivots=fixed_count_rdp(high_candidates, policy.rdp_points),
        low_pivots=fixed_count_rdp(low_candidates, policy.rdp_points),
    )




def _has_pivot_between(
    pivots: Sequence[PivotPoint],
    start: date,
    end: date,
) -> bool:
    return any(start <= item.day <= end for item in pivots)



def _first_pivot_after(
    pivots: Sequence[PivotPoint],
    after: date,
) -> PivotPoint | None:
    return next((item for item in sorted(pivots, key=lambda point: point.day) if item.day > after), None)



def augment_spike_entry_points(
    base: BasePivotResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SPIKE_ANGLE_THRESHOLD_DEG,
    min_visual_y_share: float = SPIKE_MIN_VISUAL_Y_SHARE,
    min_retracement_ratio: float = SPIKE_MIN_RETRACEMENT_RATIO,
    max_bc_to_ab_y_ratio: float = SPIKE_MAX_BC_TO_AB_Y_RATIO,
    followup_points: int = SPIKE_FOLLOWUP_POINTS,
) -> SpikeAugmentedPivotResult:
    """Finish Stage 1 by confirming spike shapes on top of the RDP result.

    A spike candidate is judged from the actual A -> B -> C excursion:
    - B is a local same-side RDP extreme.
    - A, B, C are three consecutive same-side RDP pivots; B is the local
      spike extreme.
    - angle ABC must be <= 25 degrees by default.
    - the larger of AB/BC vertical screen travels must cover >= 20% of the
      visible y-axis by default.
    - BC must retrace >= 70% of AB by default.
    - BC vertical magnitude must be <= 1.5 * AB vertical magnitude by default.
    - after C, inspect only C+1 and C+2 in chronological Stage-1 RDP order.
      If either point exceeds B again in the AB direction, B belongs to the
      continuing move and is not a spike.

    Up/down handling is exactly symmetric.  After this function returns,
    Stage 1 is sealed; later stages never consult discarded candidate sets.
    """
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")
    if min_visual_y_share < 0 or min_visual_y_share > 1:
        raise ValueError("min_visual_y_share must be between 0 and 1")
    if min_retracement_ratio < 0:
        raise ValueError("min_retracement_ratio must be non-negative")
    if max_bc_to_ab_y_ratio < min_retracement_ratio:
        raise ValueError("max_bc_to_ab_y_ratio must be >= min_retracement_ratio")
    if followup_points < 0:
        raise ValueError("followup_points must be non-negative")

    y_span = float(geometry.y_max - geometry.y_min)
    if y_span <= 0:
        raise ValueError("geometry y-axis span must be positive")

    high_rdp = tuple(sorted(base.high_pivots, key=lambda item: item.day))
    low_rdp = tuple(sorted(base.low_pivots, key=lambda item: item.day))
    high_candidates = tuple(sorted(base.high_candidates, key=lambda item: item.day))
    low_candidates = tuple(sorted(base.low_candidates, key=lambda item: item.day))
    added_high: dict[tuple[date, float], PivotPoint] = {}
    added_low: dict[tuple[date, float], PivotPoint] = {}
    spike_peaks: dict[tuple[date, float, str], SpikePeak] = {}

    def opposite_sideways_contains_spike(
        pivot: PivotPoint,
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
            if sideways is not None and left.day < pivot.day < right.day:
                return True
        return False

    def followup_rebreaks_peak(
        pivot: PivotPoint,
        c_point: PivotPoint,
        direction: str,
    ) -> bool:
        same_side = high_rdp if direction == "up" else low_rdp
        after_c = [
            point
            for point in same_side
            if point.day > c_point.day
        ][:followup_points]
        if direction == "up":
            return any(point.value > pivot.value for point in after_c)
        return any(point.value < pivot.value for point in after_c)

    def shape_passes(
        a_point: PivotPoint,
        pivot: PivotPoint,
        c_point: PivotPoint,
        direction: str,
    ) -> tuple[bool, float]:
        if not (a_point.day < pivot.day < c_point.day):
            return False, 0.0

        if direction == "up":
            ab = float(pivot.value) - float(a_point.value)
            bc = float(pivot.value) - float(c_point.value)
        else:
            ab = float(a_point.value) - float(pivot.value)
            bc = float(c_point.value) - float(pivot.value)

        if ab <= 0 or bc <= 0:
            return False, 0.0
        ratio = bc / ab
        if ratio < min_retracement_ratio or ratio > max_bc_to_ab_y_ratio:
            return False, 0.0
        if max(ab, bc) / y_span < min_visual_y_share:
            return False, 0.0

        angle = screen_angle_degrees(a_point, pivot, c_point, geometry)
        if angle > angle_threshold_deg:
            return False, angle
        if followup_rebreaks_peak(pivot, c_point, direction):
            return False, angle
        return True, angle

    for index in range(1, len(high_rdp) - 1):
        left, pivot, right = high_rdp[index - 1], high_rdp[index], high_rdp[index + 1]
        if not (geometry.display_start <= pivot.day <= geometry.display_end):
            continue
        if not (pivot.value > left.value and pivot.value > right.value):
            continue

        passes, angle = shape_passes(left, pivot, right, "up")
        if not passes:
            continue

        marker_only = opposite_sideways_contains_spike(pivot, "up")
        if marker_only:
            entry = None
        else:
            existing_entries = [
                item for item in low_rdp
                if left.day < item.day < pivot.day
            ]
            if existing_entries:
                entry = min(existing_entries, key=lambda item: item.value)
            else:
                entry_candidates = [
                    item for item in low_candidates
                    if left.day < item.day < pivot.day
                ]
                if not entry_candidates:
                    continue
                entry = min(entry_candidates, key=lambda item: item.value)
                added_low[(entry.day, entry.value)] = entry

        spike_peaks[(pivot.day, pivot.value, "up")] = SpikePeak(
            point=pivot,
            direction="up",
            angle_deg=angle,
            entry=entry,
            marker_only=marker_only,
        )

    for index in range(1, len(low_rdp) - 1):
        left, pivot, right = low_rdp[index - 1], low_rdp[index], low_rdp[index + 1]
        if not (geometry.display_start <= pivot.day <= geometry.display_end):
            continue
        if not (pivot.value < left.value and pivot.value < right.value):
            continue

        passes, angle = shape_passes(left, pivot, right, "down")
        if not passes:
            continue

        marker_only = opposite_sideways_contains_spike(pivot, "down")
        if marker_only:
            entry = None
        else:
            existing_entries = [
                item for item in high_rdp
                if left.day < item.day < pivot.day
            ]
            if existing_entries:
                entry = max(existing_entries, key=lambda item: item.value)
            else:
                entry_candidates = [
                    item for item in high_candidates
                    if left.day < item.day < pivot.day
                ]
                if not entry_candidates:
                    continue
                entry = max(entry_candidates, key=lambda item: item.value)
                added_high[(entry.day, entry.value)] = entry

        spike_peaks[(pivot.day, pivot.value, "down")] = SpikePeak(
            point=pivot,
            direction="down",
            angle_deg=angle,
            entry=entry,
            marker_only=marker_only,
        )

    # Seal stage 1. Candidate sets and the pre-correction BasePivotResult are
    # deliberately not carried across this boundary.
    final_highs = {
        (item.day, item.value, item.pivot_type): item
        for item in (*high_rdp, *added_high.values())
    }
    final_lows = {
        (item.day, item.value, item.pivot_type): item
        for item in (*low_rdp, *added_low.values())
    }
    return SpikeAugmentedPivotResult(
        high_pivots=tuple(sorted(final_highs.values(), key=lambda item: item.day)),
        low_pivots=tuple(sorted(final_lows.values(), key=lambda item: item.day)),
        spike_peaks=tuple(sorted(spike_peaks.values(), key=lambda item: item.point.day)),
    )




