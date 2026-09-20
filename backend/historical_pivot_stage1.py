"""Stage 1: envelope/RDP selection and spike correction/protection.

Only the sealed SpikeAugmentedPivotResult leaves this module. Plateau candidates and
pre-spike RDP internals remain local to this stage.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable, Sequence

from historical_pivot_shared import (
    BasePivotResult,
    ChartGeometry,
    EnvelopePoint,
    PivotPoint,
    PivotPolicy,
    PIVOT_POLICIES,
    SeriesPoint,
    SpikeAugmentedPivotResult,
    SpikePeak,
    SPIKE_ANGLE_THRESHOLD_DEG,
    classify_sideways_reference_line,
    normalize_rows,
    screen_angle_degrees,
)

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
) -> SpikeAugmentedPivotResult:
    """Finish stage 1 by confirming spikes on top of the RDP result.

    RDP selection and spike correction belong to the same stage. This function may
    inspect plateau candidates only to recover a missing NORMAL spike entry before
    stage 1 is finalized.

    Normal spike:
      - keep the spike peak
      - use an existing opposite-side RDP entry when present
      - otherwise recover the best opposite-side plateau candidate as the entry
      - the recovered entry becomes part of the FINAL stage-1 RDP result

    Spike inside an opposite sideways segment:
      - keep ONLY the spike peak
      - do NOT select or add an entry point
      - marker_only=True, so later stages never connect a line to that peak

    After this function returns, stage 1 is sealed. Later stages may use only this
    returned final point set and may not consult base candidates again.
    """
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")

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
        for start, end in zip(opposite, opposite[1:]):
            sideways = classify_sideways_reference_line(
                start,
                end,
                prior_trend,
                geometry,
            )
            if sideways is not None and start.day < pivot.day < end.day:
                return True
        return False

    for index in range(1, len(high_rdp) - 1):
        left, pivot, right = high_rdp[index - 1], high_rdp[index], high_rdp[index + 1]
        if not (geometry.display_start <= pivot.day <= geometry.display_end):
            continue
        if not (pivot.value > left.value and pivot.value > right.value):
            continue
        angle = screen_angle_degrees(left, pivot, right, geometry)
        if angle >= angle_threshold_deg:
            continue
        opposite_inside = [
            item for item in low_rdp
            if left.day <= item.day <= right.day
        ]
        if any(item.value > max(left.value, right.value) for item in opposite_inside):
            continue
        if _first_pivot_after(low_rdp, right.day) is None:
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
        angle = screen_angle_degrees(left, pivot, right, geometry)
        if angle >= angle_threshold_deg:
            continue
        opposite_inside = [
            item for item in high_rdp
            if left.day <= item.day <= right.day
        ]
        if any(item.value < min(left.value, right.value) for item in opposite_inside):
            continue
        if _first_pivot_after(high_rdp, right.day) is None:
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




